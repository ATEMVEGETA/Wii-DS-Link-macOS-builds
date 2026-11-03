# Experimental macOS Dolphin build helper

Only the public Wii-DS Link fork is used. No Playnite source or runtime is
changed. Native `arm64` and `x86_64` jobs produce separate app candidates;
these are not yet a universal release and have no retail Mac confirmation.

## Inputs

Run on a matching native hosted Mac (not Rosetta) with Xcode/AppleClang,
CMake 3.25 or newer, Ninja, Python 3, and the official universal Qt 6.8.3 SDK.
The parent workflow installs Qt via aqt and supplies its prefix. Initialize
the pinned source submodules recursively, except the Windows-only Qt and
FFmpeg binary submodules need not be fetched. Qt Core/Gui/Widgets/Svg and
their Cocoa/macstyle plugins must be present. No mutable Homebrew Qt is used.

```sh
MACOSX_DEPLOYMENT_TARGET=15.0 JOBS=3 bash build-dolphin.sh \
  sources/dolphin build/dolphin-arm64 out/dolphin-arm64 arm64 /path/to/Qt/6.8.3/macos
```

The script uses upstream native CMake app bundling, dependency fixups and
ad-hoc signing with the existing Dolphin JIT entitlements. It preserves the
native Metal/OpenGL backends. For this initial experimental candidate,
Vulkan/MoltenVK, in-emulator FFmpeg frame dumping, LLVM disassembly and the
upstream updater are disabled. External screen recording is not disabled,
and no 1080p/60fps hardware-performance guarantee is made.

The initial minimum macOS target is 15.0 for both architectures. The bundle
audit checks the actual Mach-O deployment load commands; compilation does
not establish support for older Macs/macOS. The apps are ad-hoc signed only,
not Developer-ID signed or notarized. Gatekeeper acceptance and real JIT/GPU
behavior still require user testing.

## Output and validation

The source build emits `build/Binaries/DolphinQt.app`; the helper copies it
as `out/Dolphin.app`. It also records source/submodule identity, license
notices, a JSON architecture/dependency/minimum-OS audit and code-sign data.
It rejects absolute non-system dylib loads and escaped bundle symlinks.
The helper additionally starts a copied app for ten seconds with a private
`-u` profile, analytics consent disabled and no ROM. This catches startup
dyld/plugin failures but does not establish retail GPU/JIT performance.
Only the owned fixture PID is signalled during cleanup. No games, NAND,
BIOS, saves or personal settings are packaged. The private fixture is removed;
its scalar JSON result and stdout/stderr stay separate in `smoke-report`.

The production `WiiDSBridgeMultiplayer.h` has no Windows API dependency.
The existing six-group `DolphinMultiplayerFixture.cpp` can be compiled by
AppleClang with `-std=c++20 -I sources/dolphin/Source/Core` to verify the
real peer/round logic without games or a hardware adapter. The parent
macOS tests own that portable fixture; it is not duplicated here.

To isolate test settings, the combined package may place `portable.txt`
beside `Dolphin.app`, yielding an adjacent `User` directory on first launch.
Do not put user data inside the signed app or ship a preconfigured profile.
