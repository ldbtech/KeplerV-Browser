# Mail setup: OAuth client IDs, token bridge, and live send

KeplerV reads and sends mail through the providers' official APIs. Tokens
live in the system keychain under `oauth:{provider}:{account}` entries —
separate from model API credentials — and never enter frontend JS state.

## Providers and labels

| Provider | What it is | Label |
| --- | --- | --- |
| Gmail | Live Gmail API (read + send) | `LIVE — transmitted via the provider mail API` on send receipts |
| Outlook | Live Microsoft Graph API (read + send) | same live label |
| Fake Mail | Local fictional fixture, no network | `FAKE — …` on every payload and the fake outbox |
| Mock (tests only) | Loopback OAuth + resource fixture in `test_mock_oauth_idp.py` | `MOCK IdP — …` |

Without a connected account every mail path fails closed: reads return
nothing and sends report `Nothing was sent` instead of substituting data.

## Scopes requested at sign-in

| Provider | Scopes |
| --- | --- |
| Gmail | `openid`, `email`, `https://www.googleapis.com/auth/gmail.readonly`, `https://www.googleapis.com/auth/gmail.send` |
| Outlook | `openid`, `email`, `Mail.Read`, `Mail.Send` (delegated) |

Accounts connected before the send scopes existed must reconnect before
sending; the scheduler reports which scope is missing.

## Client IDs (user-pasted, non-secret)

Gmail and Outlook sign-in need only a **public OAuth client ID** — KeplerV
never uses or accepts a client secret. Paste each ID in the app under
*Providers → Connected services → Gmail/Outlook client ID*, or set the
environment variable before launch:

- `KEPLERV_OAUTH_CLIENT_ID_GMAIL`
- `KEPLERV_OAUTH_CLIENT_ID_OUTLOOK`

The file on disk is `oauth-client-ids.json` in the KeplerV app data
directory (`{"gmail": "…", "outlook": "…"}`). Client IDs are public
identifiers, so plain file storage is correct; they are never written to
the keychain and never sent anywhere except the provider's own
authorization endpoint.

## Swap in real Google credentials

1. Open the [Google Cloud console](https://console.cloud.google.com), create
   or pick a project, and enable the **Gmail API**.
2. *APIs & Services → OAuth consent screen*: choose **External**, fill the
   app name and support email, and add the Gmail read + send scopes above.
   While the app is in testing mode, add your Gmail address under *Test
   users*.
3. *APIs & Services → Credentials → Create Credentials → OAuth client ID*:
   choose application type **Desktop app**. No redirect URI needs typing —
   desktop clients may use any loopback port, and KeplerV listens on
   `http://127.0.0.1:{port}/oauth/callback` with a fresh port per sign-in.
4. Copy the client ID (it ends with `.apps.googleusercontent.com`) into
   KeplerV's Gmail client-ID field.
5. Press **Connect Gmail**, approve access in the KeplerV sign-in window,
   and confirm the account appears as connected.

## Swap in real Microsoft credentials

1. Open the [Entra app registrations](https://entra.microsoft.com) portal
   (*Identity → Applications → App registrations → New registration*).
2. Name the app, choose **Accounts in any organizational directory and
   personal Microsoft accounts**, and under *Redirect URI* pick **Mobile
   and desktop applications** with `http://localhost` (loopback clients
   may use any loopback port; KeplerV uses
   `http://127.0.0.1:{port}/oauth/callback`).
3. Under *API permissions → Add a permission → Microsoft Graph →
   Delegated permissions*, add `openid`, `email`, `Mail.Read`, and
   `Mail.Send`, then grant admin consent if your tenant requires it.
4. Copy the **Application (client) ID** (a GUID) into KeplerV's Outlook
   client-ID field. No client secret is needed.
5. Press **Connect Outlook** and approve access in the KeplerV sign-in
   window.

## How the token bridge works

```
Rust (keychain owner)                Python sidecar (127.0.0.1 + app token)
─────────────────────                ─────────────────────────────────────
oauth_connect/refresh
  keychain write + metadata file
  ── POST /accounts/sync ──────────► metadata upsert (non-secret only;
                                      extra fields rejected with 422)
  ── POST /accounts/tokens ────────► in-memory vault (agent background
                                      tasks; never disk/logs/JS)

runtime_mail_list/get
  resolve account from Rust metadata
  read keychain entry
  ── GET /mail/… ──────────────────► per-request X-Keplerv-Mail-Token
      + binding headers               honored only for the resolved
                                      account; never stored

oauth_disconnect
  remote revoke (best-effort) +
  keychain/metadata delete
  ── sync(removed) + DELETE ───────► metadata dropped, vault evicted
```

Direct UI reads prefer the per-request header; agent mail tools use the
vault entry pushed at connect/refresh. A `401` from either provider maps
to "Reconnect the account" — sessions are never retried silently.

## Live send approval flow

`gmail.send` / `mail.send` require **both** a per-agent/per-goal
`gmail.send` grant (`grant_service_access`, never inherited by children)
**and** one explicit human approval per execution. After approval the
scheduler transmits through `GmailSender` / `OutlookSender` and returns a
live-labeled receipt. Denying the approval never touches the provider API,
and the fake-mail fixture keeps its separate labeled outbox.

## Tests

- `runtime/tests/test_outlook_connector.py`, `test_gmail_send.py`:
  MockTransport wire shapes + fail-closed paths. No live calls.
- `runtime/tests/test_mail_token_bridge.py`: vault, sync/token routes,
  per-request header binding, and grant→approval→live-send with mocks.
- `runtime/tests/test_mock_oauth_idp.py`: loopback IdP speaking real
  OAuth+PKCE end to end (connect → token → refresh → read → send →
  revoke → fail closed) against the real connector classes.
- `cargo test` (`src-tauri/src/oauth.rs`): PKCE vectors, callback
  validation, client-ID validation/merge, token-expiry extraction, and the
  bridge account-resolution mirror.

Live provider paths are exercised only through user-connected accounts;
committed tests use fakes, mocks, and loopback fixtures exclusively.
