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
| Claim verification | Real checks, fixture authority | 4 verdicts (`cryptographically-attested` / `independently-observed` / `statement-made` / `unverified`); trusted catalog with SHA-256 manifest; evidence refs are caller-supplied only | Catalog authority is fictional fixture data; attestations are fixture-only HMAC (see below) |
| AIP gateway matrix | Real enforcement | `gateway.discover`/`close_session` grant-only; `open_session`/`send`/`verify` require grant + explicit approval; denials emit `aip.decision` + `service.access_denied` and open Guard cases | Scheduler dispatch only; no new HTTP grant/revoke routes |
| Data minimization | Real enforcement | Secret/account/session-bearing outbound fields refuse the whole send, naming fields only (never values); refused sends issue zero gateway POSTs and zero session usage | Field-name matching (compound-key parts); oversized payloads refused by the byte cap |
| Federated AIP statements | Experimental prototype | HMAC-SHA256 signed statements verify at operator level; tampered/unknown/revoked keys rejected as unverified-signature; delegation/receipt proof levels only with valid covering credentials; signed-is-not-true is tested | Symmetric HMAC only (stdlib `hmac`/`hashlib`, no custom crypto) — production use requires asymmetric signatures with KMS-held keys; fixture keys never touch the keychain |
| Guard behavioral classification | Experimental rules | A2A inbound replies are screened in the background for observable instruction overrides, credential requests, coercion, and redirect markers; flags are attached to the session's Guard case and transcript; firm bargaining is not itself suspicious | Fixed deterministic rules labeled `fake-rules-v1`, not a live-model judgment; no intent claims and no guarantee of detecting novel attacks |
| Guard external investigation | Experimental rules | Pure analysis (no gateway handle, so no I/O): sanitized Detective transcripts, catalog+ledger contradiction detection, verdicts, classification, gateway-bound follow-ups, abstention on thin evidence; contradictions open `external_contradiction` cases | Same `fake-rules-v1` caveat; follow-ups are data the local agent must issue via `gateway.send` |
| Phase 6 eval runner | Real harness, fake adapters | AIP-only vs passive-observer vs active-investigation over dev + held-out splits; all metrics measured from transcripts/verdicts/AIP/usage/latency; determinism digest; errors recorded, never invented | No live-model path; the live loopback transport requires explicit `KEPLERV_PHASE6_LIVE_EVAL=1` and is still fictional data |
| Voice inspection | Real intents, fixture data | 5 new intent types with known-id validation (invented ids → clarification); verification/close routes are Bearer-authed; STOP_TALKING_TO navigates, never closes by voice | Answers come only from fetched session records; closing stays a human-confirmed button |
| Session graph UI | Real rendering of fixture data | External-agent nodes with tier badges, trust-boundary divider, session edges, one session detail panel (agent/operator/session/claims/verdicts/contradictions/evidence/analysis) with simple-mode progressive disclosure | No UI redesign, no tabs/dashboards/chat panels — the existing surfaces are reused |
| Demo end-to-end | Real test, fake world | Committed `test_phase6_demo_e2e.py`: shopping goal → discover 2 merchants → legit offer completes, unsupported branch stopped, unauthorized branch denied → Guard investigation → catalog comparison → findings → inspectable after restart | Socket-free fixtures; the 5 `test_demo.py` loopback-bind tests (of 6) still need real sockets (see below) |

## Unsupported / explicitly out of scope

- Live-model negotiation or live-model eval runs by default — fake adapters
  only; the live loopback runner is explicit opt-in and labeled.
- Real purchases, real currency, real merchants — the marketplace is fictional.
- Asymmetric federated credentials / KMS integration — the HMAC prototype
  must be replaced before any production use.
- Automatic Guard containment for gateway anomalies — cases open for human
  review; emergency auto-rules remain unimplemented.
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
.venv/bin/python -m pytest runtime/tests -q --deselect runtime/tests/test_demo.py  # 245 passed, 6 skipped
.venv/bin/python -m pytest runtime/tests/test_phase6_demo_e2e.py -q                # 1 passed
.venv/bin/python -m runtime.experiments.phase6_eval runtime/experiments/results/phase6-eval  # fake eval
npm test                       # vitest: 56 passed
npm run build                  # svelte-check (0 errors) + vite build
```

Optional live loopback eval (explicit opt-in; still fictional fixture data):

```sh
KEPLERV_PHASE6_LIVE_EVAL=1 .venv/bin/python -m runtime.experiments.phase6_eval --live runtime/experiments/results/phase6-eval-live
```

`runtime/tests/test_demo.py` (6 tests, 5 binding loopback sockets) fails with
`PermissionError: [Errno 1]` inside the macOS sandbox; this predates Phase 6
and is unrelated to it. No live-model evals are part of these gates
(`runtime.experiments.live_eval` is never run here).

## Module map

- `runtime/gateway/` — registry, SSRF guard, sessions/transcripts, client,
  events, agent-facing tools (`discover/open/close/send/verify`)
- `runtime/merchants/` — fixture profiles, discovery, scenarios, fixture +
  trusted catalogs
- `runtime/negotiation/` — multi-round driver + deterministic fake policy
- `runtime/verification/` — claim verdict service
- `runtime/federation/`, `runtime/aip/federated.py` — signed-statement prototype
- `runtime/guard/behavior_classification.py`,
  `runtime/guard/external_investigation.py` — classification + investigation
- `runtime/experiments/phase6_eval.py` — three-arm eval runner
- `runtime/voice/intent.py`, `runtime/api.py` — inspection intents + routes
- `src/experience/gatewayGraph.ts` — session graph helpers + tests
