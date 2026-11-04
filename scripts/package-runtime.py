from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILD_ENV = ROOT / ".packaging-venv"
OUTPUT = ROOT / "src-tauri" / "runtime-bundle"
DIST = OUTPUT / "KeplerVRuntime"
WORK = ROOT / ".packaging-work"
PYINSTALLER_CONFIG = ROOT / ".packaging-pyinstaller"
BROWSER_DIST = ROOT / ".packaging-playwright"


def run(args: list[str], *, env: dict[str, str] | None = None) -> None:
    subprocess.run(args, cwd=ROOT, env=env, check=True)


def main() -> None:
    if sys.platform != "darwin":
        raise SystemExit("The bundled runtime build currently requires macOS.")
    if sys.version_info[:2] != (3, 12):
        raise SystemExit(
            f"Use Python 3.12 to build the app runtime (found {platform.python_version()}). "
            "Set KEPLERV_BUILD_PYTHON to a Python 3.12 executable."
        )

    build_python = os.environ.get("KEPLERV_BUILD_PYTHON")
    if not BUILD_ENV.exists():
        run([build_python or sys.executable, "-m", "venv", str(BUILD_ENV)])
    python = BUILD_ENV / "bin" / "python"
    run([str(python), "-m", "pip", "install", "-r", "runtime/packaging-requirements.txt"])

    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    OUTPUT.mkdir(parents=True)
    pyinstaller_env = os.environ.copy()
    pyinstaller_env["PYINSTALLER_CONFIG_DIR"] = str(PYINSTALLER_CONFIG)
    pyinstaller_args = [
        str(python), "-m", "PyInstaller", "--noconfirm", "--clean",
        "--name", "KeplerVRuntime", "--distpath", str(OUTPUT), "--workpath", str(WORK),
        "--specpath", str(WORK), "--collect-all", "runtime", "--collect-all", "playwright",
        "--hidden-import", "runtime.api", "scripts/runtime_entry.py",
    ]
    signing_identity = os.environ.get("APPLE_SIGNING_IDENTITY")
    if signing_identity:
        pyinstaller_args.extend([
            "--codesign-identity", signing_identity,
            "--osx-entitlements-file", str(ROOT / "scripts" / "playwright-runtime.entitlements.plist"),
        ])
    run(pyinstaller_args, env=pyinstaller_env)

    browser_env = os.environ.copy()
    browser_env["PLAYWRIGHT_BROWSERS_PATH"] = str(BROWSER_DIST)
    if not BROWSER_DIST.exists():
        run([str(python), "-m", "playwright", "install", "chromium", "--only-shell"], env=browser_env)
    shutil.copytree(BROWSER_DIST, OUTPUT / "ms-playwright")
    expected = DIST / "KeplerVRuntime"
    if not expected.is_file():
        raise SystemExit(f"PyInstaller did not create the runtime executable: {expected}")
    if not (OUTPUT / "ms-playwright").is_dir():
        raise SystemExit("Playwright Chromium did not install into the app runtime bundle.")
    browser_env["HOME"] = str(ROOT / ".packaging-home")
    browser_env["PLAYWRIGHT_BROWSERS_PATH"] = str(OUTPUT / "ms-playwright")
    run([str(expected), "--self-test-browser"], env=browser_env)
    print(f"Bundled runtime ready for {platform.machine()}: {OUTPUT}")


if __name__ == "__main__":
    main()
