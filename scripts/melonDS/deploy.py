#!/usr/bin/env python3
"""Bundle Qt and non-Qt dependencies, ad-hoc sign, then smoke a private copy.

This is a macOS-only CI packaging tool; it never reads an installed user's
melonDS profile and does not publish an artifact or use a Developer ID key.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import socket
import subprocess
import tempfile
import time


def run(*args: str | Path) -> str:
    return subprocess.check_output([str(arg) for arg in args], text=True).strip()


def inside(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def macho_files(root: Path) -> list[Path]:
    found: dict[Path, Path] = {}
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            if "Mach-O" in run("file", "-b", path):
                found[path.resolve()] = path
    return sorted(found.values())


def dependencies(image: Path) -> list[str]:
    # Universal Qt SDK images print an extra non-indented architecture header.
    # Only the indented compatibility-version records are load commands.
    return list(dict.fromkeys(re.sub(r" \(.*", "", line.strip())
                for line in run("otool", "-L", image).splitlines()
                if line[:1].isspace() and " (compatibility version " in line))


def rpaths(image: Path) -> list[str]:
    lines = run("otool", "-l", image).splitlines()
    result: list[str] = []
    for index, line in enumerate(lines):
        if line.strip() == "cmd LC_RPATH":
            for following in lines[index + 1:index + 5]:
                match = re.match(r"\s*path (.*?) \(offset \d+\)", following)
                if match:
                    result.append(match.group(1))
                    break
    return list(dict.fromkeys(result))


def system_library(name: str) -> bool:
    return name.startswith(("/System/Library/", "/usr/lib/"))


class Closure:
    def __init__(self, app: Path, qt_root: Path, arch: str, executable: str = "melonDS"):
        self.app = app
        self.main = app / "Contents/MacOS" / executable
        self.frameworks = app / "Contents/Frameworks"
        self.frameworks.mkdir(parents=True, exist_ok=True)
        self.qt_root = qt_root
        self.arch = arch
        self.origins: dict[Path, Path] = {}
        self.copied: dict[Path, Path] = {}

    def expand(self, value: str, image: Path) -> Path:
        if value.startswith("@loader_path"):
            return image.parent / value.replace("@loader_path", "", 1).lstrip("/")
        if value.startswith("@executable_path"):
            return self.main.parent / value.replace("@executable_path", "", 1).lstrip("/")
        return Path(value)

    def resolve(self, name: str, image: Path) -> Path:
        origin = self.origins.get(image.resolve(), image)
        candidates: list[Path] = []
        if name.startswith("@rpath/"):
            suffix = name[len("@rpath/"):]
            for current in (image, origin, self.main):
                candidates += [self.expand(value, current) / suffix for value in rpaths(current)]
            candidates += [self.frameworks / suffix, self.qt_root / "lib" / suffix]
        elif name.startswith("@loader_path"):
            candidates = [self.expand(name, image), self.expand(name, origin)]
        elif name.startswith("@executable_path"):
            candidates = [self.expand(name, self.main)]
        else:
            candidates = [Path(name)]
        for candidate in candidates:
            if candidate.exists():
                return candidate.resolve()
        raise RuntimeError(f"Cannot resolve {name!r} required by {image.name}")

    def copy_dependency(self, source: Path) -> Path:
        source = source.resolve()
        if source in self.copied:
            return self.copied[source]
        framework = next((parent for parent in [source, *source.parents]
                          if parent.name.endswith(".framework")), None)
        if framework:
            target_root = self.frameworks / framework.name
            if not target_root.exists():
                shutil.copytree(framework, target_root, symlinks=True)
                for target in macho_files(target_root):
                    self.origins[target.resolve()] = framework / target.relative_to(target_root)
            target = target_root / source.relative_to(framework)
        else:
            target = self.frameworks / source.name
            if target.exists():
                if target.read_bytes() != source.read_bytes():
                    raise RuntimeError(f"Conflicting dependency filenames: {source.name}")
            else:
                shutil.copy2(source, target)
                self.origins[target.resolve()] = source
        target.chmod(target.stat().st_mode | 0o200)
        self.copied[source] = target.resolve()
        return target.resolve()

    def apply(self) -> None:
        handled: set[Path] = set()
        while True:
            pending = [path for path in macho_files(self.app) if path.resolve() not in handled]
            if not pending:
                break
            for image in pending:
                handled.add(image.resolve())
                image.chmod(image.stat().st_mode | 0o200)
                slices = run("lipo", "-archs", image).split()
                if self.arch not in slices:
                    raise RuntimeError(f"Required {self.arch} slice missing from {image.name}")
                if len(slices) > 1:
                    temporary = image.with_name(image.name + ".wiids-thin")
                    mode = image.stat().st_mode
                    run("lipo", image, "-thin", self.arch, "-output", temporary)
                    temporary.chmod(mode)
                    os.replace(temporary, image)
                for old in dependencies(image):
                    if system_library(old):
                        continue
                    dependency = self.resolve(old, image)
                    if not inside(dependency, self.app):
                        dependency = self.copy_dependency(dependency)
                    if inside(dependency, self.frameworks):
                        new = "@rpath/" + dependency.relative_to(self.frameworks.resolve()).as_posix()
                        if old != new:
                            run("install_name_tool", "-change", old, new, image)
                # A bundle-relative lookup must win; never depend on the builder's
                # Homebrew/Qt install path after the archive is transferred.
                for old in rpaths(image):
                    if old.startswith("/") or (old.startswith("@") and not inside(self.expand(old, image), self.app)):
                        run("install_name_tool", "-delete_rpath", old, image)
                desired = "@loader_path/" + os.path.relpath(self.frameworks, image.parent)
                if desired not in rpaths(image):
                    run("install_name_tool", "-add_rpath", desired, image)
                ids = [line.strip() for line in run("otool", "-D", image).splitlines()
                       if line.strip() and not line.rstrip().endswith(":")]
                if ids and inside(image, self.frameworks):
                    new_id = "@rpath/" + image.resolve().relative_to(self.frameworks.resolve()).as_posix()
                    if ids[0].strip() != new_id:
                        run("install_name_tool", "-id", new_id, image)


def sign(app: Path, entitlements: Path) -> None:
    for image in macho_files(app):
        run("codesign", "--force", "--sign", "-", image)
    for framework in sorted(app.rglob("*.framework"), key=lambda value: len(value.parts), reverse=True):
        if not framework.is_symlink():
            run("codesign", "--force", "--sign", "-", framework)
    run("codesign", "--force", "--sign", "-", "--entitlements", entitlements, app)
    run("codesign", "--verify", "--deep", "--strict", "--verbose=2", app)


def smoke(app: Path, output: Path) -> dict:
    result = {"scope": "Owned copied no-ROM app, private portable profile and offscreen Qt; fresh default-off and configured automatic loopback discovery. Not retail gameplay, GPU performance or normal shutdown.", "passed": False, "modes": []}
    # Never interfere with another program's listener, even on a CI host.
    for port in range(7065, 7081):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(("127.0.0.1", port))
    for automatic in (False, True):
        label = "automatic" if automatic else "default-off"
        mode = {"name": label, "passed": False}
        result["modes"].append(mode)
        with tempfile.TemporaryDirectory(prefix="owned-smoke-", dir=output) as temporary:
            root = Path(temporary)
            copy = root / "melonDS.app"
            shutil.copytree(app, copy, symlinks=True)
            portable = root / "portable"
            portable.mkdir()
            config = "PauseLostFocus = false\nRecentROM = []\n[Instance0]\n"
            if automatic:
                config += "[Instance0.WiiDS]\nAutomatic = true\nEnabled = false\nDiagnostics = false\n"
            config += "[Instance0.Window0]\nEnabled = true\n"
            (portable / "melonDS.toml").write_text(config)
            (root / "home").mkdir()
            (root / "tmp").mkdir()
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith(("MELONDS_WIIDS_", "QT_", "QML", "DYLD_"))}
            env.update(HOME=str(root / "home"), TMPDIR=str(root / "tmp"),
                       QT_QPA_PLATFORM="offscreen", QT_MULTIMEDIA_PREFERRED_PLUGINS="darwin")
            with (output / f"smoke-{label}-stdout.log").open("w") as stdout, (output / f"smoke-{label}-stderr.log").open("w") as stderr:
                child = subprocess.Popen([str(copy / "Contents/MacOS/melonDS"), "--boot", "never"],
                                         cwd=root, env=env, stdout=stdout, stderr=stderr)
                try:
                    replies: dict[int, str] = {}
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer:
                        peer.bind(("127.0.0.1", 0))
                        peer.settimeout(0.1)
                        deadline = time.monotonic() + 6
                        while time.monotonic() < deadline:
                            if child.poll() is not None:
                                raise RuntimeError(f"Copied melonDS app exited in {label}: {child.returncode}")
                            for port in range(7065, 7081):
                                peer.sendto(b"WIIDSBRIDGE/1 DOLPHIN HELLO", ("127.0.0.1", port))
                            try:
                                response, address = peer.recvfrom(256)
                            except (socket.timeout, ConnectionRefusedError):
                                continue
                            text = response.decode("ascii", errors="strict")
                            if address[0] != "127.0.0.1" or not re.fullmatch(r"WIIDSBRIDGE/1 MELONDS ACK [0-9A-F]{16} M2", text):
                                raise RuntimeError("Unexpected private frontend discovery response")
                            if address[1] in replies and replies[address[1]] != text:
                                raise RuntimeError("Repeated discovery changed a live socket identity")
                            replies[address[1]] = text
                    if len(replies) != (1 if automatic else 0):
                        raise RuntimeError(f"{label}: unexpected number of frontend radio listeners: {len(replies)}")
                    mode.update(aliveForSeconds=6, acknowledgedRadioPorts=sorted(replies), passed=True)
                finally:
                    if child.poll() is None:
                        child.terminate()
                        try:
                            child.wait(timeout=8)
                        except subprocess.TimeoutExpired:
                            child.kill()
                            child.wait(timeout=5)
                            mode["ownedFixtureForcedCleanup"] = True
                    mode["cleanupExitCode"] = child.returncode
    result["passed"] = True
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("app", "qt-root", "source", "output", "entitlements"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--arch", choices=("x86_64", "arm64"), required=True)
    parser.add_argument("--minimum-os", default="15.0")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    app = output / "melonDS.app"
    if app.exists():
        raise RuntimeError("Refusing to replace an existing packaged app")
    shutil.copytree(args.app, app, symlinks=True)
    run(args.qt_root / "bin/macdeployqt", app, "-always-overwrite", "-verbose=2")
    # macdeployqt normally deploys Cocoa only. Keep an offscreen platform
    # plugin for the owned no-ROM smoke test without relying on the SDK.
    closure = Closure(app, args.qt_root, args.arch)
    for relative in ("platforms/libqoffscreen.dylib", "imageformats/libqsvg.dylib"):
        original = args.qt_root / "plugins" / relative
        if not original.is_file():
            raise RuntimeError(f"Qt SDK lacks required plugin: {relative}")
        target = app / "Contents/PlugIns" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, target)
        closure.origins[target.resolve()] = original.resolve()
    if not (app / "Contents/PlugIns/platforms/libqcocoa.dylib").is_file():
        raise RuntimeError("macdeployqt did not deploy the native Cocoa platform plugin")
    closure.apply()
    plist_path = app / "Contents/Info.plist"
    with plist_path.open("rb") as handle:
        info = plistlib.load(handle)
    info["LSMinimumSystemVersion"] = args.minimum_os
    info["NSLocalNetworkUsageDescription"] = "Connect to the companion Dolphin Wii emulator on this Mac."
    with plist_path.open("wb") as handle:
        plistlib.dump(info, handle)
    sign(app, args.entitlements)
    report = smoke(app, output)
    (output / "SMOKE-TEST.json").write_text(json.dumps(report, indent=2) + "\n")
    revision = run("git", "-C", args.source, "rev-parse", "HEAD")
    executable = app / "Contents/MacOS/melonDS"
    build = {
        "component": "melonDS", "architecture": args.arch,
        "requestedMinimumMacOS": args.minimum_os,
        "sourceRevision": revision,
        "executableSHA256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "codeSigning": "ad-hoc, allow-jit entitlement; not Developer ID signed or notarized",
        "retailGameplayVerified": False,
        "nativeHostMacOS": run("sw_vers", "-productVersion"),
        "qtVersion": run(args.qt_root / "bin/qmake", "-query", "QT_VERSION"),
        "dependencyClosure": "Qt deployed with macdeployqt, recursive non-system dependencies copied and rewritten into bundle",
        "smokePassed": report["passed"],
    }
    (output / "BUILD-INFO.json").write_text(json.dumps(build, indent=2) + "\n")


if __name__ == "__main__":
    main()
