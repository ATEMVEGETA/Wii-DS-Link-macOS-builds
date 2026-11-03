#!/usr/bin/env python3
"""Collect allowlisted outputs and encrypt them; never upload plaintext."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tarfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--architecture", choices=("arm64", "x86_64"), required=True)
    parser.add_argument("--component", choices=("both", "melonDS", "Dolphin"), required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    sealed = workspace / "sealed"
    sealed.mkdir(exist_ok=True)
    name = f"macos-{args.architecture}-{args.component}-{os.environ.get('GITHUB_RUN_ID', 'local')}-{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}"
    archive = sealed / (name + ".tar")
    if archive.exists():
        raise RuntimeError("Do not replace an existing result archive")
    selected = []
    output = workspace / "out"
    if output.is_dir():
        for path in sorted(output.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(output)
            if relative.parts[0] == "downloads" or path.suffix in (".json", ".log", ".txt", ".diff", ".plist"):
                # Do not archive compiled apps, arbitrary build intermediates
                # or binaries from a failed no-ROM test.
                if ".app" not in str(relative):
                    selected.append(path)
    with tarfile.open(archive, "w") as tar:
        for path in selected:
            tar.add(path, arcname=path.relative_to(workspace), recursive=False)
    target = sealed / (name + ".wdsenc")
    subprocess.run([sys.executable, str(workspace / "scripts/seal.py"), "encrypt",
                    "--public", str(workspace / "keys/build-public.pem"),
                    "--input", str(archive), "--output", str(target)], check=True)
    archive.unlink()
    print(f"Encrypted result files={len(selected)} archive={target.name}")


if __name__ == "__main__":
    main()
