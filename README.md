# KeplerV

**KeplerV is a macOS browser built around goals and AIP.** Instead of asking users to manage a pile of tabs, it gives AI agents a workspace to research and act within clear permissions. AIP decides whether an agent action is allowed, needs your approval, or must be blocked. Guard records selected risks and decisions so people can review what happened.

KeplerV is an active prototype. Some integrations use real services, while Phase 6 federation and merchant scenarios include experimental or fictional fixtures. See the capability notes before treating a feature as production-ready.

## Try KeplerV

### Requirements

- macOS
- Xcode Command Line Tools
- Node.js 20.19+ or 22.12+
- Rust stable
- Python 3.12

### Set up and launch

From the repository root:

```sh
npm ci
python3.12 -m venv .venv
.venv/bin/python -m pip install -r runtime/requirements.txt
.venv/bin/python -m playwright install chromium
npm run desktop
```

The first launch opens the Tauri desktop app and starts its local Python runtime. Connect a model in **LLM providers** in the app to run live research. For local development or automation, you can instead set `MODEL_API_KEY` in the shell before starting KeplerV. `.env.example` documents the supported variables; it is a template and is not loaded automatically.

`npm run dev` starts only the web interface. Runtime and native features require `npm run desktop`.

## What is in the project?

| Folder | What it contains |
| --- | --- |
| `src/` | Svelte interface, workspace and goal flows, media and account helpers, voice controls, and local simulation code. |
| `src-tauri/` | Tauri desktop shell, Rust commands, runtime and OAuth bridges, app permissions, icons, and macOS configuration. |
| `runtime/` | Local Python API and agent runtime. Includes AIP policy, research browser, providers, Guard, connected services, voice, and Phase 6 experiments. |
| `scripts/` | macOS runtime packaging, code signing, DMG creation, notarization, and runtime entry point. |
| `docs/` | Privacy, Guard, connected-service setup, App Store readiness, and Phase 5/6 technical notes. |
| `public/` | Static app assets and demo media. |
| `output/` | Generated demo, PDF, and UI QA artifacts; not application source. |

See [CONTRIBUTING.md](CONTRIBUTING.md) for the source map, development checks, design rules, and detailed macOS `.dmg` instructions.

## AIP and Guard

AIP is the central control point for agent actions. It checks the agent, goal, granted capability, requested tool, and the applicable origin or human approval before work runs. The model cannot change AIP permissions. Guard keeps a reviewable record of policy denials and selected risk signals; its model-based assessments are advisory and do not change access policy.

This is an evolving prototype, not a claim that every possible attack is detected or prevented. Read [Guard architecture](docs/guard-architecture.md) and [privacy and data controls](docs/privacy-and-data-controls.md) for details.

## Test and build

```sh
npm test                                      # Svelte/TypeScript tests
npm run test:runtime                         # Python runtime tests
npm run test:browser                          # Live Playwright browser integration test
npm run build                                 # Frontend type checks and production build
cargo test --manifest-path src-tauri/Cargo.toml
```

The browser integration and live model evaluations use network access. Live model calls require a provider credential. For focused Phase 5/6 checks and test limitations, see [CONTRIBUTING.md](CONTRIBUTING.md) and the phase documents in `docs/`.

## Build a macOS `.dmg`

The release script builds a bundled Python runtime and Playwright Chromium, signs the app when a Developer ID identity is configured, and creates a drag-to-Applications DMG. The result is architecture-specific and large because Chromium and the Python runtime are included.

```sh
export APPLE_SIGNING_IDENTITY='Developer ID Application: Your Name (TEAMID)'
npm run desktop:build
```

The DMG is written to `src-tauri/target/release/bundle/dmg/`. For an unsigned local build, leave `APPLE_SIGNING_IDENTITY` unset. To distribute outside your own Mac, sign and notarize with Apple; follow the complete steps in [CONTRIBUTING.md](CONTRIBUTING.md#build-and-notarize-a-macos-dmg). Mac App Store packaging is a separate release track; see [App Store readiness](docs/app-store-readiness.md).

## Project notes

- [Phase 5 capabilities](docs/phase5-capabilities.md)
- [Phase 6 federation and capability status](docs/phase6-federation.md)
- [Phase 6 Trust Gateway](docs/phase6-trust-gateway.md)
- [Phase 6 evaluation](docs/phase6-eval.md)
- [Mail and OAuth setup](docs/mail-setup.md)
