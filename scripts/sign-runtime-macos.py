#!/usr/bin/env python3
"""Developer ID sign all Mach-O code embedded in the packaged runtime."""
from __future__ import annotations

import subprocess
import sys
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "src-tauri" / "runtime-bundle"
CHROMIUM_ENTITLEMENTS = ROOT / "scripts" / "playwright-runtime.entitlements.plist"


def is_macho(path: Path) -> bool:
    result = subprocess.run(["/usr/bin/file", "-b", str(path)], capture_output=True, text=True)
    return result.returncode == 0 and "Mach-O" in result.stdout


def sign(path: Path, identity: str, entitlements: Path | None = None) -> None:
    command = ["/usr/bin/codesign", "--force", "--options", "runtime", "--timestamp", "--sign", identity]
    if entitlements:
        command.extend(["--entitlements", str(entitlements)])
    command.append(str(path))
    subprocess.run(command, check=True)


def main() -> None:
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        raise SystemExit("Usage: sign-runtime-macos.py 'Developer ID Application: Name (TEAMID)'")
    identity = sys.argv[1]
    if not RUNTIME.is_dir():
        raise SystemExit(f"Packaged runtime is missing: {RUNTIME}")

    # Apple scans Mach-O symlink aliases as independent executables. The
    # Python.framework aliases were rejected even though their shared target
    # verified locally, so materialize those links as byte-identical files and
    # give each path its own Developer ID signature.
    for alias in sorted(RUNTIME.rglob("*")):
        if not alias.is_symlink() or not alias.is_file():
            continue
        target = alias.resolve()
        if is_macho(target):
            alias.unlink()
            shutil.copy2(target, alias)

    # Frameworks and PyInstaller expose some binaries through symlink aliases.
    # Directory symlinks remain links; binary aliases above are now independent
    # files so each can carry a signature that Apple's scanner can validate.
    binaries = sorted(
        (path for path in RUNTIME.rglob("*") if not path.is_symlink() and path.is_file() and is_macho(path)),
        key=lambda p: len(p.parts),
        reverse=True,
    )
    if not binaries:
        raise SystemExit(f"No Mach-O binaries found under {RUNTIME}")

    for binary in binaries:
        entitlements = CHROMIUM_ENTITLEMENTS if binary.name in {"chrome-headless-shell", "Chromium", "Google Chrome for Testing"} else None
        print(f"Developer ID signing runtime binary: {binary.relative_to(RUNTIME)}", flush=True)
        sign(binary, identity, entitlements)

    # Sign enclosing code bundles after their contained executables and libraries.
    bundles = sorted(
        (path for path in RUNTIME.rglob("*") if path.is_dir() and path.suffix in {".framework", ".app", ".xpc", ".bundle"}),
        key=lambda p: len(p.parts),
        reverse=True,
    )
    for bundle in bundles:
        print(f"Developer ID signing runtime bundle: {bundle.relative_to(RUNTIME)}", flush=True)
        sign(bundle, identity)

    print(f"Signed {len(binaries)} runtime binaries and {len(bundles)} nested bundles.")


if __name__ == "__main__":
    main()
