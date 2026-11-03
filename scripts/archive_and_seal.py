#!/usr/bin/env python3
"""Collect allowlisted outputs and encrypt them; never upload plaintext."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile


def preserve_recovery_apps(workspace: Path, architecture: str) -> None:
    # A successful compile must survive unrelated license/ZIP assembly errors.
    # Raw recovery apps are NEVER release-ready or uploaded in plaintext.
    # ditto preserves Mac links/permissions inside ZIPs kept closed on Windows.
    if (workspace / "out/downloads/SHA256SUMS.txt").is_file():
        return
    recovery = workspace / "out/recovery"
    for folder, app_name in (("melonDS", "melonDS.app"), ("dolphin", "Dolphin.app")):
        app = workspace / "out" / folder / app_name
        if not app.is_dir():
            continue
        recovery.mkdir(parents=True, exist_ok=True)
        target = recovery / (folder + "-" + architecture + "-recovery-only.zip")
        if target.exists():
            raise RuntimeError("Refusing to replace a recovery app archive")
        subprocess.run(["/usr/bin/ditto", "-c", "-k", "--sequesterRsrc",
                        "--keepParent", str(app), str(target)], check=True)
        value = hashlib.sha256()
        with target.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                value.update(block)
        metadata = {"component": folder, "architecture": architecture,
                    "experimentalRecoveryOnly": True, "releaseReady": False,
                    "sha256": value.hexdigest()}
        target.with_suffix(".json").write_text(json.dumps(metadata, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--architecture", choices=("arm64", "x86_64"), required=True)
    parser.add_argument("--component", choices=("both", "melonDS", "Dolphin"), required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    try:
        preserve_recovery_apps(workspace, args.architecture)
    except Exception as error:
        # Preserve diagnostic logs even if the recovery ZIP itself cannot be
        # created (for example, a runner disk-space failure).
        output = workspace / "out"
        output.mkdir(parents=True, exist_ok=True)
        (output / "recovery-error.log").write_text(str(error) + "\n")
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
            if relative.parts[0] in ("downloads", "recovery") or path.suffix in (".json", ".log", ".txt", ".diff", ".plist"):
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
