# Experimental Wii-DS Link for macOS

These are the public melonDS/Dolphin forks, not the custom Playnite builds.
Use the arm64 download on Apple Silicon Macs and x86_64 on Intel Macs.
The first experimental builds require macOS 15 or later. They are not yet
verified with real games, save transfers, JIT gameplay, or recording software.

## Install and open

Extract the entire ZIP into a normal writable folder. Do not launch the apps
from inside the ZIP, a read-only disk image, or a protected system directory.
Keep the folder layout: melonDS has an adjacent empty portable folder and
Dolphin has an adjacent portable.txt marker. Their new profiles stay alongside
the test package rather than replacing an existing emulator installation.

The apps are ad-hoc signed, not Apple Developer-ID signed or notarized. macOS
may initially block a downloaded app. Only if you trust this build and have
verified its checksum, use Apple's normal Privacy & Security "Open Anyway"
process after attempting to open it. Do not globally disable Gatekeeper.
No Apple Developer subscription or paid software is required to test.

Games, BIOS/firmware, NAND, Miis, configurations and saves are not supplied.
Use your own material and backed-up copies of saves. Never let two running
instances write to the same save path, and do not interrupt an active save.

## Link the apps

1. In melonDS, stop the game first if necessary, open Config -> Wifi settings
   and enable Connect to Dolphin. The choice is saved per DS instance.
2. In Dolphin, enable Connect to melonDS in Configuration -> Wii -> Misc
   Settings. No USB Wi-Fi adapter or external relay is used.
3. Start the games in either order and overlap their in-game connection
   screens. Test Ranch with Diamond/Pearl and the previously matched saves.
4. Check deposit, save, clean disconnect, close/reopen and withdrawal.
5. For Battle Revolution copying, use the matching DS/Wii save identity.
6. For DS Battle Mode, add DS instances one at a time, enable each one's
   Connect to Dolphin option, disable pause-on-focus-loss if needed, and stay
   within the game's four-player limit. Try one participant disconnect/rejoin.

Report the Mac chip/model, macOS version, package architecture, game versions,
exact failure screen and whether game saves were preserved. Automated
loopback and startup checks do not prove a full retail connection.

## Initial limitations

- melonDS uses its original upstream JIT defaults: Intel macOS is
  interpreter-only in this source base. Do not expect identical performance
  across Intel and Apple Silicon.
- Dolphin's first candidate uses Metal/OpenGL. Vulkan, built-in FFmpeg frame
  dumping and auto-update are disabled. External screen recording is separate.
- No 1080p/60fps recording or full-speed gameplay guarantee is made.
- The link is same-Mac loopback only, not LAN/internet/physical-console Wi-Fi.
- The package contains source commits, build helpers, licenses and checksums.
  Do not publish it as a stable release until real-Mac testing is complete.
