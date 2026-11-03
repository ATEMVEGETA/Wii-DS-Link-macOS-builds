# ROM-free macOS bridge checks

These tests exercise the real public-fork transport code without a game, save,
firmware dump, emulator GUI or physical wireless adapter. They are not retail
game or recording-performance validation.

Run `run-tests.sh MELONDS_SOURCE DOLPHIN_SOURCE OUTPUT_DIR`. It compiles the
production melonDS `WiiDSBridge.cpp` with a POSIX adaptation of the existing
eight-context/two-process regression fixture, then compiles Dolphin's actual
multiplayer helpers. Intel and Apple Silicon CI jobs should run them natively,
not only cross-compile the application bundles. Set `EXPECTED_ARCH=x86_64` or
`arm64` to fail if the runner has the wrong native architecture.

The bridge test uses IPv4 loopback UDP ports 7065-7080. It refuses to start if
any are occupied. Do not run it alongside a game session. It forks exactly one
owned child, closes a private pipe to stop it, and only signals that child PID
if cleanup times out. Existing emulator processes are never stopped.

The 13 melonDS cases retain the Windows transport fixture's protocol checks:
eight independent identities, auth/association and AIDs, foreign-peer rejection,
round-tagged commands and replies, correlated failure masks, per-context
release/rebind, STOP/disable isolation and final socket cleanup. The POSIX copy
adds explicit `IdentitySlot` assertions. Dolphin has six production-helper
groups covering peer capacity/AIDs, real reply aggregation, departure/rejoin,
stale rounds, watchdog/order behavior, malformed records and legacy isolation.

Reports include compiler version, native architecture, source/input hashes,
compile output, complete test stdout and test counts. A successful run does
not prove the app bundle starts, JIT works, a retail game connects, saves are
correct, or recording reaches any target frame rate.
