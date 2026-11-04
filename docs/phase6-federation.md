# Phase 6 — Federated trust gateway: capability matrix

Honest real/experimental/unsupported status for the Phase 6 milestone.
KeplerV now has a real A2A path for public, unauthenticated JSON-RPC agents:
users opt in per goal, provide an Agent Card URL (or ask the researcher to
locate one through ordinary web search), and approve each session and outbound
message. Every cross-boundary message is SSRF-checked, correlated, budgeted,
and recorded in a per-session transcript. A2A does not provide a universal
agent directory, so discovery is limited to an endpoint supplied by the user
or one indexed by web search. Fixture merchants remain in the developer test
harness only and are not a source of real-world agents.

See also `phase6-trust-gateway.md` (protocol, tiers, SSRF rules, verdicts)
and `phase6-eval.md` (eval arms, scenarios, metrics).

## Status matrix

| Area | Status | What is real | What is not / limits |
| --- | --- | --- | --- |
| Public A2A interoperability | Real, opt-in | Agent Card discovery at the standard well-known paths; JSON-RPC messaging for A2A 1.0 and 0.3; human approval for opening sessions and each outbound message; transcripts and responses flow through the Trust Gateway | Public HTTPS and unauthenticated agents only; JSON-RPC text messages only; no universal directory, OAuth/API-key onboarding, streaming, or file-part transport yet |
| External-agent registry | Real | Tiered identity (`verified`/`self_declared`/`unknown`/`suspected`), declared-vs-observed capabilities, asserted-by provenance, reliability counters; secret-bearing fields rejected at registration | Tiers are fixture-driven; no real-world identity provider backs `verified` |
| Trust Gateway transport | Real | Exclusive cross-boundary path: SSRF-checked endpoints, none/bearer-header auth with explicit per-call tokens, strict `extra=forbid` envelopes, `reply_to` correlation, untrusted tagging of inbound, no auto-redirects with per-hop re-validation, timeouts, cancel flags, per-minute + min-interval rate limits, message/token/cost budgets, per-message ledger events + Guard hook; sqlite transcript store | Loopback merchant fixtures allowed only via an explicit `host:port` allowlist (param or `KEPLERV_GATEWAY_FIXTURE_ALLOWLIST`); plain HTTP otherwise denied |
| SSRF protection | Real | `https` only (except allowlisted loopback fixtures); every resolved DNS IP must be acceptable; localhost/private/link-local/cloud-metadata ranges denied; no credentials in URLs; discovery refuses cross-host redirect hops | Allowlist is the explicit escape hatch for fixtures — anything else stays blocked |
| Merchant fixtures | Developer tests only | 4 deterministic profiles (cooperative / competitive / misleading / adversarial) over fictional catalog data, served via loopback HTTP or socket-free mock transports with identical handlers | Synthetic by design; not offered as real internet agents and not used by the public A2A UI |
| Negotiation driver | Real logic over fake merchants | Multi-round quote/order flows through the gateway with full transcripts + claim verdicts; frozen goals with per-round drift checks; inbound threat scan + policy-view redaction; credential-request refusal; never fetches inbound URLs | Local policy is the deterministic fake policy (`FAKE` label); no live model negotiates |
| Claim verification | Real checks, fixture authority | 4 verdicts (`cryptographically-attested` / `independently-observed` / `statement-made` / `unverified`); trusted catalog with SHA-256 manifest; evidence refs are caller-supplied only | Catalog authority is fictional fixture data; attestations are Ed25519 with an in-memory process key (production must use the keychain `federation-identity` namespace) |
| AIP gateway matrix | Real enforcement | `gateway.discover`/`close_session` grant-only; `open_session`/`send`/`verify` require grant + explicit approval; denials emit `aip.decision` + `service.access_denied` and open Guard cases | Scheduler dispatch only; no new HTTP grant/revoke routes |
| Data minimization | Real enforcement | Secret/account/session-bearing outbound fields refuse the whole send, naming fields only (never values); refused sends issue zero gateway POSTs and zero session usage | Field-name matching (compound-key parts); oversized payloads refused by the byte cap |
| Federated AIP statements | Experimental prototype | Ed25519 signed statements verify at operator level; tampered/unknown/revoked keys rejected as unverified-signature; delegation/receipt proof levels only with valid covering credentials; signed-is-not-true is tested; key rotation keeps pre-rotation envelopes verifiable | `cryptography`-package Ed25519, no custom crypto — production keys must live in the keychain `federation-identity:{operator}:{key}` namespace (test-only file fallback via `KEPLERV_FEDERATION_KEYSTORE_DIR`) |
| Guard behavioral classification | Rules baseline + model classifier | Deterministic rules stay as the offline baseline; a model-powered classifier (`runtime/guard/model_classification.py`) classifies the 8 behavior classes through the provider interface over a sanitized transcript with schema-validated JSON. Fake-adapter results are labeled `fake-model-v1`; the live Muse path requires `MODEL_API_KEY` + `KEPLERV_GUARD_MODEL_CLASSIFIER=1` and is labeled `live-muse-v1`. The session panel shows the backend classifier label with a FAKE/LIVE badge | Committed tests use fake adapters only; live classification needs the user's `MODEL_API_KEY`; no intent claims and no guarantee of detecting novel attacks |
| Guard external investigation | Experimental rules | Pure analysis (no gateway handle, so no I/O): sanitized Detective transcripts, catalog+ledger contradiction detection, verdicts, classification, gateway-bound follow-ups, abstention on thin evidence; contradictions open `external_contradiction` cases | Baseline labels still apply; follow-ups are data the local agent must issue via `gateway.send` |
| Guard auto-containment | Real, triggered end to end | Policy-gated auto-containment (`runtime/guard/auto_containment.py`, modes off / pause-only default / full) reuses the trusted pause/deny/isolate/revoke controls, records policy (never user) authorization as hash-chained evidence, and emits a user-visible `guard.auto_containment` audit event per action; mode is set via constructor/attribute/env or authed `GET/POST /guard/auto-containment`, and the Safety panel renders mode + automatic-action notices with Undo/Review | High-risk flagged external replies (`injection`, `unauthorized-request`, `policy-violation`) trigger automatically after `gateway.send`; routine AIP denials never auto-trigger; isolate/revoke need approval unless mode is full; human override via existing resume/restore |
| Phase 6 eval runner | Real harness, fake adapters | AIP-only vs passive-observer vs active-investigation over dev + held-out splits; all metrics measured from transcripts/verdicts/AIP/usage/latency; determinism digest; errors recorded, never invented | No live-model path; the live loopback transport requires explicit `KEPLERV_PHASE6_LIVE_EVAL=1` and is still fictional data |
| Voice inspection | Real intents, fixture data | 5 new intent types with known-id validation (invented ids → clarification); verification/close routes are Bearer-authed; STOP_TALKING_TO navigates, never closes by voice | Answers come only from fetched session records; closing stays a human-confirmed button |
| Session graph UI | Real rendering of fixture data | External-agent nodes with tier badges, trust-boundary divider, session edges, one session detail panel (agent/operator/session/claims/verdicts/contradictions/evidence/analysis) with simple-mode progressive disclosure | No UI redesign, no tabs/dashboards/chat panels — the existing surfaces are reused |
| Demo end-to-end | Real test, fake world | Committed `test_phase6_demo_e2e.py`: shopping goal → discover 2 merchants → legit offer completes, unsupported branch stopped, unauthorized branch denied → Guard investigation → catalog comparison → findings → inspectable after restart | Socket-free fixtures; the 5 `test_demo.py` loopback-bind tests (of 6) still need real sockets (see below) |

