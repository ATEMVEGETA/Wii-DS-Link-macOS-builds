#!/usr/bin/env python3
"""Package verified native Mac apps; no encryption or network upload here."""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess

PINS = {
    "melonDS": "60a07cca86189ac8b9e0cd1cc6516f575ed60ca3",
    "Dolphin": "b24e2427a0ab5aec6102f3b5014d581699b9b4a4",
}
DEPENDENCIES = ("sdl2", "libarchive", "zstd", "faad2", "enet")
NOTICE_NAMES = ("LICENSE*", "LICENCE*", "COPYING*", "COPYRIGHT*", "NOTICE*", "PATENTS*")
FORBIDDEN_NAMES = {
    "melonds.toml", "dolphin.ini", "wiidsbridge.ini", "wiimotenew.ini",
    "gcpadnew.ini", "bios7.bin", "bios9.bin", "firmware.bin", "nand.bin",
    "keys.bin", "rfl_db.dat", "wii-ds-bridge.log", "wfcsettings.bin",
}
FORBIDDEN_SUFFIXES = {".nds", ".srl", ".sav", ".dsv", ".wad", ".iso", ".rvz", ".wbfs", ".pcap", ".pcapng", ".mii"}


def run(*args: str | Path) -> str:
    return subprocess.check_output([str(arg) for arg in args], text=True).strip()


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def read_passed(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("passed") is not True:
        raise RuntimeError(f"Required audit/smoke did not pass: {path.name}")
    return value


def copy_tree(source: Path, destination: Path) -> None:
    if destination.exists():
        raise RuntimeError(f"Refusing to replace existing output: {destination.name}")
    shutil.copytree(source, destination, symlinks=True)


def privacy_check(root: Path) -> None:
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if path.is_symlink():
            if not path.resolve(strict=True).is_relative_to(root.resolve()):
                raise RuntimeError(f"Escaping bundle symlink: {relative}")
            continue
        if not path.is_file():
            continue
        name = path.name.lower()
        if name in FORBIDDEN_NAMES or path.suffix.lower() in FORBIDDEN_SUFFIXES or ".sav." in name:
            raise RuntimeError(f"Unexpected personal/game file in package: {relative}")
        if any(part.lower() in ("privateuser", "privatehome", "facelib") for part in relative.parts):
            raise RuntimeError(f"Private smoke profile found in package: {relative}")


def collect_homebrew_notices(destination: Path) -> dict:
    names = set(DEPENDENCIES)
    # brew deps is recursive by default; modern Homebrew rejects --recursive.
    names.update(run("brew", "deps", "--installed", *DEPENDENCIES).splitlines())
    report = {}
    destination.mkdir(parents=True)
    for name in sorted(filter(None, names)):
        prefix = Path(run("brew", "--prefix", name)).resolve()
        copied = 0
        for path in prefix.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(prefix)
            if len(relative.parts) > 7:
                continue
            if not any(fnmatch.fnmatch(path.name.upper(), pattern) for pattern in NOTICE_NAMES):
                continue
            target = destination / name / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            copied += 1
        report[name] = {"version": run("brew", "list", "--versions", name), "noticeFiles": copied}
    (destination / "DEPENDENCIES.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def collect_component_notices(workspace: Path, component: str, destination: Path) -> None:
    source = workspace / "sources" / ("melonDS" if component == "melonDS" else "dolphin")
    destination.mkdir(parents=True)
    if component == "Dolphin":
        supplied = workspace / "out/dolphin/LICENSES-Dolphin"
        if not supplied.is_dir():
            raise RuntimeError("Dolphin notices are missing")
        copy_tree(supplied, destination / "upstream")
        return
    shutil.copy2(source / "LICENSE", destination / "LICENSE-melonDS")
    notices = {
        "teakra": "src/teakra/LICENSE",
        "FatFs": "src/fatfs/LICENSE.txt",
        "blip-buf": "src/blip-buf/license.txt",
        "Dolphin-vendored": "src/dolphin/license_dolphin.txt",
        "tiny-AES": "src/tiny-AES-c/unlicense.txt",
        "InputConfig-illustrations.md": "src/frontend/qt_sdl/InputConfig/resources/LICENSE.md",
        "xxhash.h": "src/xxhash/xxhash.h",
        "toml.hpp": "src/frontend/qt_sdl/toml/toml.hpp",
    }
    for label, relative in notices.items():
        shutil.copy2(source / relative, destination / label)


def component_state(workspace: Path, component: str, architecture: str) -> dict:
    folder = workspace / "out" / ("melonDS" if component == "melonDS" else "dolphin")
    if component == "melonDS":
        audit = read_passed(folder / "BUNDLE-AUDIT.json")
        smoke = read_passed(folder / "SMOKE-TEST.json")
        build = json.loads((folder / "BUILD-INFO.json").read_text(encoding="utf-8"))
        executable = folder / "melonDS.app/Contents/MacOS/melonDS"
    else:
        audit = read_passed(folder / "DOLPHIN-BUNDLE-AUDIT.json")
        smoke = read_passed(folder / "smoke-report/report.json")
        executable = folder / "Dolphin.app/Contents/MacOS/DolphinQt"
        build = {}
    if audit.get("architecture") != architecture:
        raise RuntimeError(f"{component} audit architecture mismatch")
    source = workspace / "sources" / ("melonDS" if component == "melonDS" else "dolphin")
    revision = run("git", "-C", source, "rev-parse", "HEAD")
    if revision != PINS[component]:
        raise RuntimeError(f"{component} source revision mismatch")
    return {
        "sourceCommit": revision,
        "executableSHA256": digest(executable),
        "bundleAuditPassed": audit["passed"],
        "startupSmokePassed": smoke["passed"],
        "gameplayVerified": False,
        "sourceBuildInfo": build,
    }


def inventory(root: Path) -> list[dict]:
    result = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            result.append({"path": relative, "type": "symlink", "target": os.readlink(path)})
        elif path.is_file():
            result.append({"path": relative, "type": "file", "bytes": path.stat().st_size, "sha256": digest(path)})
    return result


def create_package(args: argparse.Namespace, components: list[str], notices: Path, states: dict, downloads: Path) -> Path:
    label = "Wii-DS-Link" if len(components) == 2 else components[0] + "-WiiDS-Link"
    name = f"{label}-macOS-{args.architecture}-experimental"
    stage = args.workspace / "out/package-stage" / name
    if stage.exists():
        raise RuntimeError(f"Package staging already exists: {name}")
    stage.mkdir(parents=True)
    for component in components:
        folder = "melonDS" if component == "melonDS" else "Dolphin"
        target = stage / folder
        target.mkdir()
        app_name = "melonDS.app" if component == "melonDS" else "Dolphin.app"
        app = args.workspace / "out" / ("melonDS" if component == "melonDS" else "dolphin") / app_name
        copy_tree(app, target / app_name)
        if component == "melonDS":
            (target / "portable").mkdir()
        else:
            (target / "portable.txt").touch()
    shutil.copy2(args.workspace / "TESTING.md", stage / "TESTING.md")
    copy_tree(notices, stage / "LICENSES")
    scripts = stage / "BUILD-SCRIPTS"
    scripts.mkdir()
    for component in components:
        helper = "melonDS" if component == "melonDS" else "dolphin"
        source_helpers = args.workspace / "scripts" / helper
        copy_tree(source_helpers, scripts / helper)
    if "Dolphin" in components and "melonDS" not in components:
        (scripts / "melonDS").mkdir()
        shutil.copy2(args.workspace / "scripts/melonDS/deploy.py", scripts / "melonDS/deploy.py")
    shutil.copy2(Path(__file__), scripts / "package.py")
    info = {
        "label": name,
        "experimental": True,
        "architecture": args.architecture,
        "minimumMacOS": args.minimum_os,
        "qtVersion": "6.8.3",
        "codeSigning": "Ad-hoc signed; not Developer ID signed/notarized.",
        "components": {component: states[component] for component in components},
        "retailGameplayVerified": False,
        "recordingPerformanceVerified": False,
        "limitations": [
            "Original upstream Intel macOS melonDS JIT is disabled.",
            "Dolphin uses Metal/OpenGL; Vulkan, built-in FFmpeg dumping and updater disabled.",
            "Same-Mac loopback transport only; no physical-console bridge.",
        ],
    }
    (stage / "BUILD-INFO.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    privacy_check(stage)
    (stage / "MANIFEST.json").write_text(json.dumps(inventory(stage), indent=2) + "\n", encoding="utf-8")
    sums = [f"{digest(path)}  {path.relative_to(stage).as_posix()}" for path in sorted(stage.rglob("*")) if path.is_file() and not path.is_symlink()]
    (stage / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")
    archive = downloads / (name + ".zip")
    if archive.exists():
        raise RuntimeError(f"Refusing to replace existing archive: {archive.name}")
    run("/usr/bin/ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", stage, archive)
    return archive


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--architecture", choices=("arm64", "x86_64"), required=True)
    parser.add_argument("--component", choices=("both", "melonDS", "Dolphin"), required=True)
    parser.add_argument("--minimum-os", default="15.0")
    parser.add_argument("--qt-root", type=Path, required=True)
    args = parser.parse_args()
    args.workspace = args.workspace.resolve(strict=True)
    if platform.system() != "Darwin" or platform.machine() != args.architecture:
        raise RuntimeError("Packaging must run on the matching native Mac runner")
    if run(args.qt_root / "bin/qmake", "-query", "QT_VERSION") != "6.8.3":
        raise RuntimeError("The pinned Qt SDK version is required")
    components = ["melonDS", "Dolphin"] if args.component == "both" else [args.component]
    states = {component: component_state(args.workspace, component, args.architecture) for component in components}
    notices = args.workspace / "out/package-notices"
    if notices.exists():
        raise RuntimeError("Refusing to replace existing notices staging")
    shared = args.workspace / "scripts/licenses"
    if not shared.is_dir() or not any(shared.rglob("*")):
        raise RuntimeError("Missing pre-collected Qt dependency/license notices")
    copy_tree(shared, notices / "shared")
    for component in components:
        collect_component_notices(args.workspace, component, notices / component)
    collect_homebrew_notices(notices / "Homebrew")
    downloads = args.workspace / "out/downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    archives = [create_package(args, [component], notices, states, downloads) for component in components]
    if len(components) == 2:
        archives.append(create_package(args, components, notices, states, downloads))
    checksums = [f"{digest(path)}  {path.name}" for path in archives]
    (downloads / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    print("MACOS_PACKAGES_READY", *(path.name for path in archives))


if __name__ == "__main__":
    main()
