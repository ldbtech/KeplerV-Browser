#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/.."
: "${APPLE_NOTARY_PROFILE:?Set APPLE_NOTARY_PROFILE to the notarytool Keychain profile name first.}"
if [[ -z "${APPLE_SIGNING_IDENTITY:-}" ]]; then
  echo "Set APPLE_SIGNING_IDENTITY to your Developer ID Application certificate name first." >&2
  exit 1
fi

npm run desktop:build
shopt -s nullglob
images=(src-tauri/target/release/bundle/dmg/*.dmg)
if [[ ${#images[@]} -ne 1 ]]; then
  echo "Expected one DMG in src-tauri/target/release/bundle/dmg; found ${#images[@]}." >&2
  exit 1
fi
xcrun notarytool submit "${images[0]}" --keychain-profile "$APPLE_NOTARY_PROFILE" --wait
xcrun stapler staple "${images[0]}"
echo "Notarized and stapled: ${images[0]}"