## Unsupported / explicitly out of scope

- Live-model negotiation or live-model eval runs by default — fake adapters
  only; the live loopback runner is explicit opt-in and labeled.
- Real purchases, real currency, real merchants — the marketplace is fictional.
- Keychain-backed federated credentials — Ed25519 is real but the
  Python sidecar holds memory-only handles; the Tauri layer must persist
  seeds under `federation-identity:{operator}:{key}` before production use.
- Automatic triggering of Guard containment for routine denials —
  high-risk flagged external replies trigger automatically, while routine
  denials only open cases for human review, by design.
- Keychain `MODEL_API_KEY` access from the gateway path — never touched or
  printed; fixture tokens are explicit per-call values.
- Docker, non-loopback binds, custom crypto — not used anywhere in Phase 6.

## Verification scenarios

- **Demo end-to-end** (`runtime/tests/test_phase6_demo_e2e.py`, pytest):
  shopping goal → discover cooperative + misleading merchants → legit quote
  verifies `independently-observed` and the fictional order is accepted →
  misleading quote verifies `unverified` and the branch is stopped with no
  order sent → ungranted `gateway.send` is denied by AIP with a
  `service_access_denied` Guard case → secret-bearing outbound is refused
  naming fields only → Guard investigation classifies misleading with
  catalog-price + unsupported-claim contradictions and opens an
  `external_contradiction` case while the legit exchange classifies clean →
  catalog comparison + 2 findings → after reopening the store, sessions,
  transcripts, events, evidence, Guard cases, and the verification route all
  remain inspectable.
