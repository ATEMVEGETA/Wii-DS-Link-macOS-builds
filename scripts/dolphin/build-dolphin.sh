#!/usr/bin/env bash
# Native experimental macOS build of the public Wii-DS Dolphin fork.
# Usage: build-dolphin.sh SOURCE BUILD OUTPUT ARCH QT_PREFIX
set -euo pipefail
[[ $# -eq 5 ]] || { echo "Usage: $0 SOURCE BUILD OUTPUT {arm64|x86_64} QT_PREFIX" >&2; exit 2; }
source_dir=$(cd "$1" && pwd -P)
build_dir=$2
output_dir=$3
target_arch=$4
qt_prefix=$(cd "$5" && pwd -P)
script_dir=$(cd "$(dirname "$0")" && pwd -P)
deployment_target=${MACOSX_DEPLOYMENT_TARGET:-15.0}
jobs=${JOBS:-3}
[[ $(uname -s) == Darwin ]] || { echo "Run this helper on macOS." >&2; exit 2; }
case "$target_arch" in arm64|x86_64) ;; *) echo "Unsupported architecture." >&2; exit 2;; esac
[[ $(uname -m) == "$target_arch" ]] || { echo "Use a native $target_arch runner." >&2; exit 2; }
[[ "$jobs" =~ ^[1-8]$ ]] || { echo "JOBS must be 1 through 8." >&2; exit 2; }
[[ -f "$source_dir/Source/Core/Core/IOS/Network/WD/WiiDSBridgeMultiplayer.h" ]] || { echo "Not Wii-DS public Dolphin source." >&2; exit 2; }
[[ -f "$qt_prefix/lib/cmake/Qt6/Qt6Config.cmake" ]] || { echo "Missing pinned Qt6 SDK." >&2; exit 2; }
mkdir -p "$build_dir" "$output_dir"
build_dir=$(cd "$build_dir" && pwd -P)
output_dir=$(cd "$output_dir" && pwd -P)
[[ "$source_dir" != "$build_dir" && "$source_dir" != "$output_dir" && "$build_dir" != "$output_dir" ]] || { echo "Source/build/output must differ." >&2; exit 2; }
[[ ! -e "$output_dir/Dolphin.app" ]] || { echo "Refusing to overwrite an existing app candidate." >&2; exit 2; }

# Qt is the official pinned universal 6.8.3 SDK supplied by CI. Other core
# dependencies are pinned vendored static libraries, not Homebrew runtime
# dependencies. Native Metal/OpenGL remain enabled. Optional Vulkan/MoltenVK
# and in-emulator FFmpeg frame dumping are excluded from this first candidate;
# external recording is unaffected. No updater may replace this fork.
cmake -S "$source_dir" -B "$build_dir" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
  -DCMAKE_OSX_ARCHITECTURES="$target_arch" \
  -DCMAKE_OSX_DEPLOYMENT_TARGET="$deployment_target" \
  -DCMAKE_PREFIX_PATH="$qt_prefix" -DQt6_DIR="$qt_prefix/lib/cmake/Qt6" \
  -DUSE_SYSTEM_LIBS=OFF \
  -DENABLE_QT=ON -DENABLE_TESTS=OFF -DENABLE_LTO=OFF \
  -DENABLE_VULKAN=OFF -DENABLE_AUTOUPDATE=OFF \
  -DENABLE_LLVM=OFF -DENCODE_FRAMEDUMPS=OFF \
  -DPOSTPROCESS_BUNDLE=ON -DMACOS_CODE_SIGNING=ON \
  -DMACOS_CODE_SIGNING_IDENTITY=- \
  -DDISTRIBUTOR=WiiDSLink-macOS-experimental
cmake --build "$build_dir" --target dolphin-emu --parallel "$jobs"
app="$build_dir/Binaries/DolphinQt.app"
[[ -x "$app/Contents/MacOS/DolphinQt" ]] || { echo "Missing DolphinQt.app output." >&2; exit 1; }
ditto "$app" "$output_dir/Dolphin.app"
# Upstream deployment can retain SDK runpaths. Reuse the native closure
# packer to remove host paths, thin universal SDK libraries, and re-sign.
python3 - "$output_dir/Dolphin.app" "$qt_prefix" "$target_arch" \
  "$source_dir/Source/Core/DolphinQt/DolphinEmu.entitlements" \
  "$script_dir/../melonDS/deploy.py" <<'PY'
import importlib.util
from pathlib import Path
import sys
spec = importlib.util.spec_from_file_location("bundle_closure", sys.argv[5])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.Closure(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], executable="DolphinQt").apply()
module.sign(Path(sys.argv[1]), Path(sys.argv[4]))
PY
python3 "$script_dir/audit_bundle.py" "$output_dir/Dolphin.app" \
  --arch "$target_arch" --minimum "$deployment_target" \
  --output "$output_dir/DOLPHIN-BUNDLE-AUDIT.json"
codesign --verify --deep --strict --verbose=2 "$output_dir/Dolphin.app"
codesign -d --entitlements :- "$output_dir/Dolphin.app" \
  > "$output_dir/DOLPHIN-ENTITLEMENTS.plist" 2> "$output_dir/DOLPHIN-CODESIGN.log"
python3 "$script_dir/smoke_dolphin.py" "$output_dir/Dolphin.app" \
  --output "$output_dir/smoke-report"
mkdir -p "$output_dir/LICENSES-Dolphin"
cp "$source_dir/COPYING" "$output_dir/LICENSES-Dolphin/COPYING"
ditto "$source_dir/LICENSES" "$output_dir/LICENSES-Dolphin/upstream"
git -C "$source_dir" rev-parse HEAD > "$output_dir/DOLPHIN-SOURCE-COMMIT.txt"
git -C "$source_dir" ls-tree -r HEAD | awk '$1 == "160000" { print }' > "$output_dir/DOLPHIN-SUBMODULE-REVISIONS.txt"
echo "DOLPHIN_MACOS_APP=$output_dir/Dolphin.app"
