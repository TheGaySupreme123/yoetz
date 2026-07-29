# Run-5 residuals: make the reviewer useful and the recovery path truthful

**Date:** 2026-07-28

**Source:** the provider-model-catalog dogfood preserved on commit `45e1272` and the interrupted
Claude Code root-cause session recovered from its local task outputs.

Seven defects, seven sequential implementation boundaries. **The numbering is execution order.**
This directory is design only: it creates no issue, changes no runtime code, and grants no
design-gate acknowledgement.

Drafted against `main` at `eda6623` (PRs #52, #54, #56, #58, #60, #62, and #63 merged).

## Corrected verdict from the investigation

The run proved activation, durable publication through frontier `37`, external Fireworks dispatch,
and receipt issuance/replay. It did **not** prove that the semantic reviewer was useful.

The three accepted semantic responses all took the only easy branch: no challenges and no
findings. The four longer responses were rejected by a Python contract stricter than the JSON
Schema sent to the provider. More importantly, the reviewer was given only a dependency digest,
frontier, and deterministic finding IDs; it did not receive the goal, claims, obligations,
decisions, timeline, evidence, or excerpts ADR-006 says it reviews. A zero-finding result therefore
meant “nothing useful was supplied,” not “nothing was wrong.”

The publish failure also has a narrower root cause than the first review claimed. The malformed
`decision_recorded.authority` is already rejected by the published request schema with the safe
pointer `/event_drafts/0/payload/authority`. Before returning that error, the MCP bridge performs
an envelope-first operation lookup. In the dogfood, that secondary `status view=operation` read
raised the already-fixed `AttributeError`; its `read_projection_failed` result replaced the
original authoring error. The durable diagnostic ring proves the correlation IDs belonged to
internally generated `status` request IDs, not to `publish_work`.

## Execution order

| # | Plan | Defect | Why here |
| --- | --- | --- | --- |
| 01 | [Recovery must not mask authoring errors](01-recovery-must-not-mask-authoring-errors.md) | A failed recovery read replaces a known-invalid publish body with a false read-only remedy | Launch gate: the prescribed recovery path must not erase the error that invoked it. |
| 02 | [One provider judgment contract](02-one-provider-judgment-contract.md) | The provider is constrained by a weaker schema than Yoetz enforces | Until generation and consumption agree, useful challenge output is systematically discarded. |
| 03 | [Send the real frozen review packet](03-send-the-real-frozen-review-packet.md) | Semantic review receives two digests and an empty ID list instead of the ADR-006 case | This is the value proposition: the reviewer must have something bounded and meaningful to review. |
| 04 | [Durable semantic attempts and retry budget](04-durable-semantic-attempts-and-retry-budget.md) | Retry/timeout configuration is inert and semantic attempt tables are unused | Reliability and postmortem evidence come after one attempt can be valid and useful. |
| 05 | [Actionable root-level validation](05-actionable-root-level-validation.md) | `dependentRequired` and other object rules collapse to a generic start error | Small, independent authoring fix with no semantic dependency. |
| 06 | [Authorable obligation resolution](06-authorable-obligation-resolution.md) | Meaning-changing obligation resolution yields a misleading type error with no worked correction | Makes closure possible without reducer-source archaeology. |
| 07 | [Distinguish claimed time from accepted time](07-distinguish-claimed-time-from-accepted-time.md) | Read projections show caller time but hide the digest-bound service acceptance time | Honesty repair; no ordering or storage-integrity defect exists. |

## Decisions made before implementation

These decisions are the baseline for the seven plans.

- **No raw provider response retention.** ADR-006 decision 8 remains intact. Diagnostics retain
  bounded structural parse/validation facts, not provider plaintext.
- **No skew rejection for `occurred_at`.** It is a caller assertion. Ledger ordering continues to
  use `ingestion_sequence`; the service exposes `accepted_at` beside the claim instead of pretending
  it can verify when outside work occurred.
- **Recovery authority beats body validation only when recovery is authoritative.** A found
  pending/complete/quarantined operation wins. An authoritative absent result returns the original
  validation error. An unavailable lookup returns an explicit ambiguity result and never claims
  that no durable state changed.
- **One judgment contract.** The machine-enforced provider schema and the consumer normalization
  contract have one owning model and one generated/schema-tested representation.
- **Provider ordering is not semantic.** `cited_refs` are normalized into canonical order after
  validation; an otherwise valid challenge is not discarded because the model emitted refs in
  narrative order.
- **One check, several physical attempts.** Retries stay inside one durable semantic operation and
  one final `check_recorded` event. Every physical dispatch has fresh authorization, receipt,
  attempt ID, and bounded attempt record as ADR-006 requires.
- **Obligation resolution repeats meaning.** `status` and `resolution_evidence_refs` are the only
  fields that may differ from the open obligation. The public error names which fixed schema fields
  differ without echoing their values.
- **Temporal honesty is per event.** Add `accepted_at` to event-history projection and guidance;
  do not add a new aggregate coverage dimension in this set.

## Required design gates

Plans 01, 02, 03, 04, 05, and 07 touch protocol, privacy/egress, durability, or public wire
contracts. Under `CONTRIBUTING.md`, their future implementation requires an acknowledged issue
before a PR. This planning branch intentionally does not create those issues.

Plan 06 is partly a documentation/error-contract change and partly reducer behavior. Treat it as
design-gated if its implementation changes the admitted resolution invariant rather than only
making the existing invariant authorable.

## Exact-runtime gate before any run-6 claim

The provider-catalog run recorded checkout baseline `eda6623`, but it did not prove that the live
service process was rebuilt, reinstalled, and restarted from that ref. Its diagnostic ring showed
the pre-fix `status view=operation` `AttributeError` even though the focused tests on current
`main` pass.

The acceptance run after these plans must record, separately:

1. exact source ref and clean/known patch state;
2. built wheel filename and SHA-256;
3. installed distribution location and version;
4. service stop/start and new instance/generation;
5. MCP handshake against that instance;
6. the exact installed code/artifact identity used by the service;
7. focused regression result before the live semantic call.

Registration, a matching `0.1.0` version string, or a source checkout alone is not activation of
the newly built runtime.

## Run-6 acceptance scenario

The final dogfood must deliberately exercise the repaired branches:

- submit a malformed `decision_recorded.authority` with a fresh request ID and receive a
  field-pointed authoring error;
- simulate or inject recovery-read unavailability and receive an ambiguity-safe same-ID remedy,
  never “no durable state changed”;
- publish a real goal, obligations, claim, decision, deterministic finding basis, and bounded
  evidence excerpt;
- make the semantic reviewer return at least one valid citable challenge;
- observe either first-attempt success or durable bounded retry records for every physical attempt;
- resolve an obligation incorrectly once and receive the exact invariant/field correction;
- read history and see both caller `occurred_at` and service `accepted_at`;
- close with a current receipt and replay it.

## What this set does not address

- The provider-model-catalog product diff on branch
  `codex/provider-model-catalog-dogfood-20260728` / PR #64. It remains a separate intake and review
  decision.
- Raw provider-response capture. Any future encrypted diagnostic capture needs a separate ADR-006
  amendment, human authorization, and retention policy.
- Live repository browsing by the reviewer. The case builder may use only already recorded,
  frozen, policy-selected material.
- A global metrics backend. Plan 04 adds durable bounded attempt accounting and status summaries,
  not telemetry or another network channel.