- **Eval** (`runtime/tests/test_phase6_eval.py` + `docs/phase6-eval.md`):
  prevention only in the active arm, claim detection everywhere, zero false
  positives, full completion and citation accuracy, determinism digest.
- **Voice + inspection** (`runtime/tests/test_phase6_voice_inspection.py`,
  `src/experience/gatewayGraph.test.ts`): intent validation, verification
  route from real transcript rows, auth/404/close behavior, tier badges and
  redaction in the graph helpers.

## Dev commands

```sh
.venv/bin/python -m pytest runtime/tests -q    # 319 passed, 8 skipped (run outside the macOS sandbox for loopback-bind tests)
.venv/bin/python -m pytest runtime/tests/test_phase6_demo_e2e.py -q                # 1 passed
.venv/bin/python -m runtime.experiments.phase6_eval runtime/experiments/results/phase6-eval  # fake eval
npm test                       # vitest: 95 passed
npm run build                  # svelte-check (0 errors, 0 warnings) + vite build
cargo test --manifest-path src-tauri/Cargo.toml   # 23 passed
```

Guard/federation-focused subsets (fake adapters only, no live-model calls):

```sh
.venv/bin/python -m pytest runtime/tests/test_federation_ed25519.py runtime/tests/test_phase6_investigation.py runtime/tests/test_phase6_negotiation.py -q
.venv/bin/python -m pytest runtime/tests/test_guard_auto_containment.py runtime/tests/test_guard_model_classifier.py -q
```

Optional live loopback eval (explicit opt-in; still fictional fixture data):

```sh
KEPLERV_PHASE6_LIVE_EVAL=1 .venv/bin/python -m runtime.experiments.phase6_eval --live runtime/experiments/results/phase6-eval-live
```

No live-model evals are part of these gates
(`runtime.experiments.live_eval` is never run here).

## Judge verification (no secrets needed)

```sh
cd /Users/alidaho/Projects/keplerv/browser
.venv/bin/python -m pytest runtime/tests -q
npm test
cargo test --manifest-path src-tauri/Cargo.toml
npm run build
```

Expected: pytest 319 passed / 8 skipped, vitest 95 passed, cargo 23
passed, svelte-check 0 errors / 0 warnings. Live Guard classification
additionally needs `MODEL_API_KEY` + `KEPLERV_GUARD_MODEL_CLASSIFIER=1`
(env-gated, labeled `live-muse-v1`); without them the deterministic
`fake-rules-v1` baseline and `fake-model-v1` adapters apply. Committed
tests make no live-model calls.

## Module map

- `runtime/gateway/` — registry, SSRF guard, sessions/transcripts, client,
  events, agent-facing tools (`discover/open/close/send/verify`)
- `runtime/merchants/` — fixture profiles, discovery, scenarios, fixture +
  trusted catalogs
- `runtime/negotiation/` — multi-round driver + deterministic fake policy
- `runtime/verification/` — claim verdict service
- `runtime/federation/`, `runtime/aip/federated.py` — Ed25519 signed statements
  (`keys.py`: keygen/sign/verify, keystore, rotation; `cryptography==50.0.2`)
- `runtime/guard/model_classification.py`,
  `runtime/guard/auto_containment.py` — model classifier (fake default,
  live env-gated) + policy-gated auto-containment
- `runtime/guard/behavior_classification.py`,
  `runtime/guard/external_investigation.py` — classification + investigation
- `runtime/experiments/phase6_eval.py` — three-arm eval runner
- `runtime/voice/intent.py`, `runtime/api.py` — inspection intents + routes
- `src/experience/gatewayGraph.ts` — session graph helpers + tests
