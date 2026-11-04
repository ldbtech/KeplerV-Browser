# Phase 5 — Media surfaces & connected services: capability matrix

Honest real-vs-fake status for the Phase 5 milestone. Anything labeled FAKE is a
fictional `example.test` fixture that can never be mistaken for live data; live
paths that are not wired fail loudly instead of substituting simulation.

## Media

| Surface | Status | What is real | What is not |
| --- | --- | --- | --- |
| Agent learning library | Goal-scoped list | Collects agent evidence links and saved resources into Videos, Documents, Files, and Web pages; cards show the finding, source excerpt, provider, and agent; common YouTube/Vimeo/PDF/video URLs open from the card | The current research runtime returns findings and links, not arbitrary generated file attachments; an agent-created PDF attachment needs a file-output contract |
| YouTube / Vimeo | Link-driven official embeds | Provider URLs found by an agent are parsed into their supported player; users never paste provider IDs | Requires network and provider embed permission; blocked or private videos can be opened at their source |
| PDF | In-app document view | Agent-linked PDFs and the existing planning draft open in the library and can be downloaded | Remote sites can block embedding; use the source link in that case |
| Netflix | External fallback only — never embedded | A Netflix resource opens in the system browser | Embedded DRM playback is unsupported; KeplerV does not try to bypass it |

Voice control (`OPEN_MEDIA` / `PAUSE_MEDIA` / `RESUME_MEDIA` / `CLOSE_MEDIA`) is
wired end to end: `runtime/voice/intent.py` classifies the intent,
`interpret_voice` proxies through `POST /interpret`, and `App.svelte` opens the
selected goal's learning library. A media ID is not required; a requested title
can open a matching item.

## Connected services (OAuth + email)

| Area | Status | What is real | What is not |
| --- | --- | --- | --- |
| OAuth PKCE flow | Real protocol, unconfigured credentials | RFC 7636 S256 PKCE, state/nonce, authorize-URL builder, 127.0.0.1 loopback callback with state/path validation, code exchange, refresh, best-effort revoke; public client IDs only via `KEPLERV_OAUTH_CLIENT_ID_{PROVIDER}` or `oauth-client-ids.json`; tokens in the macOS keychain under `oauth:{provider}:{account}` (never printed; the `MODEL_API_KEY` path is untouched) | No client IDs ship, so no live Gmail/Outlook sign-in has been exercised end to end |
| Account metadata | Real local store | `ServiceRegistry` JSON store with descriptors, multi-account support, expiry-derived status, revoked-wins semantics; verified to hold no token/secret blobs | Metadata only — Python never sees token values |
| Fake-mail provider | Clearly-labeled fixture | `FakeMailProvider` / `FakeMailAdapter` serve 3 fictional messages; every payload carries `FAKE — test fixture, not live mail` on the envelope, each message, and the subject line | Not live mail; read-only (the provider has no send path) |
| Gmail connector | Real client, unwired credentials | Request shapes verified against `MockTransport` (list `q`/`maxResults`, `get format=full`, threads); without a token supplier it raises `MailNotConnectedError` with zero HTTP calls | Live tokens stay in the Rust keychain and no bridge passes them to Python yet, so live Gmail reads fail closed as "not connected" |
| Outlook connector | Skeleton only | Raises labeled `OUTLOOK SKELETON — not implemented` on all 4 ops; the HTTP layer surfaces it as a 400, never as mail | No live path at all |
| Email send | Fake outbox only | `gmail.send` runs only for `fake-mail` after AIP grant + explicit approval, recorded in the fake outbox so tests can assert denied sends sent nothing | Real-provider send fails loudly with "Nothing was sent"; there is no live send path by design, and voice can only request approval, never send |
| Service permissions (M8) | Real enforcement | 13 service tools gated on exact per-agent/per-goal grants; sensitive ops need explicit approval even with a grant; revoke takes effect immediately; denials emit `service.access_denied` and map to high-severity Guard cases; Scenario C (unauthorized send denied, empty outbox) is tested | Grants are scheduler methods exercised by tests; no HTTP routes were added for grant/revoke |

Mail reads through the API are never auto-picked: zero matches → 404 with no
fabricated content, several matches → 409 with candidates, revoked/expired →
403. All routes require the per-launch app Bearer [REDACTED] (`401` otherwise).

## Verification scenarios

- **Scenario A — media and learning library** (`src/experience/learningLibrary.test.ts`,
  vitest): normal YouTube/Vimeo links become provider embeds, PDF and direct
  video files are classified, research evidence is grouped into the goal's
  library, and invalid lookalike domains are rejected.
- **Scenario B — fake-provider OAuth connect + read + revoke**
  (`runtime/tests/test_phase5_scenarios.py`, pytest): connect records metadata
  with no secrets; list/search/read/thread return FAKE-labeled fixtures;
  revoke → 403 with no message content; disconnect → 404 with nothing
  fabricated. The keychain half of the connect is covered by `cargo test`
  (`oauth.rs` unit tests, incl. the RFC 7636 vector).
- **Scenario C — unauthorized send denied** (`runtime/tests/test_service_permissions.py`,
  from M8): ungranted send is denied with an empty outbox; approved fake-mail
  send records exactly one outbox entry.

## Dev commands

```sh
.venv/bin/python -m pytest runtime/tests -q --deselect runtime/tests/test_demo.py  # 141 passed, 5 skipped
npm test                       # vitest: 40 passed
cargo test --manifest-path src-tauri/Cargo.toml   # 13 passed
npm run build                  # svelte-check (0 errors) + vite build
```

`runtime/tests/test_demo.py` (6 tests, 5 binding loopback sockets) fails with
`PermissionError: [Errno 1]` inside the macOS sandbox; this predates Phase 5
and is unrelated to it. No live-model evals are part of these gates
(`runtime.experiments.live_eval` is never run here).

## Module map

- `src/experience/surfaceManager.ts` — pure audio-focus policy + capability probes
- `src/experience/accountResolver.ts` — frontend account disambiguation
- `src-tauri/src/oauth.rs` — PKCE flow, keychain storage, connect/refresh/disconnect commands
- `runtime/services/registry.py` — provider descriptors + account metadata store
- `runtime/services/fake_provider.py`, `runtime/services/fake_services.py` — labeled fixtures
- `runtime/connectors/` — Gmail client, Outlook skeleton, fake adapter, shared resolver
- `runtime/services/permissions.py`, `runtime/aip/policy.py` — grant enforcement
