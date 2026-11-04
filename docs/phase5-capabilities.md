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
| Netflix | External fallback only — never embedded | A Netflix resource opens in the system browser via the validated `open_system_browser` Tauri command (http(s), standard ports, no credentials, Netflix allowlist, main-window only, no shell); the panel shows a DRM explanation + fallback label + Intent-Space return note, and the old false "system browser" toast for in-app resources was fixed | Embedded DRM playback is unsupported; KeplerV does not try to bypass it |

Voice control (`OPEN_MEDIA` / `PAUSE_MEDIA` / `RESUME_MEDIA` / `CLOSE_MEDIA`) is
wired end to end: `runtime/voice/intent.py` classifies the intent,
`interpret_voice` proxies through `POST /interpret`, and `App.svelte` opens the
selected goal's learning library. A media ID is not required; a requested title
can open a matching item.

## Connected services (OAuth + email)

| Area | Status | What is real | What is not |
| --- | --- | --- | --- |
| OAuth PKCE flow | Real protocol, user-supplied credentials | RFC 7636 S256 PKCE, state/nonce, authorize-URL builder, 127.0.0.1 loopback callback with state/path validation, code exchange, refresh, best-effort revoke; public client IDs pasteable in the Connected services card (`oauth_client_id_save`, stored non-secret in `oauth-client-ids.json`) or via `KEPLERV_OAUTH_CLIENT_ID_{PROVIDER}`; tokens in the macOS keychain under `oauth:{provider}:{account}` (never printed; the `MODEL_API_KEY` path is untouched); full connect→refresh→read→send→revoke→fail-closed flow proven end to end against a loopback mock OAuth IdP with real S256 PKCE (`test_mock_oauth_idp.py`) | No client IDs ship — a judge pastes their own Google Desktop-app / Entra IDs per `docs/mail-setup.md` for live sign-in; without them, live paths fail closed as "not connected" |
| Account metadata | Real local store | `ServiceRegistry` JSON store with descriptors, multi-account support, expiry-derived status, revoked-wins semantics; verified to hold no token/secret blobs; Rust pushes best-effort metadata sync (`POST /accounts/sync`, extra fields rejected 422) | Metadata only — Python never sees token values except per-request bridged access tokens (below), never stored or logged |
| Token bridge (Rust → sidecar) | Real, sidecar-only | Rust resolves the requested account from its own metadata, reads the keychain entry, and forwards ONLY the access token in sidecar-only headers over the authed 127.0.0.1 channel — per request, bound to the resolved account, never in JS state; plus an in-memory-only vault (`POST/DELETE /accounts/tokens`) for agent background tasks, pushed on connect/refresh/start and evicted on disconnect/revoke | Tokens never touch frontend JS, disk, or logs; disconnect/revoke evicts immediately |
| Fake-mail provider | Clearly-labeled fixture | `FakeMailProvider` / `FakeMailAdapter` serve 3 fictional messages; every payload carries `FAKE — test fixture, not live mail` on the envelope, each message, and the subject line | Not live mail; read-only (the provider has no send path) |
| Gmail connector | Real client, bridged credentials | List/search/get/thread via official Gmail shapes through the token bridge; LIVE-labeled payloads; without a connected account it raises `MailNotConnectedError` with zero HTTP calls | Needs the user's OAuth sign-in (client ID + connect); disconnected reads fail closed, never fabricated |
| Outlook connector | Real MS-Graph client | List/search/get/thread via official Graph shapes plus `POST /me/sendMail` (delegated `Mail.Send`), through the same token bridge and AIP gating as Gmail | Needs the user's Entra OAuth sign-in; disconnected calls fail closed |
| Email send | Real send, AIP-gated | `gmail.send` / `mail.send` run for live providers after AIP grant + explicit human approval (goal-panel approval list shows TO/SUBJECT/VIA live-or-fake + send-scope note + body preview), returning a LIVE-labeled receipt; fake-mail sends still record in the fake outbox | Denied, unscoped, or disconnected sends transmit nothing ("Nothing was sent"); voice can only request approval, never send |
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
.venv/bin/python -m pytest runtime/tests -q    # 319 passed, 8 skipped (incl. loopback-bind tests; run outside the macOS sandbox)
npm test                                       # vitest: 95 passed
cargo test --manifest-path src-tauri/Cargo.toml   # 23 passed
npm run build                  # svelte-check (0 errors, 0 warnings) + vite build
```

Mail-focused subset (no live-model calls; mock IdP binds loopback sockets,
so run outside the sandbox):

```sh
.venv/bin/python -m pytest runtime/tests/test_mock_oauth_idp.py runtime/tests/test_mail_token_bridge.py runtime/tests/test_gmail_send.py runtime/tests/test_outlook_connector.py runtime/tests/test_mail_api.py runtime/tests/test_phase5_scenarios.py -q
cargo test --manifest-path src-tauri/Cargo.toml oauth
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
passed, svelte-check 0 errors / 0 warnings. For live mail, paste your own
Google Desktop-app / Entra client IDs in the Connected services card (see
`docs/mail-setup.md`); without them the UI shows not-connected hints and
live paths fail closed. Committed tests make no live-model or live-mail
calls.

## Module map

- `src/experience/surfaceManager.ts` — pure audio-focus policy + capability probes
- `src/experience/accountResolver.ts` — frontend account disambiguation
- `src-tauri/src/oauth.rs` — PKCE flow, keychain storage, connect/refresh/disconnect commands
- `runtime/services/registry.py` — provider descriptors + account metadata store
- `runtime/services/fake_provider.py`, `runtime/services/fake_services.py` — labeled fixtures
- `runtime/connectors/` — Gmail client, Outlook MS-Graph client, fake adapter, shared resolver
- `runtime/services/mail_token_vault.py` — in-memory-only bridged-token vault
- `src-tauri/src/runtime_bridge.rs` — token-bridging headers + `runtime_guard_*` bridge commands
- `src/experience/mailSendApproval.ts`, `src/experience/autoContainment.ts`, `src/experience/federationStatus.ts` — send-approval detail, auto-containment notices, federation labels
- `runtime/services/permissions.py`, `runtime/aip/policy.py` — grant enforcement
