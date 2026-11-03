#!/usr/bin/env python3
"""Read-only architecture, dependency and minimum-macOS audit of an app.

Reject absolute non-system dylib load commands, wrong Mach-O slices, escaped
symlinks, and a dependency requiring newer macOS than advertised. This does not
certify JIT, GPU, gameplay, Gatekeeper/notarization or retail Wii-DS behavior.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import plistlib
import re
import subprocess
import sys


MACHO_MAGICS = frozenset(bytes.fromhex(value) for value in (
    "feedface", "cefaedfe",  # 32-bit Mach-O, both byte orders
    "feedfacf", "cffaedfe",  # 64-bit Mach-O, both byte orders
    "cafebabe", "bebafeca",  # 32-bit fat header, both byte orders
    "cafebabf", "bfbafeca",  # 64-bit fat header, both byte orders
))


def is_macho_candidate(path: pathlib.Path) -> bool:
    # The magic is a cheap prefilter, not a substitute for file validation.
    with path.open("rb") as stream:
        return stream.read(4) in MACHO_MAGICS


def run(*arguments: str) -> str:
    return subprocess.check_output(arguments, text=True, stderr=subprocess.STDOUT)


def version_tuple(value: str) -> tuple[int, ...]:
    parts = tuple(int(part) for part in value.split("."))
    return (parts + (0, 0, 0))[:3]


def system_path(value: str) -> bool:
    return value in ("/usr/lib", "/System/Library") or value.startswith(
        ("/usr/lib/", "/System/Library/")
    )


def parse_dependencies(output: str) -> list[str]:
    # Ignore per-architecture image headers emitted for universal SDK files.
    return list(dict.fromkeys(
        match.group(1) for line in output.splitlines()
        if (match := re.match(r"^\s+(.+?)\s+\(compatibility version", line))
    ))


def parse_rpaths(commands: str) -> list[str]:
    lines = commands.splitlines()
    result = []
    for index, line in enumerate(lines):
        if line.strip() != "cmd LC_RPATH":
            continue
        for following in lines[index + 1:index + 5]:
            match = re.match(r"\s*path (.*?) \(offset \d+\)", following)
            if match:
                result.append(match.group(1))
                break
    return list(dict.fromkeys(result))


def expand_path(value: str, image: pathlib.Path, executable: pathlib.Path):
    for token, root in (
        ("@loader_path", image.parent),
        ("@executable_path", executable.parent),
    ):
        if value == token or value.startswith(token + "/"):
            return (root / value[len(token):].lstrip("/")).resolve()
    if value.startswith("/"):
        return pathlib.Path(value).resolve()
    return None


def audit_load_paths(
    app: pathlib.Path, image: pathlib.Path, executable: pathlib.Path,
    dependencies: list[str], image_rpaths: list[str], executable_rpaths: list[str],
) -> tuple[list[dict], list[str]]:
    app = app.resolve()
    errors = []
    resolved = []
    relative = str(image.resolve().relative_to(app))
    for value in image_rpaths:
        target = expand_path(value, image, executable)
        if target is None:
            errors.append(f"Unsupported/non-portable rpath: {relative}: {value}")
        elif not target.is_relative_to(app) and not system_path(str(target)):
            errors.append(f"External rpath: {relative}: {value}")

    # dyld searches the image's runpaths and the main executable's inherited
    # runpaths. Do not invent an implicit Frameworks fallback: a dependency
    # must resolve through an actual load command to a file inside this app.
    search = []
    for owner, values in ((image, image_rpaths), (executable, executable_rpaths)):
        for value in values:
            target = expand_path(value, owner, executable)
            if target is not None and target.is_relative_to(app) and target not in search:
                search.append(target)
    for dependency in dependencies:
        if system_path(dependency):
            resolved.append({"load": dependency, "system": True})
            continue
        if dependency.startswith("/"):
            errors.append(f"Non-portable absolute load: {relative}: {dependency}")
            continue
        if dependency.startswith("@rpath/"):
            suffix = dependency[len("@rpath/"):]
            candidates = [root / suffix for root in search]
        else:
            target = expand_path(dependency, image, executable)
            candidates = [target] if target is not None else []
        found = None
        for candidate in candidates:
            target = candidate.resolve()
            if target.is_relative_to(app) and target.is_file():
                found = target
                break
        if found is None:
            errors.append(f"Unresolved/outside bundle load: {relative}: {dependency}")
        else:
            resolved.append({"load": dependency, "path": str(found.relative_to(app))})
    return resolved, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=pathlib.Path)
    parser.add_argument("--arch", choices=("arm64", "x86_64"), required=True)
    parser.add_argument("--minimum", required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    app = args.bundle.resolve(strict=True)
    if app.suffix != ".app":
        parser.error("bundle must be an .app directory")
    report = {
        "scope": "Read-only native Mach-O dependency/minimum-OS audit; no game or user profile loaded.",
        "architecture": args.arch,
        "advertisedMinimumMacOS": args.minimum,
        "files": [],
        "errors": [],
        "passed": False,
    }
    with (app / "Contents" / "Info.plist").open("rb") as stream:
        plist = plistlib.load(stream)
    executable = app / "Contents/MacOS" / str(plist["CFBundleExecutable"])
    executable_commands = run("/usr/bin/otool", "-arch", args.arch, "-l", str(executable))
    executable_rpaths = parse_rpaths(executable_commands)
    report["bundleIdentifier"] = plist.get("CFBundleIdentifier")
    report["plistMinimumMacOS"] = plist.get("LSMinimumSystemVersion")
    if version_tuple(str(plist.get("LSMinimumSystemVersion", "0"))) != version_tuple(args.minimum):
        report["errors"].append("Info.plist minimum differs from advertised target")
    seen: set[pathlib.Path] = set()
    for item in sorted(app.rglob("*")):
        real = item.resolve()
        if item.is_symlink() and not item.exists():
            report["errors"].append(f"Dangling bundle symlink: {item.relative_to(app)}")
            continue
        if not real.is_relative_to(app):
            report["errors"].append(f"Bundle symlink escapes the app: {item.relative_to(app)}")
            continue
        if not item.is_file() or real in seen:
            continue
        seen.add(real)
        if not is_macho_candidate(real):
            continue
        kind = run("/usr/bin/file", "-b", str(real))
        if "Mach-O" not in kind:
            continue
        relative = str(real.relative_to(app))
        arches = run("/usr/bin/lipo", "-archs", str(real)).strip().split()
        if args.arch not in arches:
            report["errors"].append(f"Wrong architecture: {relative}: {arches}")
        loads = run("/usr/bin/otool", "-arch", args.arch, "-L", str(real))
        dependencies = []
        for line in loads.splitlines():
            # Universal SDK frameworks may print a per-slice image header.
            # Only actual dependency/ID records carry compatibility metadata.
            match = re.match(r"^\s+(.+?)\s+\(compatibility version", line)
            if not match:
                continue
            dependency = match.group(1)
            dependencies.append(dependency)
            if dependency.startswith("/") and not dependency.startswith(("/usr/lib/", "/System/Library/")):
                report["errors"].append(f"Non-portable absolute load: {relative}: {dependency}")
        commands = run("/usr/bin/otool", "-arch", args.arch, "-l", str(real))
        resolved, path_errors = audit_load_paths(
            app, real, executable, dependencies, parse_rpaths(commands), executable_rpaths
        )
        report["errors"].extend(path_errors)
        minima = re.findall(r"\bminos\s+(\d+(?:\.\d+){0,2})", commands)
        minima += re.findall(r"cmd LC_VERSION_MIN_MACOSX\s+cmdsize\s+\d+\s+version\s+(\d+(?:\.\d+){0,2})", commands)
        if not minima:
            report["errors"].append(f"No minimum macOS load command: {relative}")
        for minimum in minima:
            if version_tuple(minimum) > version_tuple(args.minimum):
                report["errors"].append(f"Newer minimum OS: {relative}: {minimum}")
        report["files"].append({"path": relative, "architectures": arches, "minimumMacOS": minima, "dependencies": dependencies, "resolvedDependencies": resolved})
    if not report["files"]:
        report["errors"].append("No Mach-O files found")
    report["passed"] = not report["errors"]
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for error in report["errors"]:
        print(error, file=sys.stderr)
    print(f"BUNDLE_AUDIT passed={report['passed']} machOFiles={len(report['files'])}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
