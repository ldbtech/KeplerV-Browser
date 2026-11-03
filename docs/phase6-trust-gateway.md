# Phase 6: Trust Gateway

External agents talk to KeplerV only through the Trust Gateway. There is no
direct-contact path: every
cross-boundary message is SSRF-checked, schema-validated, correlated,
budgeted, and recorded in a per-session transcript.

The user-facing gateway also supports real public A2A agents. For a goal where
external contact is enabled, discovery reads the A2A Agent Card from
`/.well-known/agent-card.json` (with the older `/.well-known/agent.json`
fallback), and text requests use the card's JSON-RPC interface. KeplerV
supports public HTTPS cards with no required authentication and A2A protocol
versions 1.0 and 0.3. It does not scan the internet or claim there is a single
global agent directory. The user can provide a card URL, or the researcher may
find one indexed by ordinary web search. Opening a session and every outbound
message still require explicit human approval through AIP. Agent Card and
message content remain untrusted. Authenticated agents, streaming, and
file/image parts are not supported by this adapter yet.

Inbound A2A text is screened in the background for observable prompt-override,
credential-request, coercion, and redirect markers. A match adds evidence to
the session's Guard case. The deterministic screen is a narrow heuristic; it
does not guarantee detection of novel attacks or make claims about an agent's
intent.

## Trust tiers

The external-agent registry (`runtime/gateway/registry.py`) records each
agent's identity tier plus provenance (who asserted the record, when, with
what evidence refs):

- `verified` — identity confirmed against a trusted record.
- `self_declared` — the agent asserts its own identity; nothing confirms it.
- `unknown` — no identity information (default for new records).
- `suspected` — past exchanges gave reason to distrust the agent.

Registry records never hold secrets: registration rejects secret-bearing
fields outright. Declared capabilities come from discovery descriptors;
observed capabilities are updated from real replies.

## Gateway protocol

Sessions (`runtime/gateway/sessions.py`) live in sqlite tables beside the
runtime store. Each message carries a correlation id (`reply_to`), a label
(`live` or `replay`), and an untrusted flag on inbound content. Inbound
merchant content is data, never instructions: the negotiation driver
redacts threat-shaped fields before the local policy sees them, never
fetches URLs from inbound content, and never sends credentials.

Local agents reach the gateway through `GatewayTools`
(`runtime/gateway/tools.py`): discover, open/close session, send, verify.
Outbound payloads pass data minimization — secret-, account-, or
session-bearing fields refuse the whole send (naming the fields), never
silently stripped.

## SSRF rules

`runtime/gateway/ssrf.py` validates every endpoint before any fetch:

- Only `https`, or `http` to an explicitly allowlisted loopback fixture.
- DNS is resolved and **every** resolved IP must be acceptable; private,
  link-local, loopback, and cloud-metadata ranges are denied.
- No credentials in URLs, no automatic redirect following; each redirect
  hop is re-validated (discovery refuses cross-host hops outright).
- Loopback merchant fixtures are allowed only via an explicit `host:port`
  allowlist (parameter or `KEPLERV_GATEWAY_FIXTURE_ALLOWLIST`).

## Claim verification

`runtime/verification/claims.py` assigns one of four verdicts per merchant
statement, citing only caller-supplied evidence plus the checks it ran
itself:

- `cryptographically-attested` — a presented attestation verifies.
- `independently-observed` — matches the trusted catalog.
- `statement-made` — recorded as said, nothing more.
- `unverified` — failed, missing, or conflicting evidence.

The trusted catalog (`runtime/merchants/trusted_catalog.py`) is the
canonical authority: fictional products with a SHA-256 manifest and
fixture-only HMAC attestations (stdlib `hmac`/`hashlib` — established
primitives, no custom crypto; the key is hard-coded test-only material
that never touches the keychain).

## Fake / live / replay labeling

- `fake` — synthetic fixtures, deterministic rules, fake-model policy.
- `replay` — sessions against fixtures (including the loopback live
  transport); recorded runs are labeled `[REPLAY]`.
- `live` — real loopback HTTP to fixtures (explicit opt-in only); data is
  still fictional and the report says so.

The UI shows session labels verbatim and marks inbound content untrusted.
Voice answers speak only from fetched session records.
