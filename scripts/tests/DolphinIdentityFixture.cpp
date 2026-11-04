// ROM-free checks of the actual production parent-identity helpers.
// No emulator frontend, sockets, games, save files, or platform SDK is used.
#include "Core/IOS/Network/WD/WiiDSBridgeIdentity.h"

#include <cstdio>
#include <stdexcept>
#include <string>

namespace
{
using namespace IOS::HLE::WD;
constexpr std::array<u8, 6> NaturalMAC{0x00, 0x17, 0xab, 0x57, 0x66, 0xf5};
constexpr std::array<u8, 8> Magic{'W', 'D', 'S', 'B', '0', '0', '0', '1'};
unsigned Groups = 0;

void Require(bool value, const char* message)
{
  if (!value)
    throw std::runtime_error(message);
}

void Pass(const char* name)
{
  ++Groups;
  std::printf("PASS %s\n", name);
}

unsigned BodySize(const std::vector<u8>& packet)
{
  Require(packet.size() >= 11, "short packet header");
  return (unsigned(packet[9]) << 8) | packet[10];
}

std::array<u8, WiiDSBridgeNativeBeaconSize> NativeBeacon()
{
  std::array<u8, WiiDSBridgeNativeBeaconSize> beacon{};
  for (std::size_t i = 0; i < beacon.size(); ++i)
    beacon[i] = u8((i * 37 + 19) & 0xff);
  return beacon;
}
}  // namespace

