# KeplerV-Browser

KeplerV is a macOS Tauri 2 application organized around user goals, agent work, and human review. This project extends the existing Svelte Intent Space with a managed local Universal Agent Runtime (UAR), Meta Muse, a permissioned research browser, and a persistent event/evidence store.

## Run the desktop app

Requirements for development: macOS, Xcode Command Line Tools, Rust stable, Node.js 20.19+ or 22.12+, and Python 3.12+.

```sh
cd /Users/alidaho/Projects/keplerv/browser
npm install
python3.12 -m venv .venv
.venv/bin/python -m pip install -r runtime/requirements.txt
.venv/bin/python -m playwright install chromium
```

Connect Meta Muse from LLM providers in the app. KeplerV saves the credential in the macOS Keychain and passes it from its native process to the local Python runtime; it is not stored with workspace data or sent to browser pages. Environment variables remain available for development and automation.

```sh
export MODEL_API_KEY="your Meta Model API key"
npm run desktop
```

The app starts a Python sidecar on `127.0.0.1` using a fresh, app-scoped random bearer token. Tauri stops the sidecar when the desktop process exits. Run `npm run dev` to work on the Svelte interface alone; runtime controls require the native desktop app. Build the macOS app bundle with `npm run desktop:build`.

## Build a downloadable macOS installer

The release build creates a self-contained app and DMG for the architecture of the build Mac. It packages Python 3.12, the production runtime dependencies, and Playwright's headless Chromium. The first build downloads its Python packaging dependencies and Chromium, so it needs internet access and can take several minutes. The resulting DMG is large because it contains Chromium.

```sh
npm run desktop:build
```

The build currently produces an Apple Silicon DMG when run on an Apple Silicon Mac and an Intel DMG when run on an Intel Mac. Build and distribute one DMG per architecture. Apple Silicon users can run Intel apps with Rosetta, but the embedded runtime and browser are native to their build architecture.

### Sign with your Apple Developer account

In Xcode, open **Xcode → Settings → Accounts**, add your Apple Developer account, select your team, open **Manage Certificates**, and create a **Developer ID Application** certificate. This certificate is for apps distributed outside the Mac App Store. Check that macOS can see it:

```sh
security find-identity -v -p codesigning
```

Copy the exact `Developer ID Application: ...` identity shown there. The private key stays in your macOS Keychain; do not put it in this repository. Then build a signed app and DMG:

```sh
export APPLE_SIGNING_IDENTITY='Developer ID Application: Your Name (TEAMID)'
npm run desktop:build
```

To notarize the DMG, first create a notarytool Keychain profile once. Find your Team ID in the Apple Developer account membership details, create an app-specific password at account.apple.com, then run:

```sh
xcrun notarytool store-credentials KeplerVNotary \
  --apple-id 'your-apple-id@example.com' \
  --team-id 'TEAMID' \
  --password 'app-specific-password'
```

The password is stored in your login Keychain. Then notarize and staple the DMG ticket:

```sh
export APPLE_SIGNING_IDENTITY='Developer ID Application: Your Name (TEAMID)'
export APPLE_NOTARY_PROFILE='KeplerVNotary'
npm run desktop:notarize
```

The notarization command submits the built DMG to Apple and waits for its result. A successful notarization is stapled to the DMG so Gatekeeper can verify it when the Mac is offline. These Apple steps require an active Developer Program membership and the Account Holder or an Admin who can create signing assets.

`.env.example` lists the supported environment variable names. It is a template, not loaded automatically; set the variables in the shell that launches KeplerV. `KEPLERV_PYTHON` can point the native app to a specific Python executable if you do not use the project's `.venv`.

## Research flow

1. Create/select a goal in the existing Intent Space and open its details.
2. Set the maximum token budget for the run (1,000–50,000) or leave the saved preference.
3. Start Muse research from the goal. A starting URL is optional; KeplerV searches the public web for up to three results.
4. Muse may inspect up to two public HTTPS result pages and extract up to 3,500 characters per page in an isolated headless Chromium context. Page clicks and form fills require per-action approval.
5. KeplerV records runtime events and source-quoted findings in local SQLite, associates them with the goal, and updates the existing graph/activity surfaces.
6. Pause, resume, stop, review approvals, and inspect findings from the goal/agent panels.

