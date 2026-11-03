#!/usr/bin/env bash
# Native macOS build of the public Wii-DS fork. No public upload is performed.
set -euo pipefail

if [[ $# -ne 5 ]]; then
  echo "Usage: build-melonds.sh SOURCE BUILD OUTPUT ARCH QT_PREFIX" >&2
  exit 2
fi
source_dir="$(cd "$1" && pwd)"
build_dir="$2"
output_dir="$3"
arch="$4"
minimum_os="${MACOSX_DEPLOYMENT_TARGET:-15.0}"
jobs="${JOBS:-2}"
script_dir="$(cd "$(dirname "$0")" && pwd)"
QT_ROOT="$5"
if [[ "$(uname -s)" != Darwin || "$(uname -m)" != "$arch" ]]; then
  echo "Build on a native $arch macOS runner; no Rosetta cross-build is used." >&2
  exit 2
fi
qt_root="$(cd "$QT_ROOT" && pwd)"
if [[ ! -x "$qt_root/bin/macdeployqt" ]]; then
  echo "Qt SDK is missing macdeployqt: $qt_root" >&2
  exit 2
fi
if [[ "$("$qt_root/bin/qmake" -query QT_VERSION)" != 6.8.3 ]]; then
  echo "This experimental recipe requires the pinned Qt 6.8.3 SDK." >&2
  exit 2
fi
mkdir -p "$build_dir" "$output_dir"
build_dir="$(cd "$build_dir" && pwd)"
output_dir="$(cd "$output_dir" && pwd)"
brew_prefix="$(brew --prefix)"
export PKG_CONFIG_PATH="$brew_prefix/opt/sdl2/lib/pkgconfig:$brew_prefix/opt/libarchive/lib/pkgconfig:$brew_prefix/opt/zstd/lib/pkgconfig:$brew_prefix/opt/faad2/lib/pkgconfig:$brew_prefix/opt/enet/lib/pkgconfig:${PKG_CONFIG_PATH:-}"
export MACOSX_DEPLOYMENT_TARGET="$minimum_os"
revision="$(git -C "$source_dir" rev-parse HEAD)"

# The pinned upstream template incorrectly uses <key> inside its archive
# extension array. Normalize only those 23 known entries in this disposable
# CI checkout, before CMake/macdeployqt parse or copy the plist. Keep the exact
# source delta with the candidate; the public source repository is not changed.
python3 - "$source_dir/res/melon.plist.in" <<'PY'
from pathlib import Path
import plistlib
import re
import sys

path = Path(sys.argv[1])
original = path.read_text(encoding="utf-8")
expected = [
    "zip", "7z", "rar", "tar", "tar.gz", "tgz", "tar.xz", "txz",
    "tar.bz2", "tbz2", "tar.lz4", "tlz4", "tar.zst", "tzst",
    "tar.Z", "taz", "tar.lz", "tar.lzma", "tlz", "tar.lrz",
    "tlrz", "tar.lzo", "tzo",
]
changed = 0

def normalize(match):
    global changed
    body = match.group(2)
    keys = re.findall(r"<key>([^<>]*)</key>", body)
    if keys:
        if keys != expected or changed:
            raise RuntimeError("Unexpected malformed extension array; source patch refused")
        body = re.sub(r"<key>([^<>]*)</key>", r"<string>\1</string>", body)
        changed += len(keys)
    return match.group(1) + body + match.group(3)

updated = re.sub(
    r"(<key>CFBundleTypeExtensions</key>\s*<array>)(.*?)(</array>)",
    normalize, original, flags=re.S,
)
data = plistlib.loads(updated.encode("utf-8"))
extensions = [entry.get("CFBundleTypeExtensions", [])
              for entry in data["CFBundleDocumentTypes"]]
if expected not in extensions or any(
    not isinstance(extension, str) for group in extensions for extension in group
):
    raise RuntimeError("Unexpected normalized extension list")
if changed not in (0, 23):
    raise RuntimeError("Unexpected source patch count")
if updated != original:
    path.write_text(updated, encoding="utf-8")
print(f"MELONDS_PLIST_ARCHIVE_TYPES_NORMALIZED={changed}")
PY
git -C "$source_dir" diff --check -- res/melon.plist.in
git -C "$source_dir" diff --binary -- res/melon.plist.in > "$output_dir/SOURCE-PATCH.diff"

cmake -S "$source_dir" -B "$build_dir" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
  -DCMAKE_OSX_ARCHITECTURES="$arch" -DCMAKE_OSX_DEPLOYMENT_TARGET="$minimum_os" \
  -DCMAKE_PREFIX_PATH="$qt_root;$brew_prefix" \
  -DUSE_VCPKG=OFF -DUSE_QT6=ON -DBUILD_STATIC=OFF \
  -DMACOS_BUNDLE_LIBS=OFF -DMACOS_BUILD_DMG=OFF \
  -DENABLE_LTO_RELEASE=OFF -DMELONDS_EMBED_BUILD_INFO=ON \
  '-DMELONDS_VERSION_SUFFIX= - WiiDS Link macOS Experimental' \
  -DMELONDS_GIT_HASH="$revision" \
  -DMELONDS_GIT_BRANCH=wiids-macos-experimental \
  -DMELONDS_BUILD_PROVIDER=PrivateMacOSBuild
cmake --build "$build_dir" --parallel "$jobs"

python3 "$script_dir/deploy.py" \
  --app "$build_dir/melonDS.app" --qt-root "$qt_root" \
  --source "$source_dir" --output "$output_dir" --arch "$arch" \
  --minimum-os "$minimum_os" --entitlements "$script_dir/entitlements.plist"
python3 "${MACOS_AUDITOR:-$script_dir/../dolphin/audit_bundle.py}" "$output_dir/melonDS.app" \
  --arch "$arch" --minimum "$minimum_os" --output "$output_dir/BUNDLE-AUDIT.json"
