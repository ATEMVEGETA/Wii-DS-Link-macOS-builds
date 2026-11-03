# Experimental public melonDS macOS build inputs

These helpers build only the public Wii-DS fork. They do not change the
Playnite variants, upload a release, or require a developer certificate.

Run on a native macOS 15 Intel or Apple Silicon runner with CMake, Ninja,
pkg-config, Python 3.12, SDL2, libarchive, zstd, faad2 and ENet available. Supply
the official Qt **6.8.3** macOS SDK with Multimedia and SVG modules. Do not
install Homebrew's full Qt metapackage as part of this recipe: current Qt
formulae can require a lengthy Intel source build and many unused modules.

```sh
JOBS=2 MACOSX_DEPLOYMENT_TARGET=15.0 bash build-melonds.sh \
  /path/to/public/melonDS /path/to/build /path/to/output arm64 /path/to/Qt/6.8.3/macos
```

Use `x86_64` on the Intel runner. The upstream source enables ARM64 JIT and
disables Intel-macOS JIT by default; this recipe preserves that behavior. No
recording-performance or native-game frame-rate promise follows from a build.

`BUILD_STATIC` stays OFF because the Windows-oriented frontend adds a global
`-static` linker option when it is ON. Qt is bundled with `macdeployqt`; the
deployment helper supplements that with recursive SDL/archive/faad/ENet and
other non-system dependency closure, since Qt's deployment tool does not
guarantee those app-specific dependencies. Universal SDK libraries are thinned
to the requested architecture. Homebrew/SDK load paths are rewritten to the
bundle, then libraries/frameworks and the app are ad-hoc signed. The app has
the `allow-jit` entitlement; it is not Developer ID signed or notarized.

The shared `../dolphin/audit_bundle.py` verifies architecture, requested minimum
macOS, and remaining non-system load paths. `MACOS_AUDITOR` can override its
location. The effective deployment minimum must include all bundled libraries,
not only the CMake compiler target; a dependency requiring newer than 15.0
must fail the current audit rather than silently ship a misleading minimum.

Two owned copies of the signed app are smoke-tested with private portable
profiles and the offscreen Qt plugin. They verify a fresh profile does not
answer Wii discovery, while automatic mode returns the actual packaged
frontend's stable loopback HELLO/ACK. No ROM, save, dumped firmware, physical
adapter or existing melonDS configuration is opened. The copied no-ROM child
is terminated by PID; that is not a normal retail shutdown test. The distributed
signed app is not modified by the smoke test.

Generated files include `melonDS.app`, `BUILD-INFO.json`, `SMOKE-TEST.json`,
`BUNDLE-AUDIT.json` and smoke logs. Metadata explicitly leaves retail gameplay
unverified. Actual transfers, saves, multiplayer, graphics, audio and recording
still require later testing on a user's Mac. Keep source/build receipts and
third-party notices with each experimental package before public distribution.
