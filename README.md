# Free experimental macOS build workspace

This separate public build-only repository uses standard GitHub-hosted Mac
runners, which are free for public repositories. It does not use larger/private
runners, Actions caches, Actions artifact storage, Git LFS or paid services.
It never publishes an emulator release or changes the existing public emulator
repositories. No Playnite source, build or profile is included.

Runs are manual-only. A job is skipped if this repository is private; the
runner also verifies public visibility before building and before saving
results. Do not change visibility while a run is active. Runner labels are
restricted to macos-15 and macos-15-intel, not larger runners.

Generated packages and diagnostic archives are encrypted with a committed RSA
public key and stored only as encrypted draft release assets. The private key
stays in the owner's local D:/Codex project, outside this repository. There are
no usable public emulator downloads here. Do not publish these draft releases.

Pinned public sources:

- melonDS: aca81fea811cc96b2cd216dd8b3f6f447a601c8f (public beta.2)
- Dolphin: 22060863a04f086bd0c51a6fdf7500e27470d35d (public beta.1)
- Qt: official universal macOS SDK 6.8.3, via aqtinstall 3.3.0.

Separate native arm64 and x86_64 bundles initially target macOS 15. Dependencies,
signatures and isolated startup/discovery checks must pass. The app bundles
are ad-hoc-signed, not Developer-ID-signed/notarized distribution builds.
No retail compatibility or recording-performance claim is made until real Macs
have tested the pair.

The initial Dolphin candidate uses Metal/OpenGL. Vulkan, built-in FFmpeg frame
dumping and the automatic updater are disabled. melonDS retains upstream's JIT
defaults (Intel macOS is interpreter-only in this source base).

Original helper/source licenses remain applicable. No Nintendo games,
BIOS/firmware, NAND, personal settings, or saves are included.