The model cannot grant itself permissions. AIP checks agent/run identity, goal, capability, tool, authorized origin, public DNS resolution, delegation limits, and approval before a tool runs. The browser permits requests only to the selected origin; untrusted pages do not have Tauri IPC access. Near 60% of a sufficiently large token budget, a parent can hand off compact source excerpts to one continuation agent. The child receives only the remaining run budget, and handoffs pass through AIP and the normal delegation limits.

## Runtime settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `MODEL_API_KEY` | unset | Meta Model API credential; required for live Muse calls |
| `KEPLERV_MODEL_PROVIDER` | `meta` | Runtime adapter selection; only `meta` has a live adapter in this milestone |
| `KEPLERV_MODEL_NAME` | `muse-spark-1.3` | Model identifier sent to the Meta API |
| `KEPLERV_PYTHON` | project `.venv`, then Python 3.12, then `python3` | Development runtime interpreter override; packaged builds use their embedded runtime |

Provider settings can connect Meta Muse, OpenAI-compatible services, Anthropic, Gemini, DeepSeek, Kimi, OpenRouter, Groq, Mistral, Ollama, LM Studio, and compatible local endpoints. Cloud connections are checked against the provider's available models before their keys are saved in Keychain. OpenAI-compatible services use Chat Completions; Anthropic, Gemini, and ChatGPT use their respective request formats. ChatGPT uses OpenAI's published local-app OAuth flow, including PKCE, state/nonce checks, signed identity-token validation, keychain token storage, refresh, and optional plan-usage authorization. The runtime selects the connected provider and model for new research runs.

## Verify

```sh
npm run test:runtime
npm run test:browser
npm test
npm run build
cargo test --manifest-path src-tauri/Cargo.toml
```

Phase 5 gates (no live-model evals; `test_demo.py` needs loopback sockets and is
deselected inside sandboxes — see `docs/phase5-capabilities.md`):

```sh
.venv/bin/python -m pytest runtime/tests -q --deselect runtime/tests/test_demo.py
.venv/bin/python -m pytest runtime/tests/test_phase5_scenarios.py -q
npx vitest run src/experience/scenarioA.localVideo.test.ts
cargo test --manifest-path src-tauri/Cargo.toml
npm run build
```

Phase 6 gates (no live-model evals; `test_demo.py` needs loopback sockets and is
deselected inside sandboxes — see `docs/phase6-federation.md`):

```sh
.venv/bin/python -m pytest runtime/tests -q --deselect runtime/tests/test_demo.py
.venv/bin/python -m pytest runtime/tests/test_phase6_demo_e2e.py -q
.venv/bin/python -m runtime.experiments.phase6_eval runtime/experiments/results/phase6-eval
npm test
npm run build
```

The deterministic Python workflow exercises orchestration, AIP denial, event persistence, and source-checked evidence. A separate browser integration test below uses real Playwright Chromium. A live Meta API call requires internet access and `MODEL_API_KEY`.

Run `npm run test:browser` to exercise the scheduler and event/evidence store against a real Playwright Chromium session and the public Python documentation site. The Python suite also includes a full live Muse + Chromium workflow. It runs automatically when `MODEL_API_KEY` is present and is skipped otherwise; that test makes real requests to Meta and the public article.

Run the live Guard evaluation (scenario captures plus Spark/Wonder investigation on every config) with:

```sh
MODEL_API_KEY="your Meta Model API key" .venv/bin/python -m runtime.experiments.live_eval [output-directory]
```

It writes `evaluation.json` and `evaluation.md` with measured detections, citations, questions, tokens, and latency. The deterministic suite covers the same harness with scripted models.

Measure one live research run (13.4s wall, 3 steps, 7,409 tokens, 3 findings in the recorded run) with:

```sh
MODEL_API_KEY="your Meta Model API key" npm run benchmark -- runtime/experiments/results/benchmark
```

It writes `benchmark.json` plus a full `trace.json` you can replay labeled with `python -m runtime.experiments.replay <trace.json>`.

Run the desktop app with the synthetic demo environment using:

```sh
KEPLERV_DEMO_MODE=1 npm run desktop
```

Then open any goal and use the "Use fictional demo fixtures" button to point research at the university catalog. Fixture pages are fictional, labeled, and unreachable outside demo mode.