int main()
{
  try
  {
    constexpr std::string_view prefix = "WIIDSBRIDGE/1 MELONDS ACK ";
    for (const std::string_view suffix : {
             "0123456789ABCDEF P1 M2", "abcdef0123456789 P1 M2",
             "0123456789ABCDEF M2 P1", "0123456789ABCDEF X1 P1 X2 M2"})
    {
      Require(WiiDSBridgeSupportsParentIdentity(std::string(prefix) + std::string(suffix)),
              "valid P1 token rejected");
    }
    Pass("p1-capability-is-an-independent-whole-token");

    for (const std::string_view ack : {
             "WIIDSBRIDGE/1 MELONDS ACK", "WIIDSBRIDGE/1 MELONDS ACK 0123456789ABCDEF M2",
             "WIIDSBRIDGE/1 MELONDS ACK 0123456789ABCDEF XP1 M2",
             "WIIDSBRIDGE/1 MELONDS ACK 0123456789ABCDEF P10 M2",
             "WIIDSBRIDGE/1 MELONDS ACK 0123456789ABCDEF p1 M2",
             "WIIDSBRIDGE/1 MELONDS ACK 0123456789ABCDEF P1x M2",
             "WIIDSBRIDGE/1 MELONDS ACK 0123456789ABCDEF\tP1 M2",
             "WIIDSBRIDGE/1 MELONDS ACK 0123456789ABCDE P1 M2",
             "WIIDSBRIDGE/1 MELONDS ACK 0123456789ABCDEFG P1 M2",
             "WIIDSBRIDGE/1 MELONDS ACK 0123456789ABCDEG P1 M2",
             "WIIDSBRIDGE/2 MELONDS ACK 0123456789ABCDEF P1 M2",
             "WIIDSBRIDGE/1 DOLPHIN ACK 0123456789ABCDEF P1 M2",
             "XWIIDSBRIDGE/1 MELONDS ACK 0123456789ABCDEF P1 M2"})
    {
      Require(!WiiDSBridgeSupportsParentIdentity(ack), "legacy/malformed ACK advertised P1");
    }
    Pass("legacy-malformed-nonce-prefix-and-substring-capabilities-are-rejected");

    Require(CanAdvertiseWiiDSBridgeParent(NaturalMAC, true), "capable natural parent blocked");
    Require(CanAdvertiseWiiDSBridgeParent(WiiDSBridgeLegacyParentMAC, false),
            "natural legacy identity blocked");
    Require(!CanAdvertiseWiiDSBridgeParent(NaturalMAC, false),
            "old companion may advertise an unrepresentable identity");
    for (std::size_t byte = 0; byte < WiiDSBridgeLegacyParentMAC.size(); ++byte)
    {
      auto other = WiiDSBridgeLegacyParentMAC;
      other[byte] ^= 2;
      Require(!CanAdvertiseWiiDSBridgeParent(other, false), "legacy identity match is not exact");
    }
    Pass("upgrade-gate-requires-capability-or-exact-legacy-parent");

    const auto beacon = NativeBeacon();
    const auto original = beacon;
    const auto packet = BuildWiiDSBridgeBeaconPacket(NaturalMAC, beacon, true);
    Require(packet && packet->size() == 147 && BodySize(*packet) == 136,
            "natural parent envelope length mismatch");
    Require(std::equal(Magic.begin(), Magic.end(), packet->begin()) &&
                (*packet)[8] == 12 && (*packet)[9] == 0 && (*packet)[10] == 136,
            "natural parent envelope framing mismatch");
    Require(std::equal(NaturalMAC.begin(), NaturalMAC.end(), packet->begin() + 11) &&
                std::equal(beacon.begin(), beacon.end(), packet->begin() + 17) &&
                beacon == original,
            "parent/beacon was not encoded atomically and verbatim");
    Pass("natural-parent-type12-encodes-mac-and-native-beacon-atomically");

    const auto capable_fixed = BuildWiiDSBridgeBeaconPacket(WiiDSBridgeLegacyParentMAC, beacon, true);
    Require(capable_fixed && capable_fixed->size() == 147 && BodySize(*capable_fixed) == 136 &&
                (*capable_fixed)[8] == 12 &&
                std::equal(WiiDSBridgeLegacyParentMAC.begin(), WiiDSBridgeLegacyParentMAC.end(),
                           capable_fixed->begin() + 11) &&
                std::equal(beacon.begin(), beacon.end(), capable_fixed->begin() + 17),
            "capable fixed parent incorrectly fell back to type2");
    Pass("capable-peer-always-gets-type12-even-with-legacy-parent-value");

    const auto legacy = BuildWiiDSBridgeBeaconPacket(WiiDSBridgeLegacyParentMAC, beacon, false);
    Require(legacy && legacy->size() == 141 && BodySize(*legacy) == 130 &&
                std::equal(Magic.begin(), Magic.end(), legacy->begin()) && (*legacy)[8] == 2 &&
                std::equal(beacon.begin(), beacon.end(), legacy->begin() + 11),
            "legacy fixed-parent wire format changed");
    Pass("legacy-fixed-parent-fallback-keeps-exact-type2-length-and-body");

    Require(!BuildWiiDSBridgeBeaconPacket(NaturalMAC, beacon, false),
            "packet builder bypassed legacy upgrade gate");
    auto changed = WiiDSBridgeLegacyParentMAC;
    changed.back() ^= 2;
    Require(!BuildWiiDSBridgeBeaconPacket(changed, beacon, false),
            "near-legacy parent incorrectly passed builder gate");
    Pass("legacy-unrepresentable-parents-produce-no-advertisement");

    for (const std::size_t size : {std::size_t{0}, std::size_t{1}, std::size_t{129},
                                   std::size_t{131}, std::size_t{136}, std::size_t{65536}})
    {
      const std::vector<u8> malformed(size, 0xa5);
      Require(!BuildWiiDSBridgeBeaconPacket(NaturalMAC, malformed, true),
              "type12 accepted malformed native beacon size");
      Require(!BuildWiiDSBridgeBeaconPacket(WiiDSBridgeLegacyParentMAC, malformed, false),
              "type2 accepted malformed native beacon size");
    }
    Pass("both-wire-formats-reject-every-noncanonical-native-beacon-size");

    Require(Groups == 8, "identity group count mismatch");
    std::printf("RESULT identityGroups=%u failures=0\n", Groups);
    return 0;
  }
  catch (const std::exception& error)
  {
    std::fprintf(stderr, "FAIL %s\n", error.what());
    std::printf("RESULT identityGroups=%u failures=1\n", Groups);
    return 1;
  }
}
