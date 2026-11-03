// Real production multiplayer helpers; no game, ROM, SDK or save is used.
#include "Core/IOS/Network/WD/WiiDSBridgeMultiplayer.h"

#include <cstdio>
#include <stdexcept>

using namespace IOS::HLE::WD;
namespace
{
void Require(bool value, const char* message)
{
  if (!value) throw std::runtime_error(message);
}
std::array<u8, 6> Mac(unsigned id) { return {0, 9, 0xbf, 4, 5, u8(id)}; }
unsigned BE16(const std::vector<u8>& bytes, unsigned offset)
{
  return (unsigned(bytes.at(offset)) << 8) | bytes.at(offset + 1);
}
WiiDSBridgePeer& Add(WiiDSBridgePeerTable& table, unsigned id,
                      WiiDSBridgeClock::time_point now)
{
  auto added = table.Register(7065 + id, std::to_string(id), true, now);
  Require(added.peer != nullptr, "registration failed");
  auto associated = table.Associate(*added.peer, Mac(id));
  Require(associated.aid == id + 1 && associated.joined, "distinct AID allocation failed");
  return *added.peer;
}
}
int main()
{
  try
  {
    const auto now = WiiDSBridgeClock::time_point{} + std::chrono::seconds(20);
    WiiDSBridgePeerTable table;
    for (unsigned id = 0; id < 15; ++id) Add(table, id, now);
    auto last = table.Register(7080, "sixteen", true, now);
    Require(last.peer && table.Associate(*last.peer, Mac(15)).aid == 0, "AID table overflow accepted");
    Require(!table.Register(7081, "invalid", true, now).peer, "port range escaped");
    Require(!table.Associate(*last.peer, Mac(0)).aid, "duplicate guest MAC accepted");
    Require(!table.Associate(*last.peer, {}).aid, "zero guest MAC accepted");
    auto multicast = Mac(31); multicast[0] = 1;
    Require(!table.Associate(*last.peer, multicast).aid, "multicast guest MAC accepted");
    const auto retry = table.Associate(*table.Find(7065), Mac(0));
    Require(retry.aid == 1 && !retry.joined, "association retry created another player");
    Require(table.Register(7065, "0", true, now).result ==
                WiiDSBridgePeerTable::RegisterResult::Unchanged, "heartbeat replaced live peer");
    std::puts("PASS capacity/distinct-AIDs/duplicate-MAC/association-retry/heartbeat");

    WiiDSBridgePeerTable players;
    for (unsigned id = 0; id < 8; ++id) Add(players, id, now);
    WiiDSBridgeRounds rounds;
    const auto id = rounds.Begin(0x1fe, players, now);
    Require(id.has_value(), "round not created");
    for (unsigned player = 0; player < 7; ++player)
      Require(rounds.Receive(*id, *players.Find(7065 + player),
                             std::vector<u8>(player + 2, u8(0xa0 + player))), "actual reply rejected");
    Require(!rounds.TakeCompleted(now), "eighth DS was falsely acknowledged");
    Require(!rounds.Receive(*id, *players.Find(7065), {1}), "duplicate reply counted twice");
    Require(!rounds.Receive(*id + 100, *players.Find(7072), {1}), "unknown round accepted");
    Require(rounds.Receive(*id, *players.Find(7072), {4, 3, 2, 1, 0}), "eighth DS reply rejected");
    auto complete = rounds.TakeCompleted(now);
    Require(complete && complete->FailureMask() == 0 && complete->Replies().size() == 8,
            "eight-client completion mismatch");
    auto envelope = BuildWiiDSMPReceiveEnvelope(complete->Replies());
    Require(envelope && envelope->size() == 10 + 8 * 16 && BE16(*envelope, 4) == 8 &&
                BE16(*envelope, 6) == 16, "MP-end aggregate count/stride incorrect");
    for (unsigned player = 0; player < 8; ++player)
      Require(BE16(*envelope, 10 + player * 16 + 4) == player + 1,
              "MP-end record attributed to wrong player");
    Require((*envelope)[18] == 0xa0 && (*envelope)[34] == 0xa1 && (*envelope)[130] == 4,
            "MP-end payload changed or record offset incorrect");
    std::puts("PASS eight-real-replies/early-ACK-rejection/round-ID/aggregate-MP-end");

    const auto old = rounds.Begin(6, players, now);
    Require(old && rounds.Receive(*old, *players.Find(7065), {0x12}), "partial round setup");
    const auto generation = players.Find(7066)->generation;
    rounds.RemovePeer(7066, generation);
    players.Remove(7066);
    auto replacement = players.Register(7066, "replacement", true, now);
    Require(replacement.peer && players.Associate(*replacement.peer, Mac(22)).aid == 2,
            "departed AID could not be reused");
    Require(!rounds.Receive(*old, *replacement.peer, {0x34}), "new DS answered old DS's round");
    complete = rounds.TakeCompleted(now);
    Require(complete && complete->FailureMask() == 4 && complete->Replies().size() == 1,
            "disconnected player was marked successful");
    Require(players.Find(7065)->client.associated && players.Find(7067)->client.associated,
            "one departure removed other players");
    std::puts("PASS per-player-departure/AID-reuse/stale-generation-rejection");

    const auto first = rounds.Begin(0x8002, players, now);
    const auto second = rounds.Begin(2, players, now);
    Require(first && second && rounds.Receive(*second, *players.Find(7065), {0x55}),
            "ordered round setup failed");
    Require(!rounds.TakeCompleted(now), "later completion overtook pending round");
    Require(rounds.Receive(*first, *players.Find(7065), {0x44}), "first round reply failed");
    Require(!rounds.TakeCompleted(now + std::chrono::milliseconds(999)), "missing DS timed out early");
    complete = rounds.TakeCompleted(now + std::chrono::milliseconds(1000));
    Require(complete && complete->id == *first && complete->FailureMask() == 0x8000,
            "absent AID was falsely successful or watchdog incorrect");
    complete = rounds.TakeCompleted(now + std::chrono::milliseconds(1000));
    Require(complete && complete->id == *second && complete->FailureMask() == 0,
            "ordered second completion lost");
    std::puts("PASS ordered-rounds/missing-player-failure/watchdog-boundary");

    Require(!BuildWiiDSMPReceiveEnvelope({{0, {1}}}), "parent AID record accepted");
    Require(!BuildWiiDSMPReceiveEnvelope({{1, {1}}, {1, {2}}}), "duplicate AID record accepted");
    Require(!BuildWiiDSMPReceiveEnvelope({{16, {1}}}), "out-of-range AID accepted");
    Require(!BuildWiiDSMPReceiveEnvelope({{1, std::vector<u8>(2049)}}), "oversized payload accepted");
    const auto empty = BuildWiiDSMPReceiveEnvelope({});
    Require(empty && empty->size() == 10 && BE16(*empty, 4) == 0, "empty round invented reply");
    rounds.Clear();
    for (unsigned i = 0; i < WiiDSBridgeMaximumRounds; ++i)
      Require(bool(rounds.Begin(2, players, now)), "round capacity too small");
    Require(!rounds.Begin(2, players, now), "unbounded pending rounds");
    rounds.Clear();
    Require(rounds.Size() == 0, "reset retained pending work");
    std::puts("PASS malformed-records/zero-replies/bounded-round-queue/reset");

    WiiDSBridgePeerTable legacy;
    Require(legacy.Register(7065, "old", false, now).peer, "legacy peer rejected");
    Require(!legacy.Register(7066, "new", true, now).peer, "legacy/M2 mixed sessions accepted");
    legacy.Clear();
    Require(legacy.Register(7065, "new", true, now).peer, "M2 peer rejected");
    Require(!legacy.Register(7066, "old", false, now).peer, "M2/legacy mixed sessions accepted");
    std::puts("PASS legacy-session-isolation");
    return 0;
  }
  catch (const std::exception& error)
  {
    std::fprintf(stderr, "FAIL %s\n", error.what());
    return 1;
  }
}
