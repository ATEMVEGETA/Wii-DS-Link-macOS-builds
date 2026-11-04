#!/bin/bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
    echo "Usage: $0 MELONDS_SOURCE DOLPHIN_SOURCE OUTPUT_DIR" >&2
    exit 2
fi
melon=$(cd "$1" && pwd)
dolphin=$(cd "$2" && pwd)
output=$3
here=$(cd "$(dirname "$0")" && pwd)
cxx=${CXX:-clang++}
native=$(uname -m)
if [[ -n ${EXPECTED_ARCH:-} && $native != "$EXPECTED_ARCH" ]]; then
    echo "Expected native $EXPECTED_ARCH runner, found $native" >&2
    exit 2
fi
case $(uname -s) in
    Darwin|Linux) ;;
    *) echo "These fixtures require a POSIX host (macOS or Linux)." >&2; exit 2 ;;
esac
mkdir -p "$output"
output=$(cd "$output" && pwd)
"$cxx" --version > "$output/compiler.txt"
printf '%s\n' "$native" > "$output/architecture.txt"

"$cxx" -std=c++20 -Wall -Wextra -Werror "$here/CompilerFeatureProbe.cpp" \
    -o "$output/CompilerFeatureProbe" > "$output/compiler-feature-probe.log" 2>&1
"$output/CompilerFeatureProbe"

"$cxx" -std=c++17 -O2 -Wall -Wextra -Werror -pthread \
    -I"$melon/src" "$here/MultiDSFixture.cpp" \
    "$melon/src/net/WiiDSBridge.cpp" -o "$output/MultiDSFixture" \
    > "$output/melonds-compile.log" 2>&1
"$output/MultiDSFixture" | tee "$output/melonds-test.log"
grep -q '^RESULT cases=13 failures=0 ' "$output/melonds-test.log"
[[ $(grep -c '^PASS ' "$output/melonds-test.log") -eq 13 ]]

"$cxx" -std=c++20 -O2 -Wall -Wextra -Werror \
    -I"$dolphin/Source/Core" "$here/DolphinMultiplayerFixture.cpp" \
    -o "$output/DolphinMultiplayerFixture" \
    > "$output/dolphin-compile.log" 2>&1
"$output/DolphinMultiplayerFixture" | tee "$output/dolphin-test.log"
[[ $(grep -c '^PASS ' "$output/dolphin-test.log") -eq 6 ]]

"$cxx" -std=c++20 -O2 -Wall -Wextra -Werror \
    -I"$dolphin/Source/Core" "$here/DolphinIdentityFixture.cpp" \
    -o "$output/DolphinIdentityFixture" \
    > "$output/dolphin-identity-compile.log" 2>&1
"$output/DolphinIdentityFixture" | tee "$output/dolphin-identity-test.log"
grep -q '^RESULT identityGroups=8 failures=0$' "$output/dolphin-identity-test.log"
[[ $(grep -c '^PASS ' "$output/dolphin-identity-test.log") -eq 8 ]]

inputs=(
    "$here/MultiDSFixture.cpp" "$here/DolphinMultiplayerFixture.cpp"
    "$here/DolphinIdentityFixture.cpp"
    "$melon/src/net/WiiDSBridge.cpp" "$melon/src/net/WiiDSBridge.h"
    "$melon/src/net/WiiDSMPConfig.h"
    "$dolphin/Source/Core/Core/IOS/Network/WD/WiiDSBridgeMultiplayer.h"
    "$dolphin/Source/Core/Core/IOS/Network/WD/WiiDSBridgeDisconnect.h"
    "$dolphin/Source/Core/Core/IOS/Network/WD/WiiDSBridgeIdentity.h"
)
if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "${inputs[@]}" > "$output/input-sha256.txt"
else
    sha256sum "${inputs[@]}" > "$output/input-sha256.txt"
fi
printf '{"passed":true,"architecture":"%s","melonDSCases":13,"dolphinGroups":6,"dolphinIdentityGroups":8,"retailGameVerified":false}\n' \
    "$native" > "$output/result.json"
echo "ROM_FREE_MACOS_CHECKS_PASSED architecture=$native melonDS=13 dolphin=6 dolphinIdentity=8"
