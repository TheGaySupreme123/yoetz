# src/yoetz/domain/findings.py — canonical finding values and ranking inputs

**Wave:** B | **ADRs:** ADR-002, ADR-006 | **Imports (spec-tree):** `protocol/coverage.md`,
`protocol/errors.md`, `protocol/models.md` (`SemanticStatus`, `SemanticReason`, pair validator),
`domain/values.md`
**Imported by:** `kernel/ranking.md`, `kernel/deterministic_checks.md`, `domain/receipts.md`,
`application/check.md`, `application/respond.md`, `cli/render.md`

## Purpose

Findings are the user-visible output of Yoetz’s checking loop. This file defines the immutable
finding values that deterministic policies, semantic evaluation, and receipts all agree on. It is
the place where findings remain sparse, ranked, and coverage-bound instead of turning into free-form
assistant prose.

## Public surface

| Name | Signature (natural language) |
|---|---|
| `FindingKind` | enum of deterministic and semantic finding kinds from `specs/INTERFACES.md` |
| `FindingOrigin` | enum: `deterministic`, `semantic_model_derived` |
| `CheckVerdict` | enum: `action_required`, `no_issue_detected`, `insufficient_coverage`, `incomplete_check` |
| `WaiverScope` | enum with the sole v0.1 value `finding_only` |
| `ResponseDisposition` | enum: `acknowledged`, `rejected`, `waived` |
| `FINDING_KIND_TRAITS` | immutable mapping `FindingKind -> (required_priority, actionable)` registered in `specs/INTERFACES.md` |
| `CandidateFinding` | frozen ID-free value produced by pure deterministic/semantic post-validation |
| `Finding` | frozen dataclass `(finding_id, kind, origin, priority, summary, detail, subject_refs, policy_id, policy_version, subject_frontier, coverage, provenance)` |
| `DeterministicFinding` | alias of `Finding` for deterministic-origin findings |
| `SemanticFinding` | alias of `Finding` for semantic-origin findings |
| `RankedFindings` | frozen dataclass `(findings, suppressed_count, verdict, coverage)` |
| `SemanticProvenance` | frozen provenance record for semantic findings |
| `SamplingParams` / `TokenUsage` / `CostFields` | frozen schema-shaped provenance components |
| `SemanticDispatchKind` / `SemanticFailureClass` | closed finalized-provenance enums |
| `finding_from_json(value)` / `finding_to_json(finding)` | exact finding schema codecs |
| `semantic_provenance_from_json(value)` / `semantic_provenance_to_json(value)` | exact finalized-provenance codecs |
| `rank_key(finding)` | deterministic sort key helper |

## Behavior

### Nominal values and exact record shapes

Every enum in this file is a `str`-valued enum and every record below is
`@dataclass(frozen=True, slots=True)`. `FindingOrigin`, `FindingKind`, `CheckVerdict`,
`WaiverScope`, and `ResponseDisposition` are the nominal values imported by events, receipts,
ranking, and renderers; those consumers do not define parallel enums.

`CandidateFinding` has exactly these fields, in constructor order:

```text
kind: FindingKind
origin: FindingOrigin
priority: int
summary: str
detail: str
subject_refs: tuple[EventId | ObligationId | ClaimId, ...]
policy_id: str
policy_version: str
subject_frontier: Frontier
coverage: Coverage
provenance: SemanticProvenance | None = None
```

`Finding` has the same fields with one leading
`finding_id: FindingId`. `RankedFindings` is exactly
`findings: tuple[Finding, ...]`, `suppressed_count: int`, `verdict: CheckVerdict`, and
`coverage: Coverage`. `priority` is an `int` but not `bool`; `suppressed_count` is an `int` but not
`bool` in `0..2**53-1`. Subject refs are 1..64, sorted by unsigned ASCII bytes, duplicate-free,
and limited to the three registered ID kinds. Text and policy identities use the bounds and
patterns in `schemas/findings/finding-1.0.0.schema.json`.