## What is operational and what remains simulated

| Area | Current behavior |
| --- | --- |
| Desktop shell and Intent Space | Native Tauri 2 + Svelte application; existing goal graph and panels are reused |
| Local UAR | Real FastAPI sidecar, token-authenticated loopback API, asynchronous agent lifecycle, pause/resume/stop, SQLite persistence |
| Muse | Real Meta API adapter using `MODEL_API_KEY`, configurable model name, tool-call normalization, timeouts and visible errors; live request not tested without credentials |
| Research browser | Isolated Playwright/Chromium context, public web search, search-result origin authorization, typed browser tools; actual Chromium launch not proven by the fake-browser tests |
| AIP | Deterministic Python authorization for supported tools; experimental, not a production security protocol |
| Events and evidence | Runtime-generated records persist locally; Guard cases correlate AIP denials with append-only, hash-chained evidence and case-level read permissions |
| Guard Observer | Deterministic AIP denial rules open and group cases per run; one contextual Muse Spark screen is triggered per case, with qualitative uncalibrated risk and evidence-cited output; cases missed by a restart are re-screened on launch |
| Guard investigation | User-started Muse Wonder report plus adaptive, one-question-at-a-time interviews; Wonder chooses evidence-cited questions, the original agent model answers in an isolated no-tools context, each statement is retained as unverified evidence, and the five-question cap persists with the case; verified live against Meta models |
| Guard Adjudicator | Independent Muse Wonder review excludes the Detective report, validates citations, distinguishes denials from completed actions, and records contradictions and uncertainty for human review; it cannot change AIP policy; verified live against Meta models |
| Guard containment | Human-confirmed pause/resume, deny-pending, capability revocation, browser isolation/restore, and review requests are routed through the trusted runtime, recorded in the hash-chained case history, and leave AIP policy unchanged; automatic emergency rules remain unimplemented |
| Guard scenarios and evaluation | Five isolated adversarial scenarios (prompt injection, benign anomaly, delegation abuse, contradictory statements, search poisoning) plus a three-config evaluation harness with a no-interview ablation; see `runtime/experiments/results/` for measured live-model outputs |
| Provider connections | Cloud credentials are saved in macOS Keychain; provider settings and local model endpoints stay on-device; ChatGPT account tokens are refreshed and used only when plan usage was authorized |
| Offline workspace | Explicitly selectable from the goal panel when no model is connected; goals, tasks, and local helpers stay available while research stays gated and nothing is presented as live |
| Voice transcription | Real 16kHz microphone capture with energy endpointing, transcribed by Meta `muse-voice-transcribe-1.0`; example transcripts remain only as an explicit fallback when recording or transcription is unavailable |
| Speech output | Real system-native speech synthesis with interrupt-to-listen, announcements for approvals, Guard cases, and completions, plus a settings toggle; presence shows listening, processing, and speaking states |
| Intent interpreter | Muse classifies 15 spoken operations with validated target IDs from live workspace context; unknown references and ambiguity return clarification questions, never guesses |
| Voice approvals | Spoken requests open the exact pending approval and read its details; confirmation stays a deliberate button action in the goal panel and AIP makes the final decision |
| Synthetic demo environment | Fictional KeplerV University fixtures served from loopback; AIP exemption is path-scoped to `/demo/`, requires explicit `KEPLERV_DEMO_MODE=1`, and keeps capability and approval checks |
| Tracing and replay | `GET /runs/{id}/trace` exports events, evidence, cases, and stage latencies; `runtime.experiments.replay` verifies and plays traces back labeled REPLAY without model calls |
| Learning library | Goal-scoped collection of agent source links, findings, PDFs, direct video files, and web pages; cards show the finding, source excerpt, provider, and agent |
| UB schedule PDF | Existing guided planning draft, available in the learning library after its planning flow completes; it is not produced by the research runtime |
| Legacy sample goals and agents | Existing workspace examples remain local; Guard cases now come from actual runtime AIP decisions |
| YouTube / Vimeo | Official provider embeds created from source URLs found by the agent; users never paste IDs; blocked videos can be opened at their source |
| Direct video files and PDFs | HTTPS `.mp4`, `.webm`, `.mov`, and `.pdf` links open in the in-app viewer when the host permits it; files can be downloaded from their source |
| Netflix | Opens in the system browser; embedded playback is unsupported (DRM) and no workaround is attempted |
| Service accounts (OAuth) | Real PKCE flow (S256, state/nonce, loopback callback, refresh, revoke) with keychain token storage under `oauth:{provider}:{account}`; public client IDs only via `KEPLERV_OAUTH_CLIENT_ID_*` or `oauth-client-ids.json`; no IDs ship so live sign-in is unexercised; Python holds metadata only, never tokens |
| Email read | Fake-mail fixture (3 fictional messages, every payload FAKE-labeled) served over token-authed `/mail/*` routes; Gmail client is real but fails closed as not-connected (keychain tokens not bridged to Python); Outlook is a labeled skeleton; ambiguous references return candidates, never auto-pick |
| Email send | Fake-mail outbox only, after AIP grant plus explicit approval; voice can only request approval; real-provider send fails loudly with "Nothing was sent" — no live send path by design |
| Service permissions | 13 service tools gated on exact per-agent/per-goal grants with immediate revoke, approval for sensitive ops, `service.access_denied` events, and high-severity Guard cases; unauthorized sends are denied with an empty outbox (Scenario C) |
| Trust Gateway | Real exclusive cross-boundary path: SSRF-checked endpoints, strict envelopes, correlation, budgets, rate limits, sqlite transcripts; loopback fixtures only via explicit allowlist |
| Synthetic merchants | Fake labeled fixtures (cooperative/competitive/misleading/adversarial) over the fictional KeplerV Bazaar; fictional credits, no real purchases |
| Negotiation + verification | Real multi-round gateway flows with frozen goals, threat redaction, credential refusal, and 4-level claim verdicts against a SHA-256-manifested trusted catalog; local policy is a deterministic fake |
| Federated AIP | Experimental symmetric-HMAC signed-statement prototype (established primitives, no custom crypto); production needs asymmetric keys in a KMS |
| Guard exchange investigation | Deterministic `fake-rules-v1` classification (8 classes) + pure investigation with catalog/ledger contradiction detection and gateway-bound follow-ups; contradictions open Guard cases |
| Phase 6 eval | Real three-arm harness (AIP-only vs passive vs active) over dev + held-out fictional scenarios; fake adapters by default, live loopback explicitly opt-in and still fictional |
| Gateway inspection | Real voice intents + tier-badged session graph rendering fixture records; close stays human-confirmed, never voice-triggered |

