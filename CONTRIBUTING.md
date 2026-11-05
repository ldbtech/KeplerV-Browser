# Contributing to KeplerV

Thanks for taking a look at KeplerV. This guide explains how to run the app, where to make changes, how the agent runtime is connected, and how to produce a macOS installer.

KeplerV is an active prototype. Keep behavior and documentation honest about what is live, experimental, or fixture-based. The most important product rule is that agent actions go through AIP; a model response is never authorization by itself.

## Development setup

The complete desktop app currently targets macOS and needs Xcode Command Line Tools, Rust stable, Node.js 20.19+ or 22.12+, and Python 3.12.

```sh
git clone https://github.com/ldbtech/KeplerV-Browser.git
cd KeplerV-Browser
npm ci
python3.12 -m venv .venv
.venv/bin/python -m pip install -r runtime/requirements.txt
.venv/bin/python -m playwright install chromium
npm run desktop
```

Connect a model in **LLM providers** for live research. Alternatively, set `MODEL_API_KEY` in the launching shell. Without a connected model the workspace can still open, but live model-backed tasks will not run.

Useful development commands:

```sh
npm run dev                 # Frontend only; no native commands or Python runtime
npm run desktop             # Full Tauri app and local Python runtime
npm run build               # Frontend check and production build
```

## How the pieces fit together

```text
Svelte UI → Tauri/Rust commands → authenticated local Python API
                                      ↓
                              scheduler → AIP → tool/provider
                                      ↓
                              SQLite events and evidence → UI
```

- **Svelte UI** presents goals, research, approvals, Guard cases, settings, and learning material.
- **Tauri/Rust** owns the desktop window, narrow IPC commands, Keychain access, OAuth callbacks, and the Python sidecar lifecycle.
- **Python runtime** runs the API, scheduler, model adapters, research browser, AIP, services, Guard, and persistent local event store.
- **AIP** checks whether a specific agent may perform a requested action. Sensitive actions can require explicit approval.
- **Guard** records policy denials and selected risk signals for human review. Model-based assessments are advisory and must not grant capabilities or silently change AIP policy.

## Source map

### Frontend: `src/`

- `App.svelte` — main workspace, goal details, runtime status, approvals, Guard and learning-library panels.
- `main.ts`, `styles.css`, `types.ts` — app entry point, shared styling, and UI/runtime types.
- `experience/` — workspace data and views, research summaries, learning library, media surfaces, account resolution, Guard-related UI helpers, and their tests.
- `simulation/` — existing local goal graph behavior, seed data, and explicitly simulated scenarios. Keep simulated behavior clearly separated from live runtime results.
- `voice/` — microphone recording, WAV handling, transcription UI support, and system speech helpers.

### Desktop shell: `src-tauri/`

- `src/lib.rs`, `src/main.rs` — Tauri setup, native command registration, and app entry point.
- `src/runtime_bridge.rs` — starts and stops the local runtime, authenticates native requests, and forwards runtime events.
- `src/oauth.rs`, `src/chatgpt_auth.rs` — OAuth and ChatGPT sign-in/token handling.
- `capabilities/` — permissions available to the local Tauri window. Keep these narrow and add permissions only when a native feature needs them.
- `icons/`, `Info.plist`, `tauri.conf.json`, `Cargo.toml` — desktop assets, macOS metadata, Tauri packaging settings, and Rust dependencies.
- `runtime-bundle/` — generated packaged Python runtime and Playwright browser. It is ignored by Git and recreated by the release build; do not edit it by hand.

### Agent runtime: `runtime/`

- `api.py`, `main.py`, `models.py` — FastAPI endpoints, runtime wiring, and request/event models.
- `scheduler.py` — agent lifecycle and tool dispatch. Route agent actions through the scheduler and AIP checks.
- `aip/` — local authorization policy and federated signed-statement support.
- `browser/` — isolated Playwright research browser and typed browser operations.
- `providers/` — Meta, OpenAI-compatible, Anthropic, Google, and ChatGPT provider adapters.
- `search.py`, `store.py` — public-web search and local SQLite workspace/event persistence.
- `guard/` — Guard cases, evidence, screening, investigation, adjudication, classification, and containment.
- `services/`, `connectors/` — service permissions, account metadata, OAuth token bridge support, Gmail/Outlook clients, and labeled fake adapters.
- `voice/` — intent routing and transcription services.
- `gateway/` — outbound agent gateway, A2A message handling, endpoint checks, sessions, and gateway tools.
- `merchants/` — local fictional merchant fixtures and scenarios.
- `negotiation/` — multi-round negotiation driver.
- `verification/` — claim checks against the configured trusted catalog.
- `federation/` — Ed25519 key handling and signed federated statements.
- `demo/` — fictional demo website data.
- `experiments/` — deterministic/live evaluation, benchmarks, trace replay, scenarios, and saved evaluation artifacts.
- `tests/` — Python unit, integration, and scenario tests. Tests that contact the web or a model are called out below and should be run intentionally.

### Other folders

- `scripts/` — runtime packaging, embedded-browser checks, macOS signing, DMG creation, and notarization scripts.
- `docs/` — detailed privacy, Guard, mail, App Store, and phase-specific documentation.
- `public/` — frontend static files and demo media.
- `output/` — generated judge-demo, PDF, and UI QA artifacts; not runtime source code.
- `.packaging-*`, `.venv/`, `node_modules/`, `dist/`, and `src-tauri/target/` — local/generated dependency or build folders. These are ignored by Git.

## Contributor guidelines