The `policy_id` / `policy_version` pair on `Finding` is derived from the finding kind's unique
owning built-in pack after validation. The partition is frozen and disjoint: the ten work-integrity
kinds map to `work-integrity/0.1.0`, the four research-evidence kinds map to
`research-evidence/0.1.0`, and `semantic-review` is only a review-context recipe label. A
`ReviewerChallenge` carries no policy identity and cannot choose or override this derived field.

Final semantic provenance uses these supporting values:

```text
SemanticDispatchKind = external | local_model
SemanticFailureClass = authentication | authorization | provider_outage | quota_exhausted |
                       rate_limited | response_schema | timeout | transport |
                       unsupported_profile

SamplingParams(max_output_tokens: int,
               temperature: str | None = None,
               top_p: str | None = None,
               seed: int | None = None)
TokenUsage(input_tokens: int, output_tokens: int, total_tokens: int)
CostFields(currency: str, input_microunits: int,
           output_microunits: int, total_microunits: int)
```

The integer fields above are domain integers. Their provenance wire form is the canonical unsigned
decimal string required by the schema; they are never floats or `Decimal`. Usage, cost, seed, and
latency values are in `0..2**53-1`; `max_output_tokens` is in `1..8192`. Temperature and `top_p`
remain the schema's exact fixed-decimal strings, so decoding and re-encoding cannot change their
spelling.

`SemanticProvenance` has exactly these fields (required fields first only to make the Python
constructor unambiguous; canonical JSON key order is independent):

```text
provider: str
endpoint_profile_id: str
endpoint_profile_version: str
model: str
sdk_version: str
prompt_digest: str
schema_digest: str
policy_digest: str
privacy_policy_digest: str
sampling_params: SamplingParams
latency_ms: int
semantic_attempt_id: str                  # att_ ID
dispatch_kind: SemanticDispatchKind
privacy_receipt_id: str                   # egr_ ID
status: SemanticStatus
reason: SemanticReason
provider_request_id: str | None = None
token_usage: TokenUsage | None = None
cost_fields: CostFields | None = None
failure_class: SemanticFailureClass | None = None
egress_authorization_id: str | None = None # aut_ ID
local_disclosure_reservation_id: str | None = None # ppr_ ID
request_commitment: str | None = None
```

Construction calls `protocol.models.validate_semantic_outcome`. Final provenance permits only the
terminal statuses represented by its schema: `succeeded`, `refused`, `timeout`, `invalid`,
`unavailable`, `late`, `stale`, and `failed`. `external` requires
`egress_authorization_id` and `request_commitment` and forbids
`local_disclosure_reservation_id`; `local_model` requires the local reservation and forbids both
external fields. `privacy_receipt_id` is required in both branches. All IDs, digests, commitments,
identities, fixed decimals, and optional fields are validated exactly as
`schemas/findings/semantic-provenance-1.0.0.schema.json`; no provisional adapter object satisfies
this type.

`SemanticStatus`, `SemanticReason`, `VALID_SEMANTIC_REASONS`, and their validator are nominally
owned by `protocol/models.py`. This module imports them; it does not redeclare or re-export a
lookalike enum. `ports/semantic.py` may re-export those same objects for port consumers.

`CandidateFinding` has every logical `Finding` field except `finding_id`. Pure policy functions
return candidates so they remain deterministic and cannot read ambient randomness. The application
normalizes candidates, allocates one OS-CSPRNG `fnd_` ID for each, persists that map in the durable
local-result object, and constructs immutable `Finding` values. A crash/retry reopens the map and
never allocates replacement IDs.

`Finding` is a frozen value object. It must never depend on mutable provider SDK output, logging
context, or database rows after construction.

The `origin` field distinguishes deterministic policy findings from semantic-model-derived
findings. `FindingKind` describes the issue and never implies origin: the deterministic
research-evidence pack and semantic reviewer may both produce an evidence-assessment kind. The same
surface type is used for both because the CLI, MCP, and receipt layers need one common
representation, but origin/provenance and policy fields record which path produced it.

