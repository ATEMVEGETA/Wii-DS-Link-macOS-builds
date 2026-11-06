// Portable GameInfo properties plus native POSIX production-beacon packets.
// Header-only mode permits local Windows compilation without POSIX sockets.
#ifndef WIIDS_BEACON_HEADER_ONLY
#define main ExistingMultiDSFixtureMain
#include "MultiDSFixture.cpp"
#undef main
#endif
#include "net/WiiDSBeacon.h"
#include <algorithm>
#include <array>
#include <cstdio>
#include <stdexcept>
#include <vector>

namespace
{
using melonDS::u8;
unsigned BeaconGroups = 0;
void BeaconRequire(bool condition, const char* message)
{
    if (!condition) throw std::runtime_error(message);
}
void BeaconPass(const char* name)
{
    ++BeaconGroups;
    std::printf("PASS %s\n", name);
}
std::array<u8,128> NativeInfo(unsigned userSize, unsigned version = 1)
{
    std::array<u8,128> data{};
    for (size_t i = 0; i < data.size(); ++i) data[i] = u8(i * 29 + 7);
    const std::array<u8,16> header{1,0,1,8,0x12,0x34,0x56,0,0x47,0x52,0,1,0,2,0,1};
    std::copy(header.begin(), header.end(), data.begin());
    data[0] = u8(version); data[1] = u8(version >> 8); data[0x0a] = u8(userSize);
    return data;
}
bool NativeLengthPredicate(const std::vector<u8>& data)
{
    return data.size() >= 16 && data[0] == 1 && data[1] == 0 && data.size() == 16 + size_t(data[0x0a]);
}
void HeaderProperties()
{
    using melonDS::WiiDSBridge::NintendoGameInfoLength;
    BeaconRequire(NintendoGameInfoLength(NativeInfo(112)) == 128, "Full multiboot record changed");
    BeaconPass("full-multiboot-gameinfo-is-128-bytes");
    BeaconRequire(NintendoGameInfoLength(NativeInfo(0)) == 16, "Zero-user record was padded to storage capacity");
    BeaconPass("zero-user-gameinfo-is-16-bytes");
    for (unsigned size = 0; size <= 112; ++size)
    {
        const auto data = NativeInfo(size);
        const auto length = NintendoGameInfoLength(data);
        BeaconRequire(length == 16 + size && length <= data.size(), "Valid native length is not bounded/exact");
        BeaconRequire(NativeLengthPredicate({data.begin(),data.begin()+length}), "Declared native length predicate failed");
    }
    BeaconPass("all-113-valid-user-sizes-are-exact-and-bounded");
    for (unsigned size = 113; size <= 255; ++size)
        BeaconRequire(NintendoGameInfoLength(NativeInfo(size)) == 128, "Malformed user size lost opaque fallback");
    BeaconPass("all-143-oversized-user-sizes-preserve-opaque-128-fallback");
    for (unsigned version : {0u,2u,0x101u,0xffffu})
        BeaconRequire(NintendoGameInfoLength(NativeInfo(0,version)) == 128, "Unknown version was reinterpreted");
    BeaconPass("unknown-versions-preserve-opaque-128-fallback");
    const auto data = NativeInfo(0);
    BeaconRequire(!NativeLengthPredicate({data.begin(),data.end()}) &&
                  NativeLengthPredicate({data.begin(),data.begin()+16}), "Old padded-record negative control did not fail");
    BeaconPass("old-fixed-128-negative-control-fails-zero-user-length-predicate");
}

#ifndef WIIDS_BEACON_HEADER_ONLY
constexpr std::array<u8,2> BeaconStamp{0x23,0x45};
unsigned LastSequence = 0;
bool HaveSequence = false;
std::vector<u8> Advertisement(const std::array<u8,128>& data)
{
    std::vector<u8> body(WiiMAC.begin(), WiiMAC.end());
    body.insert(body.end(), BeaconStamp.begin(), BeaconStamp.end());
    body.insert(body.end(), data.begin(), data.end());
    return body;
}
void CheckWire(UDP& peer, const std::array<u8,128>& data, size_t length, bool knownValid)
{
    for (u8 channel : {u8(1),u8(7),u8(13)})
    {
        const auto radio = Receive(0);
        BeaconRequire(radio.size() > 52 && radio[12] == 0x80 && radio[9] == channel &&
                      LE16(radio,10) == radio.size()-12, "Production beacon length/channel changed");
        BeaconRequire(std::equal(WiiMAC.begin(), WiiMAC.end(), radio.begin()+22) &&
                      std::equal(WiiMAC.begin(), WiiMAC.end(), radio.begin()+28), "Production parent identity changed");
        const auto sequence = LE16(radio,34) >> 4;
        if (HaveSequence) BeaconRequire(sequence == ((LastSequence+1)&0xfff), "Production beacon sequence changed");
        LastSequence = sequence; HaveSequence = true;
        const auto end = radio.size()-4;
        size_t offset = 48;
        bool found = false;
        while (offset < end)
        {
            BeaconRequire(end-offset >= 2 && radio[offset+1] <= end-offset-2, "Truncated production IE");
            const auto size = radio[offset+1];
            const auto* body = radio.data()+offset+2;
            if (radio[offset] == 0xdd && size >= 4 && body[0] == 0 && body[1] == 9 && body[2] == 0xbf && body[3] == 0)
            {
                BeaconRequire(!found && size == 8+length, "Nintendo vendor IE uses storage padding instead of record length");
                const std::array<u8,8> prefix{0,9,0xbf,0,0xff,0xff,BeaconStamp[0],BeaconStamp[1]};
                BeaconRequire(std::equal(prefix.begin(),prefix.end(),body) &&
                              std::equal(data.begin(),data.begin()+length,body+8), "Nintendo prefix/native bytes changed");
                if (knownValid)
                    BeaconRequire(NativeLengthPredicate({body+8,body+size}), "Actual emitted record fails native length predicate");
                found = true;
            }
            offset += 2+size;
        }
        BeaconRequire(offset == end && found, "Nintendo vendor IE missing");
        const auto consumed = peer.Receive();
        BeaconRequire(consumed.Port == 7065 && consumed.Bytes == Packet(6), "Beacon-consumed envelope changed");
    }
    BeaconRequire(Receive(0).empty(), "Stale cached beacon survived replacement");
}
void WireProperties()
{
    for (unsigned port = 7065; port <= 7080; ++port)
        BeaconRequire(PortAvailable(port), "Loopback radio ports occupied; no emulator will be stopped");
    BeaconRequire(Bridge::Configure(Owners[0],true,false), "Production bridge configuration failed");
    UDP peer;
    Pump();
    Discover(peer,7065);
    const auto generation = Bridge::Generation(Owners[0]);
    for (unsigned size = 0; size <= 255; ++size)
    {
        const auto data = NativeInfo(size);
        Send(peer,0,12,Advertisement(data));
        CheckWire(peer,data,size <= 112 ? 16+size : 128,size <= 112);
    }
    for (unsigned version : {0u,2u,0x101u,0xffffu})
    {
        const auto data = NativeInfo(0,version);
        Send(peer,0,12,Advertisement(data));
        CheckWire(peer,data,128,false);
    }
    BeaconPass("production-posix-beacons-pass-all-length-identity-sequence-prefix-properties");
    Send(peer,0,12,Advertisement(NativeInfo(112)));
    Send(peer,0,12,Advertisement(NativeInfo(0)));
    CheckWire(peer,NativeInfo(0),16,true);
    Send(peer,0,12,Advertisement(NativeInfo(64)));
    CheckWire(peer,NativeInfo(64),80,true);
    BeaconRequire(Bridge::Generation(Owners[0]) == generation && Bridge::IsActive(Owners[0]), "Advertisement change reset session");
    Bridge::Release(Owners[0]);
    BeaconRequire(PortAvailable(7065), "Production listener leaked");
    BeaconPass("production-posix-cache-replacement-preserves-session-across-native-lengths");
}
#endif
}
int main()
{
    try
    {
        HeaderProperties();
#ifndef WIIDS_BEACON_HEADER_ONLY
        WireProperties();
        BeaconRequire(BeaconGroups == 8, "Native beacon group count mismatch");
        std::printf("RESULT beaconGroups=%u failures=0 scope=portable-properties-and-production-posix-beacon-packets\n",BeaconGroups);
#else
        BeaconRequire(BeaconGroups == 6, "Header property group count mismatch");
        std::printf("RESULT beaconGroups=%u failures=0 scope=header-properties-only\n",BeaconGroups);
#endif
        return 0;
    }
    catch (const std::exception& error)
    {
#ifndef WIIDS_BEACON_HEADER_ONLY
        Bridge::Release(Owners[0]);
#endif
        std::printf("FAIL %s\n",error.what());
        return 1;
    }
}
