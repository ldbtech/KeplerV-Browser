# Phase 6: Trust Gateway

External agents (the fictional marketplace merchants) talk to KeplerV only
through the Trust Gateway. There is no direct-contact path: every
cross-boundary message is SSRF-checked, schema-validated, correlated,
budgeted, and recorded in a per-session transcript.

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
