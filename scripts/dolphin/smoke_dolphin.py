#!/usr/bin/env python3
"""Ten-second startup of an owned copied Dolphin app with private no-ROM data.

This confirms dyld/plugin startup on the hosted Mac, not retail gameplay,
GPU/JIT emulation, save correctness, or human Gatekeeper interaction.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import tempfile
import time


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=pathlib.Path)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    app = args.bundle.resolve(strict=True)
    executable = app / "Contents" / "MacOS" / "DolphinQt"
    if app.suffix != ".app" or not executable.is_file():
        parser.error("Expected Dolphin.app containing DolphinQt")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {
        "scope": "Owned copied macOS app, private -u profile, no ROM, ten-second launch/dyld/plugin smoke only; not a retail/GPU/JIT/normal-shutdown test.",
        "executableSHA256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "startupSeconds": 10,
        "passed": False,
    }
    process = None
    try:
        with tempfile.TemporaryDirectory(prefix="private-no-rom-", dir=output) as scratch:
            scratch = pathlib.Path(scratch)
            copied = scratch / "Dolphin.app"
            shutil.copytree(app, copied, symlinks=True)
            user = scratch / "PrivateUser"
            config = user / "Config"
            config.mkdir(parents=True)
            (config / "Dolphin.ini").write_text(
                "[Analytics]\nEnabled = False\nPermissionAsked = True\n[General]\nISOPaths = 0\n", encoding="utf-8"
            )
            (config / "WiiDSBridge.ini").write_text(
                "[WiiDSBridge]\nEnabled = False\nDiagnostics = False\n", encoding="utf-8"
            )
            home = scratch / "PrivateHome"
            home.mkdir()
            environment = os.environ.copy()
            for name in tuple(environment):
                if name.startswith(("QT_", "QML", "DYLD_", "MELONDS_WIIDS_")) or name == "DOLPHIN_EMU_USERPATH":
                    environment.pop(name, None)
            environment["HOME"] = str(home)
            environment["CFFIXED_USER_HOME"] = str(home)
            environment["QT_QPA_PLATFORM"] = "cocoa"
            environment["QT_MAC_DISABLE_FOREGROUND_APPLICATION_TRANSFORM"] = "1"
            environment["XDG_CONFIG_HOME"] = str(home / ".config")
            with (output / "stdout.log").open("wb") as stdout, (output / "stderr.log").open("wb") as stderr:
                process = subprocess.Popen(
                    [str(copied / "Contents" / "MacOS" / "DolphinQt"), "-u", str(user)],
                    cwd=scratch, env=environment, stdout=stdout, stderr=stderr,
                    start_new_session=True,
                )
                report["ownedPid"] = process.pid
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError(f"No-ROM app exited early with status {process.returncode}")
                    time.sleep(0.2)
                report["stayedAliveTenSeconds"] = True
                # Only this Popen object's PID is signalled; no process search
                # or shared-machine kill is used. No game/save was loaded.
                process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    report["forcedOwnedFixtureCleanup"] = True
                    process.kill()
                    process.wait(timeout=5)
                report["cleanupReturnCode"] = process.returncode
            errors = (output / "stderr.log").read_text(encoding="utf-8", errors="replace")
            if re.search(r"Library not loaded:|dyld\[\d+\]:|Could not load the Qt platform plugin", errors):
                raise RuntimeError("Dynamic-loader or Qt platform-plugin failure in owned app stderr")
            if (user / "Logs" / "wii-ds-bridge.log").exists():
                raise RuntimeError("Diagnostics-off smoke unexpectedly wrote a bridge log")
            report["passed"] = True
    except Exception as error:
        report["error"] = str(error)
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"DOLPHIN_MACOS_SMOKE passed={report['passed']}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