`FindingKind` is the closed fourteen-value inventory registered in `specs/INTERFACES.md`, including
`action_without_result`. `FINDING_KIND_TRAITS` implements that registry table verbatim. A
candidate's explicit `priority` MUST equal its kind's registered priority; a mismatch is invalid
rather than a caller-selected reranking. The registered `actionable` boolean is derived from kind
and is never another serialized finding field. This keeps both deterministic packs and
post-validated semantic challenges on one ranking contract.

`WaiverScope.finding_only` is the sole v0.1 waiver scope. It binds exactly the `finding_id` and
`finding_frontier` carried by the response event; it cannot widen to a subject, obligation, task,
or future instance of the same rule. An optional expiry may narrow this scope but never widen it.

A deterministic finding's `summary` and `detail` are rule-templated prose that refer to their
subjects by ID. They never quote, paraphrase, or embed the content behind a `subject_ref`: not an
obligation's description, not a claim's statement, not evidence bytes, not a file excerpt. A
deterministic finding says the equivalent of "completion claim while obligation o7 remains open",
never the text of o7. The rule that fired and the IDs it fired on are the whole message; the reader
who wants the subject reads the subject.

The consequence is a disclosure property rather than a style preference. Because deterministic prose
carries no content from its refs, it discloses nothing about material the requesting writer did not
author, even when its `subject_refs` span several writers in the task — the refs are IDs the writer
already holds, and the prose adds no content behind them.

This constraint binds deterministic findings only. A post-validated semantic `ReviewerChallenge` is
provider-derived prose and keeps its existing treatment and its existing agent-context fence.

For a post-validated semantic `ReviewerChallenge`, `summary` states the discrepancy and `detail`
contains the bounded direct message to the main agent, alternative interpretation, uncertainty,
and smallest requested next step. This uses the existing finding schema so the message passes the
existing `finding_summary` agent-context privacy fence. It does not turn a finding into a chat
transcript or grant the model response/waiver authority.

The challenge's internal `cited_refs` are not copied blindly. Post-validation resolves a cited
action/result/evidence/frontier-finding to its recorded event/obligation/claim roots and resolves a
same-check deterministic finding ID to that candidate's already frozen `subject_refs`. The semantic
finding stores the sorted unique root union only. Resolution outside the frozen ref graph, an empty
root union, or a local same-check finding that was not durably pinned rejects the challenge. This
keeps public `subject_refs` valid even when the cited deterministic finding is later suppressed by
the result cap.

`priority` uses the shared three-level scheme:

- `1` = highest material priority, user action likely required;
- `2` = important but not first;
- `3` = lower-priority or explanatory material.

`coverage` is the finding’s weakest material coverage. It must be conservative: imported or stale
material weakens it, and semantic output can never strengthen it past the evidence it actually saw.

`subject_refs` is the stable tuple of event, obligation, or claim IDs that justify the finding.
It is bounded, ordered, and canonicalized. It is never allowed to contain raw free text.

`SemanticProvenance` captures the minimum finalized audit trail for a semantic finding: provider
profile, model and attempt identity, dispatch kind, external authorization or local-disclosure
reservation, durable privacy receipt, request commitment when external, exact semantic
status/reason, bounded usage, and failure class if any. The provenance record is part of the value,
not an external log lookup. A provider adapter's provisional `ProviderAttemptProvenance` is never
valid here; the coordinator may construct this value only after the matching privacy receipt is
durable.

### JSON codecs

`semantic_provenance_from_json(value)` requires a `JsonObject` with exactly the schema's keys,
constructs all nested records and nominal enums, converts canonical unsigned-decimal strings to
domain integers, and rejects unknown/missing/conditionally invalid fields with bounded
`ProtocolValueError` reasons. `semantic_provenance_to_json(value)` emits the inverse closed object,
omitting every `None` optional and rendering all wire-decimal fields canonically.

`finding_from_json(value)` and `finding_to_json(finding)` are the sole finding codecs. They use the
coverage codec owned by `protocol/coverage.py`, `Frontier.as_wire()` and the corresponding strict
frontier decoder, and the provenance codecs above. A semantic origin requires provenance; a
deterministic origin forbids it. They preserve sorted tuples and reject unknown keys. For every
valid schema value `x`, canonical encoding of `finding_to_json(finding_from_json(x))` equals the
canonical encoding of `x`; no Pydantic dump, adapter dict, or event-specific duplicate serializer
is permitted.

