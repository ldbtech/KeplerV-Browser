#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/.."

build_python="${KEPLERV_BUILD_PYTHON:-.venv/bin/python}"
if [[ ! -x "$build_python" ]]; then
  build_python="$(command -v python3.12 || true)"
fi
if [[ -z "$build_python" || ! -x "$build_python" ]]; then
  echo "Python 3.12 is required to build KeplerV. Create .venv with Python 3.12 or set KEPLERV_BUILD_PYTHON." >&2
  exit 1
fi

env -u APPLE_SIGNING_IDENTITY KEPLERV_BUILD_PYTHON="$build_python" "$build_python" scripts/package-runtime.py

if [[ -n "${APPLE_SIGNING_IDENTITY:-}" ]]; then
  "$build_python" scripts/sign-runtime-macos.py "$APPLE_SIGNING_IDENTITY"
  HOME="$PWD/.packaging-home" PLAYWRIGHT_BROWSERS_PATH="$PWD/src-tauri/runtime-bundle/ms-playwright" \
    src-tauri/runtime-bundle/KeplerVRuntime/KeplerVRuntime --self-test-browser
  config_dir="$(mktemp -d /tmp/keplerv-tauri-config.XXXXXXXXXX)"
  config_file="$config_dir/tauri.conf.json"
  trap 'rm -rf "$config_dir"' EXIT
  python3 - "$config_file" "$APPLE_SIGNING_IDENTITY" <<'PY'
import json
import sys
with open(sys.argv[1], "w", encoding="utf-8") as out:
    json.dump({"bundle": {"macOS": {"signingIdentity": sys.argv[2]}}}, out)
PY
  npx tauri build --bundles app --config "$config_file"
else
  echo "Building unsigned. Set APPLE_SIGNING_IDENTITY to your Developer ID Application certificate name to sign." >&2
  npx tauri build --bundles app
fi

app_bundle="src-tauri/target/release/bundle/macos/KeplerV.app"
if [[ ! -d "$app_bundle" ]]; then
  echo "Tauri did not create the KeplerV.app bundle." >&2
  exit 1
fi

architecture="$(uname -m)"
case "$architecture" in
  arm64) architecture="aarch64" ;;
  x86_64) architecture="x64" ;;
esac
version="$(node -p 'JSON.parse(require("fs").readFileSync("package.json", "utf8")).version')"
dmg_dir="src-tauri/target/release/bundle/dmg"
dmg_path="$dmg_dir/KeplerV_${version}_${architecture}.dmg"
stage="$(mktemp -d /tmp/keplerv-dmg.XXXXXX)"
trap 'rm -rf "$stage"' EXIT
mkdir -p "$dmg_dir"
cp -R "$app_bundle" "$stage/KeplerV.app"
ln -s /Applications "$stage/Applications"
rm -f "$dmg_path"
hdiutil create -quiet -volname "KeplerV Installer" -srcfolder "$stage" -ov -format UDZO "$dmg_path"
echo "DMG output: $dmg_path"