1. **Keep AIP on the execution path.** Do not call an external service, browse a page, send mail, delegate to an agent, or mutate a workspace on the model's say-so alone. Add/adjust the AIP decision and any required user approval before performing the action.
2. **Use least privilege.** Scope capabilities to the agent, goal, service, and origin that need them. Keep Tauri IPC and runtime API access narrow.
3. **Treat outside content as untrusted.** Web pages, files, provider responses, and agent replies are input data, not instructions that can override user intent or policy.
4. **Keep real and simulated behavior distinct.** Label fixtures and synthetic agents clearly. Never show an attempted action as successful without a real result/receipt.
5. **Protect secrets.** Use the macOS Keychain or local shell environment as appropriate. Never commit provider keys, OAuth secrets, signing keys, app-specific passwords, or user data.
6. **Add focused coverage.** Put UI logic tests with the relevant `src/experience/` or `src/simulation/` code, Python tests under `runtime/tests/`, and Rust tests alongside the native code. Keep ordinary tests deterministic; use live network/model tests only when needed.
7. **Update docs when behavior changes.** If a feature is limited, experimental, or fixture-backed, document that alongside the feature.

## Run checks

Run the focused check for the code you changed, then the broader gates when practical:

```sh
npm test
npm run test:runtime
npm run build
cargo test --manifest-path src-tauri/Cargo.toml
```

The browser test launches real Playwright Chromium and accesses a public page:

```sh
npm run test:browser
```

The Phase 5 and Phase 6 tests/evaluations are documented in [phase5-capabilities.md](docs/phase5-capabilities.md), [phase6-federation.md](docs/phase6-federation.md), [phase6-trust-gateway.md](docs/phase6-trust-gateway.md), and [phase6-eval.md](docs/phase6-eval.md). Live model checks require a configured model credential and can incur provider usage charges. Read the test command before running it to understand network or account access.

## Build and notarize a macOS `.dmg`

This produces a direct-download installer for the architecture of the Mac doing the build. It bundles a Python 3.12 runtime, production Python dependencies, and Playwright Chromium, so the first build downloads dependencies and creates a large DMG. Build one DMG on Apple Silicon and another on Intel if you need to distribute both architectures.

### 1. Prepare the build machine

From the repository root, install the development dependencies as described above. The packaging script uses Python 3.12; it defaults to `.venv/bin/python` and accepts an override through `KEPLERV_BUILD_PYTHON`.

```sh
cd /path/to/KeplerV-Browser
npm ci
python3.12 -m venv .venv
.venv/bin/python -m pip install -r runtime/requirements.txt
```

### 2. Create or check a Developer ID certificate

In Xcode, open **Xcode → Settings → Accounts**, select your paid Developer Program team, choose **Manage Certificates**, and create a **Developer ID Application** certificate. It is used for apps distributed outside the Mac App Store. The private key should remain in your Keychain.

Check the identity name available on this Mac:

```sh
security find-identity -v -p codesigning
```

Copy the complete identity beginning with `Developer ID Application:`. If the command reports no valid identities, the certificate's private key is not available in this Mac's Keychain; create or import the certificate with its private key before building a signed release.

### 3. Build the signed app and DMG

```sh
export APPLE_SIGNING_IDENTITY='Developer ID Application: Your Name (TEAMID)'
npm run desktop:build
```

The script packages the Python runtime and Chromium, signs the embedded runtime, runs its browser self-check, builds the Tauri app, and creates a drag-to-Applications DMG. It prints the output path, normally:

```text
src-tauri/target/release/bundle/dmg/KeplerV_0.1.0_aarch64.dmg
```

The version comes from `package.json`; the architecture suffix is `aarch64` on Apple Silicon or `x64` on Intel. To create an unsigned local build, unset `APPLE_SIGNING_IDENTITY` and run `npm run desktop:build`.

### 4. Set up a notarytool Keychain profile once

For distribution outside your own Mac, notarize the signed DMG. Find your Team ID in your Apple Developer account and create an app-specific password at [account.apple.com](https://account.apple.com/). Then run this command, replacing the Apple ID and Team ID:

```sh
xcrun notarytool store-credentials KeplerVNotary \
  --apple-id 'you@example.com' \
  --team-id 'TEAMID'
```

`notarytool` securely prompts for the app-specific password and saves the credentials to Keychain. Do not put your normal Apple password, app-specific password, or signing key in this repository or command history.

### 5. Submit, wait, and staple

```sh
export APPLE_SIGNING_IDENTITY='Developer ID Application: Your Name (TEAMID)'
export APPLE_NOTARY_PROFILE='KeplerVNotary'
npm run desktop:notarize
```

This command rebuilds the signed app and DMG, submits the DMG to Apple, waits for the result, then staples Apple's ticket to the DMG. It can take several minutes and requires network access.

### 6. Verify and install

Use the exact `.dmg` path printed by the build:

```sh
codesign --verify --deep --strict --verbose=2 'src-tauri/target/release/bundle/macos/KeplerV.app'
spctl --assess --type execute --verbose=2 'src-tauri/target/release/bundle/macos/KeplerV.app'
hdiutil verify 'src-tauri/target/release/bundle/dmg/KeplerV_0.1.0_aarch64.dmg'
xcrun stapler validate 'src-tauri/target/release/bundle/dmg/KeplerV_0.1.0_aarch64.dmg'
```

Open the DMG, drag **KeplerV.app** into **Applications**, then launch it from Applications. Test on a Mac that does not have the development checkout. Gatekeeper behavior and required privacy prompts should be checked on a clean machine/account before distribution.

The direct-download Developer ID DMG is **not** the Mac App Store upload artifact. App Store packaging has separate sandbox, signing, helper-runtime, and review requirements; see [App Store readiness](docs/app-store-readiness.md).