`RankedFindings` preserves:

- the ordered selection returned to the caller (not necessarily the ordinary top-N prefix because
  the registered semantic diversity rule may reserve one slot);
- the exact number of unselected findings after the cap/diversity rule (the displaced ordinary
  top-N item also counts as suppressed);
- the final verdict implied by the set; and
- the application-supplied weakest material `Coverage` across the full checked case, policy and
  semantic dependencies, explicit gaps, and all candidates before capping.

Those are exactly four stored facts: `findings: tuple[Finding, ...]`, `suppressed_count: int`,
`verdict: CheckVerdict`, and `coverage: Coverage`. None is recomputed lazily from mutable state.
Suppressing or diversity-replacing a finding never changes that coverage field.

`rank_key(finding)` returns the exact deterministic ordering tuple owned by
`kernel/ranking.md`: registered priority, registered actionability, evidence-strength bucket,
coverage bucket, origin preference, and finally finding ID bytes. It never reads prose.

## Errors and edge cases

The exact `ProtocolValueError` reasons first raised by this module are:
`invalid_finding_origin`, `invalid_finding_kind`, `finding_priority_mismatch`,
`invalid_finding_subject_refs`, `invalid_finding_policy_identity`,
`invalid_finding_provenance`, `invalid_ranked_findings`, `invalid_sampling_params`,
`invalid_token_usage`, `invalid_cost_fields`, `invalid_semantic_dispatch_kind`,
`invalid_semantic_failure_class`, `invalid_semantic_provenance`,
`finding_json_shape_invalid`, and `semantic_provenance_json_shape_invalid`. This closed inventory
must appear in `protocol.errors.PROTOCOL_REASON_CODES`. Imported ID, digest, commitment, frontier,
coverage, canonical-set, and semantic-pair validators propagate their owning reason unchanged.

- Unknown finding kinds are invalid at the boundary.
- A semantic finding without finalized provenance is invalid. An imported semantic observation
  preserves its original finalized provenance or remains an opaque/import-gap observation; it may
  not fabricate a current semantic finding.
- Findings never expose more than three items by default at the CLI surface, even if more are stored.
- `CheckVerdict` never has a value named `pass`.
- A finding cannot claim stronger coverage than its subject frontier or supporting refs justify.

## Invariants

1. Findings are sparse and ordered deterministically.
2. Coverage is always explicit.
3. Prose in `summary` and `detail` is no stronger than the underlying evidence.
4. The same finding ID never changes meaning across retry.
5. Semantic provenance is auditable but bounded.
6. Pure kernel functions create candidates; only the injected `IdPort` creates finding IDs.
7. No semantic finding can precede its durable privacy receipt.
8. Finding kind and origin remain independent, and semantic challenge prose stays bounded by the
   exact supplied case.
9. Deterministic finding prose is rule-templated and names its subjects by ID: no content from
   behind a `subject_ref` reaches `summary` or `detail`, so a deterministic finding discloses
   nothing about material the requesting writer did not author, whatever writers its refs span.

## Tests

- `tests/unit/domain/test_findings.py` — exhaustive fourteen-kind trait table, priority-mismatch
  rejection, `WaiverScope.finding_only`, four-field `RankedFindings`, and provenance validation;
  deterministic
  prose carries no content from behind its refs — fixtures whose obligation description, claim
  statement, and evidence bytes are distinctive marker strings produce `summary`/`detail` containing
  no marker, including when `subject_refs` span multiple writers.
- `tests/unit/kernel/test_ranking.py` — every rank-key component, deterministic-before-semantic
  full-fact ties, final ID-byte tie-break, and suppression-count behavior.
- `tests/subprocess/test_cli_invocations.py` — three-item cap, stable ordering, suppressed count, and
  no-stronger-than-evidence human wording.

## Open questions

None.

A later import preserves the original semantic provenance and adds imported publication/
artifact-observation coverage; it never relabels model-derived judgment as deterministic.
