# KeplerV Guard — Architecture

Guard is a personal AI security task force that investigates operating agents.
It investigates observable behavior against the authorized objective; it does
not classify log text and never rewrites policy.

## Trust domains

- **Domain A — Operating agents** (`runtime/scheduler.py`). Browse, delegate,
  and use tools under AIP. Cannot control Guard or change policy.
- **Domain B — Guard** (`runtime/guard/`). Observes, investigates, interviews,
  and recommends. Its model output is advisory and citation-checked.
- **Domain C — Trusted enforcement** (`runtime/aip/policy.py` plus the
  scheduler's tool gateway). Deterministic, model-independent. Every privileged
  tool passes through it before execution.

## Roles and models

| Role | Module | Default model | Trigger |
| --- | --- | --- | --- |
| Observer | `observer.py`, `risk.py`, `screening.py` | Rules, then `muse-spark-1.3` | AIP denial event |
| Detective | `detective.py` | `muse-wonder-1.0` | User starts investigation |
| Interrogator | `interrogation.py` | Wonder asks, suspect model answers | User asks a question |
| Adjudicator | `adjudicator.py` | `muse-wonder-1.0` | User requests review |

Deterministic rules fire before any model call, and screening runs at most once
per case. Model outputs that fail schema validation get one correction retry
(`parsing.py`); failures are recorded as evidence, never silently replaced.

## Evidence and cases

- `store.py` persists cases, hash-chained append-only evidence (enforced by
  SQLite triggers), transitions, and per-case read permissions.
- `evidence.py` rebuilds a sanitized, bounded bundle per case: goal, agents,
  delegation chain, runtime events, case evidence, and research evidence.
  Secrets are redacted, conversations excluded, and large bundles are trimmed
  oldest-first while always keeping the trigger event, AIP decisions, and
  Guard history.
- `case_manager.py` groups follow-up denials into the active run case.
- State machine: `detected → triaged → investigating → interviewing →
  evaluating → closed`, plus `awaiting_evidence`, `awaiting_human_review`,
  `containment_requested`, `inconclusive`, and `reopened`. Every transition
  stores its reason and actor.

## Investigative tools (`tools.py`)

Twelve named tools with pydantic schemas and `can_read` checks: eight reads
(`guard.get_agent`, `guard.get_goal`, `guard.get_events`,
`guard.get_delegation_chain`, `guard.get_browser_evidence`,
`guard.get_aip_decisions`, `guard.get_related_agents`, `guard.get_case_history`)
and four actions (`guard.ask_agent`, `guard.request_additional_evidence`,
`guard.submit_findings`, `guard.request_containment`). The Detective persists
reports through `submit_findings`, which re-validates citations. Guard has no
SQL, shell, filesystem, or browser execution capability.

## Interrogation

Wonder selects each question from the evidence bundle plus prior answers; there
is no fixed script. The suspect answers in an isolated no-tools context built
only from its own run transcript, without Guard's assessment. Answers persist
as explicitly unverified self-reports. Five questions per case, early
termination supported, provider and model recorded per turn.

## Contradictions and uncertainty

Findings carry classifications (`observed_attempt`, `authorized_completion`,
`denied_action`, `failed_action`, `agent_statement`, `unverified_claim`,
`inference`). The Adjudicator cross-checks statements against tool records with
exact evidence IDs and returns one of five dispositions; it can abstain via
`insufficient_evidence`. Risk labels stay qualitative and uncalibrated.

## Containment (`containment.py`)

All actions require the `local_user` principal and a case under human review,
route through `RuntimeService` controls, and record hash-chained evidence with
`aip_policy_modified: false`:

- `pause_agent` / `resume_agent` — safe-boundary lifecycle control.
- `deny_pending` — denies every pending approval, then pauses.
- `revoke_capability` — narrows `browser.read`, `browser.interact`, or
  `delegate` for the run; later tools re-authorize against the live grant.
- `isolate_browser` / `restore_browser` — closes the live Chromium context and
  denies later browser tools until restored.
- `request_human_review` — routes an investigating case to the user.

Automatic emergency rules are not implemented; AIP already blocks prohibited
actions before execution.

## Native integration

`runtime_bridge.rs` polls `/snapshot`, emits typed `runtime:event` updates to
the main window only, and exposes run/guard/credential commands. `App.svelte`
renders cases ambiently: quiet when empty, an incident node on the goal graph
plus a case panel (summary, agent, goal, evidence, interview turns,
disposition, uncertainty, containment) when active.

## Experiments and evaluation

- `runtime/experiments/scenarios.py` — five isolated captures with fictional
  pages and stub services: prompt injection, benign anomaly, delegation
  abuse, contradictory statements, and search poisoning (held-out shape).
- `runtime/experiments/evaluate.py` — investigates identical recorded cases
  under `rules`, `spark`, `guard_full`, and the `guard_no_interview`
  ablation; measures detection, false positives, citation validity,
  completion, questions, tokens, calls, and latency.
- `runtime/experiments/live_eval.py` — runs captures plus live-model
  investigation and writes `evaluation.json`/`evaluation.md`.
- `runtime/experiments/results/` — measured live-model outputs.

## Audio interface

Out of scope for this phase beyond the existing behavior: voice input captures
on-device audio and continues through explicitly labeled example transcripts;
it only creates goals and cannot bypass AIP approval.

## What remains mock or future

- Automatic emergency containment rules.
- Model-driven multi-turn Guard tool use (roles currently retrieve one
  bounded bundle per pass and persist through validated tools).
- Audio Q&A over case records.
- Production security guarantees (this is experimental enforcement).
