// ROM-free transport regression tests. This links the actual candidate
// WiiDSBridge.cpp, rather than a model or copy of its socket implementation.
#include "net/WiiDSBridge.h"
#include "net/WiiDSMPConfig.h"
#include "Platform.h"
#include <arpa/inet.h>
#include <cerrno>
#include <csignal>
#include <cstdlib>
#include <fcntl.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <sys/wait.h>
#include <unistd.h>
#include <algorithm>
#include <array>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace { melonDS::u64 NowMS = 1; }
namespace melonDS::Platform
{
u64 GetMSCount() { return NowMS; }
u64 GetUSCount() { return NowMS * 1000 + 1; }
}
namespace
{
using namespace melonDS;
namespace Bridge = melonDS::WiiDSBridge;
constexpr std::array<u8,6> WiiMAC{0,0x17,0xab,0x4d,0xb9,0xe0};
constexpr char Hello[] = "WIIDSBRIDGE/1 DOLPHIN HELLO";
constexpr unsigned PlayerCount = 8;
constexpr u16 PlayerMask = 0x01fe;
std::array<int,PlayerCount> Tokens{};
const auto Owners = [] {
    std::array<void*,PlayerCount> result{};
    for (unsigned i=0;i<PlayerCount;++i) result[i]=&Tokens[i];
    return result;
}();
int CrossProcessToken;
unsigned Cases = 0;

void Require(bool value, const char* message)
{
    if (!value) throw std::runtime_error(message);
}
void Pass(const char* name) { ++Cases; std::printf("PASS %s\n",name); }
void Pump()
{
    for (unsigned turn=0; turn<3; ++turn)
    {
        for (void* owner : Owners) Bridge::Process(owner);
        Bridge::Process(&CrossProcessToken);
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
    }
}
struct Datagram
{
    unsigned Port = 0;
    std::vector<u8> Bytes;
};
struct UDP
{
    int Socket = -1;
    explicit UDP(unsigned port = 0)
    {
        Socket = socket(AF_INET,SOCK_DGRAM,IPPROTO_UDP);
        Require(Socket>=0,"fixture socket creation");
        // No SO_REUSEADDR/SO_REUSEPORT: each radio port must have one owner.
        sockaddr_in local{};
#ifdef __APPLE__
        local.sin_len=sizeof(local);
#endif
        local.sin_family=AF_INET;
        local.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
        local.sin_port=htons(static_cast<u_short>(port));
        if (bind(Socket,reinterpret_cast<const sockaddr*>(&local),sizeof(local)))
        {
            close(Socket); Socket=-1;
            throw std::runtime_error("fixture bind failed (close emulators manually)");
        }
        const timeval timeout{0,60000};
        Require(setsockopt(Socket,SOL_SOCKET,SO_RCVTIMEO,&timeout,sizeof(timeout))==0,
                "fixture receive timeout");
    }
    ~UDP() { if (Socket>=0) close(Socket); }
    void Send(unsigned port,const std::vector<u8>& bytes)
    {
        sockaddr_in target{};
#ifdef __APPLE__
        target.sin_len=sizeof(target);
#endif
        target.sin_family=AF_INET;
        target.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
        target.sin_port=htons(static_cast<u_short>(port));
        Require(sendto(Socket,reinterpret_cast<const char*>(bytes.data()),
                static_cast<int>(bytes.size()),0,
                reinterpret_cast<const sockaddr*>(&target),sizeof(target))==
                static_cast<int>(bytes.size()),"fixture datagram send");
    }
    Datagram Receive()
    {
        std::array<u8,4096> bytes{};
        sockaddr_in sender{}; socklen_t size=sizeof(sender);
        const auto length=recvfrom(Socket,reinterpret_cast<char*>(bytes.data()),
                static_cast<int>(bytes.size()),0,reinterpret_cast<sockaddr*>(&sender),&size);
        if (length<0)
        {
            const auto error=errno;
            if (error==EAGAIN || error==EWOULDBLOCK || error==EINTR || error==ECONNREFUSED) return {};
            throw std::runtime_error("fixture receive error");
        }
        Require(sender.sin_addr.s_addr==htonl(INADDR_LOOPBACK),"non-loopback fixture response");
        return {ntohs(sender.sin_port),{bytes.begin(),bytes.begin()+length}};
    }
};
bool PortAvailable(unsigned port)
{
    try { UDP probe(port); return true; } catch (...) { return false; }
}
std::vector<u8> Packet(u8 type,const std::vector<u8>& body={})
{
    Require(body.size()<=0xffff,"fixture packet size");
    std::vector<u8> packet{'W','D','S','B','0','0','0','1',type,u8(body.size()>>8),u8(body.size())};
    packet.insert(packet.end(),body.begin(),body.end());
    return packet;
}
void Send(UDP& peer,unsigned index,u8 type,const std::vector<u8>& body={})
{
    peer.Send(7065+index,Packet(type,body)); Pump();
}
std::string Discover(UDP& peer,unsigned port)
{
    peer.Send(port,{Hello,Hello+sizeof(Hello)-1}); Pump();
    const auto reply=peer.Receive();
    Require(reply.Port==port,"ACK returned from wrong listener");
    const std::string ack(reply.Bytes.begin(),reply.Bytes.end());
    const std::string prefix="WIIDSBRIDGE/1 MELONDS ACK ";
    Require(ack.size()==prefix.size()+16+3 && ack.compare(0,prefix.size(),prefix)==0 &&
            ack.substr(ack.size()-3)==" M2","missing multi-client ACK capability");
    Require(ack.substr(prefix.size(),16).find_first_not_of("0123456789ABCDEF")==std::string::npos,
            "ACK nonce is not 16 uppercase hexadecimal digits");
    return ack;
}
std::vector<u8> Receive(unsigned index,bool host=false)
{
    std::array<u8,4096> bytes{}; u64 timestamp{};
    const auto length=Bridge::Receive(Owners[index],bytes.data(),&timestamp,host);
    Require(length>=0 && length<=static_cast<int>(bytes.size()),"invalid guest receive size");
    return {bytes.begin(),bytes.begin()+length};
}
unsigned LE16(const std::vector<u8>& bytes,size_t offset)
{
    Require(bytes.size()>=offset+2,"short radio field");
    return bytes[offset] | (unsigned(bytes[offset+1])<<8);
}
void AppendBE32(std::vector<u8>& bytes,u32 value)
{
    for (int shift : {24,16,8,0}) bytes.push_back(u8(value>>shift));
}
std::vector<u8> Beacon(unsigned index)
{
    std::vector<u8> beacon(130,0);
    beacon[1]=1;
    const std::array<u8,16> identity{1,0,1,8,0x86,2,0x40,0,0x9d,0xd4,0x70,3,0xf0,1,8,0};
    std::copy(identity.begin(),identity.end(),beacon.begin()+2);
    for (unsigned i=120;i<130;++i) beacon[i]=u8(index*13+i);
    return beacon;
}
std::vector<u8> Management(unsigned index)
{
    std::vector<u8> frame(46,0);
    frame[8]=0x0a; frame[9]=7; frame[10]=34; frame[12]=0xb0;
    std::array<u8,6> client{0,9,0xbf,0x4c,0x68,u8(0x29+index)};
    std::copy(client.begin(),client.end(),frame.begin()+16);
    std::copy(WiiMAC.begin(),WiiMAC.end(),frame.begin()+22);
    std::copy(WiiMAC.begin(),WiiMAC.end(),frame.begin()+28);
    frame[38]=2; frame[40]=u8(index);
    return frame;
}
std::vector<u8> ClientTX(unsigned index,bool reply)
{
    std::vector<u8> frame(reply?40:44,0);
    frame[8]=reply?0x14:0x0a; frame[9]=7; frame[10]=u8(frame.size()-12);
    frame[12]=reply?0x18:0; frame[13]=reply?1:0;
    std::array<u8,6> client{0,9,0xbf,0x4c,0x68,u8(0x29+index)};
    std::copy(WiiMAC.begin(),WiiMAC.end(),frame.begin()+16);
    std::copy(client.begin(),client.end(),frame.begin()+22);
    const std::array<u8,6> destination=reply?std::array<u8,6>{3,9,0xbf,0,0,0x10}:WiiMAC;
    std::copy(destination.begin(),destination.end(),frame.begin()+28);
    return frame;
}
std::vector<u8> Round(u32 id,u16 mask=PlayerMask)
{
    std::vector<u8> body;
    AppendBE32(body,id);
    body.resize(20,0);
    body[10]=0; body[11]=12;
    body[12]=u8(mask>>8); body[13]=u8(mask);
    body[14]=4; body[15]=0xb6;
    body.insert(body.end(),{0x77,0x22,0x11,0x90,0x41,0x39});
    return body;
}
std::vector<u8> Ack(u32 id,u16 failure)
{
    std::vector<u8> body;
    AppendBE32(body,id); body.push_back(u8(failure>>8)); body.push_back(u8(failure));
    return body;
}
void RequireHostCommand(unsigned index,unsigned duration=2410)
{
    const auto command=Receive(index,true);
    Require(command.size()==50 && command[12]==0x28 && command[13]==2,
            "type9 did not deliver native MP command");
    Require(LE16(command,14)==duration && LE16(command,36)==262 && LE16(command,38)==PlayerMask &&
            command[40]==0x22 && command[41]==0x77,
            "multi-client timing/mask/first-halfword framing changed");
}
int HoldPort(int stop);
struct Child
{
    int Stop=-1;
    pid_t Process=-1;
    Child()
    {
        int pipefd[2]{};
        Require(pipe(pipefd)==0,"fixture child stop pipe");
        Process=fork();
        if (Process==0)
        {
            close(pipefd[1]);
            const int result=HoldPort(pipefd[0]);
            close(pipefd[0]);
            std::_Exit(result);
        }
        close(pipefd[0]);
        if (Process<0)
        { close(pipefd[1]); throw std::runtime_error("fixture child process creation"); }
        Stop=pipefd[1];
    }
    ~Child()
    {
        // Only our private pipe and forked child are eligible for cleanup.
        if (Stop>=0) close(Stop);
        if (Process>0)
        {
            const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(3);
            int status=0;
            while (waitpid(Process,&status,WNOHANG)==0)
            {
                if (std::chrono::steady_clock::now()>=deadline)
                {
                    kill(Process,SIGTERM);
                    while (waitpid(Process,&status,0)<0 && errno==EINTR) {}
                    break;
                }
                std::this_thread::sleep_for(std::chrono::milliseconds(5));
            }
        }
    }
};
int HoldPort(int stop)
{
    if (fcntl(stop,F_SETFL,fcntl(stop,F_GETFL,0)|O_NONBLOCK)<0) return 3;
    if (!Bridge::Configure(&CrossProcessToken,true,false)) return 3;
    for (;;)
    {
        char value{};
        const auto result=read(stop,&value,1);
        if (result==0 || result>0) break;
        if (errno!=EAGAIN && errno!=EWOULDBLOCK && errno!=EINTR) break;
        Bridge::Process(&CrossProcessToken); NowMS+=5;
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
    Bridge::Release(&CrossProcessToken);
    return 0;
}
void CrossProcessTest()
{
    Child child;
    for (unsigned i=0;i<400 && PortAvailable(7065);++i)
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    Require(!PortAvailable(7065),"second process did not bind production listener");
    Require(Bridge::Configure(&CrossProcessToken,true,false),"parent cross-process configuration");
    Pump();
    Require(!PortAvailable(7066),"parent process did not select next exclusive port");
    Require(Bridge::IdentitySlot(&CrossProcessToken)==1,
            "parent did not expose its independent process radio slot");
    UDP peer;
    const auto childAck=Discover(peer,7065);
    const auto parentAck=Discover(peer,7066);
    Require(childAck!=parentAck,"separate processes reused endpoint identity");
    Bridge::Release(&CrossProcessToken);
    Require(PortAvailable(7066) && !PortAvailable(7065),"parent release disrupted child listener");
    Pass("independent-processes-select-distinct-exclusive-ports");
}
}
int main()
{
    try
    {
        for (unsigned port=7065;port<=7080;++port)
            Require(PortAvailable(port),"bridge port range occupied; close emulators manually and retry");
        CrossProcessTest();
        Require(PortAvailable(7065),"child listener cleanup failed");
        NowMS+=1001;

        UDP peer,foreign;
        for (void* owner:Owners) Require(Bridge::Configure(owner,true,false),"independent context refused");
        Pump();
        std::array<std::string,PlayerCount> identities{};
        for (unsigned i=0;i<PlayerCount;++i)
        {
            Require(!PortAvailable(7065+i),"configured instance did not bind distinct port");
            Require(Bridge::IdentitySlot(Owners[i])==i,"context radio identity slot mismatch");
            identities[i]=Discover(peer,7065+i);
            Require(Bridge::IsActive(Owners[i]),"context did not activate after HELLO");
            for (unsigned previous=0;previous<i;++previous)
                Require(identities[i]!=identities[previous],"two contexts reused nonce");
        }
        Pass("eight-contexts-have-independent-ports-and-ack-identities");
        for (unsigned i=0;i<PlayerCount;++i)
        {
            const auto generation=Bridge::Generation(Owners[i]);
            Require(Discover(peer,7065+i)==identities[i] && Bridge::Generation(Owners[i])==generation,
                    "repeated HELLO reset a context");
        }
        Pass("rediscovery-preserves-each-context-session");

        for (unsigned i=0;i<PlayerCount;++i)
        {
            const auto body=Beacon(i);
            Send(peer,i,2,body);
            for (unsigned copy=0;copy<3;++copy)
            {
                const auto beacon=Receive(i);
                Require(beacon.size()>200 && beacon[12]==0x80 &&
                        std::search(beacon.begin(),beacon.end(),body.begin()+2,body.end())!=beacon.end(),
                        "beacon payload crossed context boundary");
                const auto notice=peer.Receive();
                Require(notice.Port==7065+i && notice.Bytes==Packet(6),"beacon consumption came from wrong instance");
            }
            for (unsigned other=0;other<PlayerCount;++other)
                if (other!=i) Require(Receive(other).empty(),"beacon leaked to another context");
        }
        Pass("endpoint-beacons-and-consumption-notifications-stay-isolated");

        for (unsigned i=0;i<PlayerCount;++i)
        {
            const auto management=Management(i);
            Send(peer,i,5,management);
            const auto delivered=Receive(i);
            Require(delivered.size()==management.size() && delivered[12]==0xb0 &&
                    delivered[40]==i,"management response routed to wrong child");
            for (unsigned other=0;other<PlayerCount;++other)
                if (other!=i) Require(Receive(other).empty(),"management frame leaked to another context");
            const auto association=ClientTX(i,false);
            Require(Bridge::Send(Owners[i],association.data(),int(association.size()))==int(association.size()),
                    "association TX failed");
            const auto sent=peer.Receive();
            Require(sent.Port==7065+i && sent.Bytes==Packet(3,association),"association frame source mismatch");
            Bridge::NotifyAssociationResponse(Owners[i],u16(i+1),7);
            Bridge::NotifyAID(Owners[i],u16(i+1),7);
        }
        Pass("eight-real-associations-retain-distinct-client-macs-and-aids");

        for (unsigned i=0;i<PlayerCount;++i)
        {
            foreign.Send(7065+i,{Hello,Hello+sizeof(Hello)-1});
            foreign.Send(7065+i,Packet(8));
            foreign.Send(7065+i,Packet(9,Round(55)));
        }
        Pump();
        Require(foreign.Receive().Bytes.empty(),"foreign Dolphin received takeover ACK");
        for (unsigned i=0;i<PlayerCount;++i)
            Require(Bridge::IsActive(Owners[i]) && Receive(i,true).empty(),"foreign peer changed context");
        Pass("foreign-dolphin-cannot-hijack-any-instance-or-inject-rounds");

        std::array<u8,16> raw{}; raw[7]=12; raw[8]=u8(PlayerMask>>8); raw[9]=u8(PlayerMask);
        const auto decoded=DecodeWiiDSMPConfig(raw);
        Require(decoded && decoded->ClientMask==PlayerMask && decoded->ReplyTime==262 && decoded->Duration==2410,
                "MP decoder does not account for all eight reply slots");
        raw[8]=0x80; raw[9]=0;
        Require(DecodeWiiDSMPConfig(raw).has_value(),"AID15 incorrectly rejected");
        raw[9]=1;
        Require(!DecodeWiiDSMPConfig(raw),"parent AID0 incorrectly admitted as a child");
        Pass("mp-config-accepts-child-aids-one-through-fifteen-and-counts-slots");

        constexpr u32 round=0x1234abcd;
        for (unsigned i=0;i<PlayerCount;++i)
        {
            Send(peer,i,9,Round(round));
            RequireHostCommand(i);
            Require(Receive(i,true).empty(),"artificial ACK appeared before guest reply");
        }
        Pass("one-parent-round-delivers-eight-command-slots-without-guessed-ack");

        for (unsigned i=0;i<PlayerCount;++i)
        {
            const auto reply=ClientTX(i,true);
            Require(Bridge::Send(Owners[i],reply.data(),int(reply.size()))==int(reply.size()),"guest reply send failed");
            const auto sent=peer.Receive();
            std::vector<u8> expected; AppendBE32(expected,round);
            expected.insert(expected.end(),reply.begin(),reply.end());
            Require(sent.Port==7065+i && sent.Bytes==Packet(10,expected),"reply omitted round id or used wrong socket");
            Require(peer.Receive().Bytes.empty(),"guest reply was sent twice");
            Require(Receive(i,true).empty(),"local reply guessed overall parent ACK");
        }
        Pass("actual-replies-carry-round-identity-and-wait-for-parent-result");

        for (unsigned i=0;i<PlayerCount;++i)
        {
            Send(peer,i,11,Ack(round-1,0));
            Require(Receive(i,true).empty(),"stale round ACK advanced guest");
            Send(peer,i,11,Ack(round,4));
            const auto ack=Receive(i,true);
            Require(ack.size()==44 && ack[12]==0x18 && ack[13]==2 && LE16(ack,38)==4,
                    "parent failure mask was not propagated to every guest");
            Require(Receive(i,true).empty(),"duplicate completion frame");
        }
        Pass("round-matched-parent-ack-shares-exact-failure-mask-across-guests");

        const auto survivorGeneration=Bridge::Generation(Owners[0]);
        Bridge::Release(Owners[1]);
        Require(PortAvailable(7066) && !Bridge::IsEnabled(Owners[1]) && Bridge::IsActive(Owners[0]) &&
                Bridge::Generation(Owners[0])==survivorGeneration,"releasing one child changed another session");
        Require(Discover(peer,7065)==identities[0],"surviving context identity changed");
        NowMS+=1;
        Require(Bridge::Configure(Owners[1],true,false),"child reconfiguration failed");
        Pump();
        Require(Discover(peer,7066)!=identities[1],"released/rebound child reused socket identity");
        Require(Receive(1,true).empty(),"rebound child retained old MP round");
        Pass("release-and-rebind-one-child-preserves-other-listeners-and-clears-old-state");

        Send(peer,0,8);
        Require(!Bridge::IsActive(Owners[0]) && Bridge::IsActive(Owners[2]) && Bridge::IsActive(Owners[3]),
                "one child STOP ended other sessions");
        Bridge::Configure(Owners[0],false,false);
        Require(PortAvailable(7065) && !PortAvailable(7067) && !PortAvailable(7068),
                "one child disable released another listener");
        Pass("stop-and-disable-are-per-context");

        for (void* owner:Owners) Bridge::Release(owner);
        for (unsigned port=7065;port<=7080;++port)
            Require(PortAvailable(port),"final context release left a listener bound");
        Pass("final-release-closes-every-owned-listener");
        std::printf("RESULT cases=%u failures=0 scope=production-bridge-eight-contexts-and-two-processes-loopback-only\n",Cases);
        return 0;
    }
    catch (const std::exception& error)
    {
        std::printf("FAIL %s\n",error.what());
        for (void* owner:Owners) Bridge::Release(owner);
        Bridge::Release(&CrossProcessToken);
        return 1;
    }
}