See `docs/phase5-capabilities.md` for the full honest real-vs-fake matrix, verification scenarios, and module map.

See `docs/phase6-federation.md` for the Phase 6 real/experimental/unsupported matrix and dev commands (`docs/phase6-trust-gateway.md` and `docs/phase6-eval.md` cover the protocol and eval details).

The native resource-window feature remains separate from the agent research browser. A research agent uses its isolated Playwright context and does not open conventional tabs in the user's visible window. Web search uses DuckDuckGo's public HTML search endpoint; no separate search account or API key is required.

## Project structure

- `src/App.svelte` — Intent Space, goal/agent panels, runtime event integration, and existing audio/plan flows.
- `src/simulation/` — existing goal graph behavior and explicitly local demonstration scenarios.
- `src/experience/` — media surface manager (audio focus, capability probes) and account resolution.
- `runtime/` — FastAPI sidecar, Meta adapter, agent scheduler, AIP, Playwright browser gateway, SQLite store, and tests.
- `runtime/gateway/`, `runtime/merchants/`, `runtime/negotiation/`, `runtime/verification/`, `runtime/federation/` — Trust Gateway, synthetic merchants, negotiation driver, claim verification, and federated-statement prototype.
- `runtime/services/` — service registry, account metadata store, and labeled fake fixtures.
- `runtime/connectors/` — Gmail client, Outlook skeleton, fake-mail adapter, shared account resolver.
- `src-tauri/src/oauth.rs` — OAuth PKCE flow, keychain token storage, connect/refresh/disconnect commands.
- `src-tauri/src/runtime_bridge.rs` — managed sidecar process, random token, authenticated native requests, and typed event polling.
- `src-tauri/src/lib.rs` — Tauri command registration and sidecar lifecycle.
- `src-tauri/capabilities/default.json` — IPC permissions scoped to the local `main` window.
