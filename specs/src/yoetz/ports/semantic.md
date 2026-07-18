# src/yoetz/ports/semantic.py — SemanticEvaluatorPort protocol and semantic result types

**Wave:** B (definition) / E (live use) | **ADRs:** ADR-006, ADR-009, ADR-002 | **Imports
(spec-tree):** `protocol/errors.md`, `protocol/coverage.md`, `protocol/models.md`
(`SemanticStatus`, `SemanticReason`, `VALID_SEMANTIC_REASONS`, `DataCategory`), `domain/findings.md`
(`FindingKind`, `SamplingParams`, `TokenUsage`, `CostFields`, `SemanticFailureClass`, finalized
`SemanticProvenance`), type-checking-only names from `domain/privacy.md`,
`kernel/deterministic_checks.md` (`FindingBasis`) |
**Imported by:**
`application/egress.md`, `adapters/privacy/gateway.md`, `adapters/providers/openai_responses.md`,
`adapters/providers/fake.md`

## Purpose

The semantic layer is optional, model-derived, advisory, and deterministically fenced.
`SemanticEvaluatorPort` is the provider plug-in behind ADR-009's policy-enforcing outbound gateway.
It accepts only a bounded `ApprovedProviderCase`, never an unrestricted semantic/candidate context,
and hands back a typed untrusted result for deterministic post-validation. Application/check code
cannot receive or call this port directly.

The port never touches SQLite: `adapters/privacy/gateway.md` calls it with no transaction held, and
durable attempt/privacy-receipt persistence is the coordinator/audit port's job, not the evaluator's.

## Public surface

- `class SemanticEvaluatorPort(Protocol)`:
  - `async def evaluate(self, case: ApprovedProviderCase, deadline: Deadline) -> SemanticResult`
- `@dataclass(frozen=True, slots=True) class SemanticCase` — internal pre-egress candidate shape;
  it has no provider-facing serializer and is converted to `CandidateContext` by application code.
- `@dataclass(frozen=True, slots=True) class ReviewPacket` — structured goal, timeline, proof,
  excerpt, and omission content selected under one `ReviewContextProfile`.
- `@dataclass(frozen=True, slots=True) class ReviewAssessment` — one pinned deterministic finding
  and basis with an optional policy-selected pair of finding-prose item references.
- `@dataclass(frozen=True, slots=True) class ReviewAssessmentSkipped` — local structural record for
  one otherwise-valid assessment that cannot fit an outbound 16-ref field without loss.
- `project_review_assessment(assessment, finding_ref, summary_item_id=None,
  detail_item_id=None) -> ReviewAssessment | ReviewAssessmentSkipped` — the sole internal-basis to
  frozen outbound-review projection.
- `@dataclass(frozen=True, slots=True) class TargetedExcerptRef` and `ChangeObservation` — bounded
  recorded-excerpt binding and honest change/content-visibility facts.
- `@dataclass(frozen=True, slots=True) class Deadline` — exact fields
  `expires_at_utc: datetime` and `monotonic_deadline: float`; methods
  `remaining_seconds(now_monotonic: float, /) -> float` (clamped at `0.0`) and
  `expired(now_monotonic: float, /) -> bool`.
- `SemanticResult` — a closed union (`type SemanticResult = SemanticResultSuccess |
  SemanticResultRefused | SemanticResultTimeout | SemanticResultInvalid | SemanticResultLate |
  SemanticResultUnavailable`),
  each a frozen dataclass sharing a provisional `provenance: ProviderAttemptProvenance` field.
- `@dataclass(frozen=True, slots=True) class ProviderAttemptProvenance` — what one adapter can
  truthfully return before the coordinator has durably closed the privacy receipt.
- `SemanticProvenance` — finalized provenance imported from `domain/findings.py`; this port does
  not define a second class.
- `@dataclass(frozen=True, slots=True) class SemanticJudgment` — the parsed, schema-conforming
  raw model judgment carried by `SemanticResultSuccess` (closed conclusion plus zero to three
  `ReviewerChallenge` values), still untrusted until post-validation. Challenges are the sole
  candidate-producing shape.
- `@dataclass(frozen=True, slots=True) class ReviewerChallenge` — one case-bound discrepancy,
  alternative interpretation, direct main-agent message, smallest requested next step, and
  uncertainty statement.
- `SemanticStatus` — imported and re-exported from `protocol/models.py`: `not_requested`, `not_configured`, `blocked_by_policy`,
  `blocked_forbidden_data`, `classification_uncertain`, `awaiting_human`, `human_denied`,
  `approval_expired`, `succeeded`, `refused`, `timeout`, `invalid`, `unavailable`, `late`, `stale`,
  `failed`. `late` means lease/deadline authority was lost; `stale` means frozen dependencies no
  longer match. Privacy statuses are supplied by `application/egress.md`, not provider adapters.
- `SemanticReason` — imported and re-exported from `protocol/models.py`; the required machine-readable reason paired with every
  `SemanticStatus`: `deterministic_mode`, `no_material_semantic_case`, `provider_not_configured`,
  `local_model_not_configured`, `network_egress_denied`, `channel_disabled`,
  `provider_binding_not_authorized`, `scope_not_authorized`,
  `content_category_not_authorized`, `policy_generation_revoked`, `never_send_detected`,
  `secret_detected`, `classification_uncertain`, `human_approval_required`, `human_denied`,
  `human_approval_expired`, `semantic_completed`, `provider_refused`, `provider_timeout`,
  `response_schema_invalid`, `response_content_invalid`, `semantic_judgment_rejected`,
  `credential_unavailable`, `endpoint_profile_unavailable`, `transport_unavailable`,
  `provider_rate_limited`, `provider_quota_exhausted`, `retry_budget_exhausted`,
  `audit_reservation_unavailable`, `receipt_persistence_unknown`, `deadline_authority_lost`,
  `lease_authority_lost`, `frontier_changed`, `dependency_changed`, `coordinator_failure`.

## Behavior

This Wave B module has no runtime dependency on the later-wave privacy domain. It uses
`from __future__ import annotations`; `ApprovedProviderCase`, `ReviewContextProfile`, and
`ReviewSelectionPolicy` are imported only under `TYPE_CHECKING` and appear only in postponed
annotations. Runtime `DataCategory` comes from its B0 owner, `protocol/models.py`. Constructors do
not call `get_type_hints`, `isinstance`, or any privacy-domain constructor. The Wave D/E egress
coordinator supplies the already-validated nominal privacy values and performs the final conversion
to `ApprovedProviderCase`. Thus importing `yoetz.ports.semantic` in a Wave B-only installation does
not import or require `yoetz.domain.privacy`.

### `Deadline` — explicit process-local budget

`Deadline` is a frozen, slotted value and contains no clock, callback, lazy default, or mutable
state. `expires_at_utc` is a timezone-aware diagnostic instant whose UTC offset is exactly zero.
`monotonic_deadline` is a finite, nonnegative process-local monotonic reading. Construction rejects
a non-`datetime`, a naive or nonzero-offset datetime, or a monotonic value whose exact type is not
`float` (including `bool`), is NaN/infinite, or is negative as an internal caller defect. The value
is never serialized, persisted, or reused after a process restart.

Both methods require an explicit exact-`float`, finite, nonnegative `now_monotonic` sample; neither
method imports or calls `datetime.now`, `time.time`, `time.monotonic`, an event-loop clock, or
`ClockPort`.
`remaining_seconds(now_monotonic)` returns exactly
`max(0.0, monotonic_deadline - now_monotonic)`. `expired(now_monotonic)` returns exactly
`now_monotonic >= monotonic_deadline`; equality is expired. Invalid samples are internal caller
defects and raise before a provider call. `expires_at_utc` never participates in either result, so
wall-clock jumps cannot change an existing provider budget.

The coordinator constructs the pair from separately captured readings: it captures one
`clock.now_utc()` value for the diagnostic expiry and one `clock.monotonic_seconds()` value for the
process-local budget, then adds the same configured duration to each in its own time domain. It
does not derive a monotonic deadline by subtracting wall-clock datetimes. Every provider adapter is
constructed with an injected `ClockPort`; immediately before its one physical attempt it captures
`now_monotonic = clock.monotonic_seconds()` and passes that exact value to the deadline methods.

### `SemanticCase` — pre-egress candidate contract

The check coordinator builds this internal case from a `FrozenCase` plus the durably pinned local
deterministic result, then
`application/egress.md` converts, classifies, authorizes, minimizes, redacts, and scans it. Provider
adapters never receive this type:

- Always eligible for local selection: task goal/plan, open/relevant obligations and acceptance
  criteria, current claims, accepted decisions/revisions, a material ordered action/result/evidence
  timeline, deterministic findings plus their machine-readable `FindingBasis`, change observations,
  coverage gaps, and the omission manifest.
- Eligible only under the effective context/category/class policy: bounded evidence/test/failure,
  diff/command, and repository excerpts that are already captured or agent-published in the frozen
  case and mechanically linked to the reviewed subjects. Exact command strings are omitted by the
  upstream `assisted` rules unless material and separately allowed.
- Never: an ambient/raw repository, provider-driven fetch, live Git/filesystem lookup, secrets,
  unrelated conversation, unrestricted logs/stderr, complete transcripts, object plaintext beyond
  selected bounded excerpts, or refs outside the two exact allowlists.
- Every candidate excerpt is subject to the frozen 16 KiB item and 256 KiB case caps; approval does
  not override them.
- Fields: `case_id: str`, `subject_frontier: Frontier`, `dependency_digest: str`,
  `frontier_refs: frozenset[str]`, `local_check_refs: frozenset[str]`,
  `review_context_profile: ReviewContextProfile`, `review_selection: ReviewSelectionPolicy`,
  `policy_id: str`, `policy_version: str`,
  `packet: ReviewPacket`, `items: tuple[SemanticCaseItem, ...]`,
  `question_set: tuple[str, ...]`, and `case_digest: str` (canonical bytes digest — the dedup key in
  `semantic_jobs`). `frontier_refs` are exactly the IDs present at F; `local_check_refs` are the
  deterministic finding IDs already allocated/persisted for this same check. Their sorted union is
  the only valid semantic subject-ref set and is bound into `case_digest`.

`SemanticCaseItem` is exact: `item_id: str`, `section:
goal|obligation|claim|decision|timeline|deterministic_summary|deterministic_detail|excerpt`,
`category: DataCategory`, `source_kind:
task|obligation|claim|decision|action|result|evidence|finding|test|failure|diff|command|repository`,
`source_ref: str`, `linked_subject_refs: tuple[str, ...]`, `occurred_order: int`, `content: bytes`,
`content_bytes: int`, and `content_digest: str`. Refs are sorted unique and must belong to
`frontier_refs ∪ local_check_refs`; item IDs and source refs are opaque non-dereferenceable values.
Items sort by section order above, `occurred_order`, source-ref bytes, then item ID. `content` is at
most `MAX_SEMANTIC_ITEM_BYTES`; all item content plus structured packet bytes remain within
`MAX_SEMANTIC_CASE_BYTES`.

`ReviewPacket` is organized rather than flattened prose. Its exact fields are:

- `goal_item_ids: tuple[str, ...]` (0..4), `obligation_item_ids` (0..32), `claim_item_ids`
  (0..32), `decision_item_ids` (0..16), and `timeline_item_ids`
  (0..`MAX_REVIEW_TIMELINE_ITEMS`);
- `deterministic_assessments: tuple[ReviewAssessment, ...]`
  (0..`MAX_REVIEW_ASSESSMENTS`), ordered by work-integrity then research-evidence, each pack's
  frozen rule ordinal, then the complete candidate subject-ref tuple's unsigned ASCII bytes;
- `change_observations: tuple[ChangeObservation, ...]`
  (0..`MAX_REVIEW_CHANGE_OBSERVATIONS`), sorted by subject refs;
- `coverage: Coverage`;
- `targeted_excerpts: tuple[TargetedExcerptRef, ...]` (0..`MAX_REVIEW_EXCERPTS`), in the
  deterministic selection order; and
- `omissions: tuple[ReviewOmission, ...]` (0..`MAX_REVIEW_OMISSIONS`), sorted by subject/category/
  reason bytes.

Every item ID appears exactly once through one goal/obligation/claim/decision/timeline list, one
assessment summary/detail field, or one targeted excerpt. Every targeted excerpt references one
`section=excerpt` item; no orphan item is provider-renderable. `structural` includes only typed
facts/codes; `goal_aware` adds allowed intent/claim/finding prose; `assisted` adds problem-local
recorded excerpts; `expanded` and `custom` use their exact compiled `ReviewSelectionPolicy`.

`ReviewAssessment` contains exactly the outbound fields `finding_ref` from `local_check_refs`,
`finding_kind`, `priority`, sorted public-root `subject_refs` within `frontier_refs`, `rule_id`,
`observed_facts`, `required_but_missing_facts`, `subject_state_relation`, `source_availability`,
`coverage_gaps`, `supporting_refs`, and optional `summary_item_id` plus `detail_item_id`. The basis
fields are the lossless projection of one internal `FindingBasis`; the outbound record does not
embed the Python basis object. The two item IDs are both present or both absent.
`source_availability` is the internal four-token value
`available|not_recorded|unavailable_at_freeze|redacted_at_source`; the mapper preserves it exactly
and never collapses frozen object/key unavailability into absence, redaction, or a later privacy
decision.
When present, they resolve respectively to `section=deterministic_summary|deterministic_detail`,
`source_kind=finding`, category `finding_summary`, the same finding source/ref roots, and the exact
bounded candidate summary/detail. They may be present only when `include_finding_prose=true` and
the independent category/class policy admits them. Under `structural` they are
absent, leaving only the typed kind/priority/roots/basis; this is how the profile sends useful
deterministic facts without prose. The check coordinator derives this value one-to-one from the
pinned `DeterministicAssessment`; neither provider output nor privacy code may mutate the basis.

The phrase "one basis" above names the internal source, not an instruction to serialize its Python
dataclass unchanged. `project_review_assessment` is the sole mapper to the frozen
`outbound-case#/$defs/review_assessment` shape. It first requires
`basis.rule_id == f"{candidate.policy_id}/{candidate.kind.value}"`, a deterministic origin, a pinned
`finding_ref`, and canonical allowed refs. It then maps fields exactly:

This mapper never chooses or emits a public finding policy identity. The check coordinator derives
`Finding.policy_id` / `Finding.policy_version` later from the final accepted `FindingKind`'s unique
owning built-in pack, so a semantic reviewer cannot introduce a third `semantic-review` pack or
override the derived identity by prose.

| Outbound field | Source |
|---|---|
| `finding_ref` | the durable ID pinned to this exact assessment |
| `finding_kind` | bare `candidate.kind.value` |
| `priority` | `candidate.priority` |
| `subject_refs` | the complete `candidate.subject_refs` tuple |
| `rule_id` | bare `candidate.kind.value`, not the internal `policy-id/kind` spelling |
| `observed_facts` | every internal `(fact_code, subject_refs)` entry unchanged and in canonical order |
| `required_but_missing_facts` | every internal entry unchanged and in canonical order |
| `subject_state_relation` | unchanged |
| `source_availability` | unchanged |
| `coverage_gaps` | unchanged |
| `supporting_refs` | unchanged |
| `summary_item_id`, `detail_item_id` | the supplied pair, both present or both absent |

This mapping is lossless for an included assessment: fact/ref associations are preserved, and the
pinned local-result map recovers the policy identity behind the deliberately bare schema rule token.
It never uses the flattened status-result basis projection.

The internal basis permits up to 64 refs while each outbound `subject_refs`, fact `subject_refs`,
and `supporting_refs` field permits at most 16. The mapper MUST NOT take a prefix, split a fact,
drop a ref, or rewrite the basis. It checks, in exact order, the candidate `subject_refs`, each
`observed_facts` entry in canonical tuple order, each `required_but_missing_facts` entry in
canonical tuple order, then `supporting_refs`. If the first over-limit field is found, it returns
`ReviewAssessmentSkipped(finding_ref, limit_field, actual_count, omission)` where `limit_field` is
exactly `subject_refs|observed_fact_subject_refs|required_missing_fact_subject_refs|supporting_refs`,
`actual_count` is the unmodified tuple length, and `omission` is exactly
`ReviewOmission(subject_ref=finding_ref, category=DataCategory.bounded_structural_metadata,
source_kind="finding", reason="not_selected")`. No assessment content item is created for that
finding. The encrypted local result retains the complete assessment, the packet's coverage fold
still includes it, and the structural skip record remains coordinator audit data. Remaining
representable assessments proceed in their original deterministic order. If no provider-renderable
semantic material remains, the existing `no_material_semantic_case` path is used; an over-limit
assessment is never silently described as having been reviewed.

`ReviewAssessmentSkipped` is exactly the frozen local value `(finding_ref: FindingId,
limit_field: Literal["subject_refs", "observed_fact_subject_refs",
"required_missing_fact_subject_refs", "supporting_refs"], actual_count: int,
omission: ReviewOmission)`. `actual_count` is an `int` but not `bool` in 17..64. This value has no
outbound serializer; only its schema-valid `omission` enters the approved packet.

A namespace/kind mismatch, invalid ID kind, unsorted/duplicate tuple, empty required ref tuple, or
unknown fact code is a malformed internal assessment and rejects semantic-case construction. It is
not converted to `not_selected`, because the skip branch is reserved only for a valid internal
assessment that exceeds the narrower outbound bound.

`TargetedExcerptRef` fields are `excerpt_item_id`, `source_kind:
evidence|test|failure|diff|command|repository`, `linked_subject_refs` (1..16 allowed refs),
`subject_state_relation`, `content_visibility`, `content_digest`, and `content_bytes`. It contains
no path, locator, provider fetch token, or duplicate content.

`ReviewOmission` fields are `subject_ref`, `category`, `source_kind`, and `reason:
not_recorded|not_selected|withheld_by_policy|redacted_never_send`; it contains no omitted text,
digest of omitted plaintext, or dereferenceable source. Before egress the case builder can emit
`not_recorded|not_selected` and a `redacted_never_send` marker only when the frozen ledger already
contains that structural redaction fact and no forbidden bytes. The privacy gateway may add
`withheld_by_policy`. A newly discovered forbidden source/scan match blocks the whole request before
approved-case construction; it never becomes a provider-visible omission.

`ChangeObservation` exact fields are `subject_refs: tuple[str, ...]` (1..16),
`claimed_change: bool`, `subject_state_relation: same|different|unknown`, `content_visibility:
available|not_recorded|not_selected|withheld_by_policy|redacted_never_send`, optional
`before_state_digest`, and optional `after_state_digest`. Two present comparable equal tree digests
support `same`; two present comparable unequal tree digests support `different`; a single-sided,
unpaired, described-only, redacted, conflicting, or absent comparison remains `unknown`.
Visibility says whether the reviewer saw content and never changes the state relation. The prompt
must therefore say “change asserted, state not observed” or “change observed, content hidden”
instead of “no diff.”

### `SemanticJudgment` and `ReviewerChallenge`

The judgment is one-shot and closed: `conclusion:
no_material_discrepancy|challenges_returned|insufficient_packet` plus
`challenges: tuple[ReviewerChallenge, ...]` of 0..`MAX_REVIEW_CHALLENGES`. `challenges_returned`
requires at least one challenge; `no_material_discrepancy` requires none; `insufficient_packet`
requires none and relies on supplied omission/coverage refs. There is no parallel candidate-finding
array: each accepted challenge produces at most one semantic candidate.

A challenge contains exactly: `finding_kind` from the complete `FindingKind` registry; `summary`;
`cited_refs` drawn only from `frontier_refs ∪ local_check_refs`; `discrepancy`;
`alternative_interpretation`; `message_to_main_agent`; `requested_next_step` exactly
`act|provide_evidence|revise_claim|dispute_with_evidence|state_unresolved_limitation`; and
`uncertainty`. Each text is nonempty UTF-8 and at most `MAX_REVIEW_TEXT_BYTES`; `cited_refs` is
sorted unique with 1..16 members. Excerpt support is cited through its supplied linked subject refs;
free-text item IDs, source locators, and quotes absent from the packet are rejected.

Post-validation requires the challenge to identify a material discrepancy supported by case facts,
preserve deterministic origin/coverage, and remain useful under the packet's omissions. It resolves
each cited frontier action/result/evidence/finding to its canonical event/obligation/claim roots and
each cited `local_check_ref` to the pinned deterministic candidate's own public subject roots. The
sorted root union must be nonempty and within `frontier_refs`; those roots—not broad cited refs—become
the semantic `Finding.subject_refs`. Accepted, ranked challenge text becomes the existing semantic
finding summary/detail. The public response path is
unchanged: the main agent acknowledges/acts, publishes evidence, publishes a superseding claim,
rejects with evidence, or records an unresolved limitation through `respond` plus `publish_work`,
then runs a fresh `check`. The model cannot request arbitrary extra context, create an interactive
fetch round, decide a waiver, or converse directly with the provider after return.

### `evaluate`

1. The gateway has already consumed exact privacy authorization. The adapter renders only the
   approved bytes into its exact provider profile's request shape, captures
   `now_monotonic = clock.monotonic_seconds()` from its injected `ClockPort`, computes
   `remaining = deadline.remaining_seconds(now_monotonic)`, sets an explicit timeout from
   `remaining` minus its fixed safety margin (clamped at zero), and makes exactly one
   physical provider attempt per call. The retry budget (≤ 2 retries, only timeout/connection/429
   classes, jittered backoff, one total deadline — ADR-006 decision 5) is owned by the *coordinator*,
   which records one durable `semantic_attempts` row per physical call; the adapter never retries
   internally (`max_retries=0` on the SDK client).
2. Outcomes map to the closed union:
   - Parsed, schema-conforming structured output → `SemanticResultSuccess(judgment, provenance)`.
   - Provider refusal (explicit refusal surface of the profile) →
     `SemanticResultRefused(provenance)` — terminal for the case, never retried with the same
     case bytes.
   - Deadline expiry (adapter-observed timeout or cancellation at the deadline) →
     `SemanticResultTimeout(provenance)`.
   - Response bytes received but not parseable as the exact versioned judgment schema
     (malformed JSON, wrong schema, incomplete/truncated output) →
     `SemanticResultInvalid(provenance, raw_size: int)` — normal v0.1 operation does not retain the
     raw bytes; the adapter returns no raw text.
   - A response that arrives after the caller has already lost lease authority is detected by the
     *coordinator*, which reclassifies whatever variant the adapter returned as `late`
     (`SemanticResultLate` exists so a scripted fake can also produce late arrivals directly).
3. Expected transport/auth/profile failures return `SemanticResultUnavailable` with a bounded
   failure class. Provider exceptions never cross the gateway. Only cancellation and programming
   defects raise.
4. The adapter never writes to SQLite, never reads the ledger, never sleeps past the deadline,
   and never mutates the case.

### Semantic reason and provenance lifecycle (ADR-006 decision 7)

`SemanticReason` is not prose and is never inferred by a renderer. Every completed check stores
exactly one `(semantic_status, semantic_reason)` pair, including success and not-requested cases.
The enum objects, exhaustive mapping, and validator are owned by `protocol/models.py`; the table
below mirrors that authority so the port's outcome mapping remains reviewable:

| Status | Allowed reasons |
|---|---|
| `not_requested` | `deterministic_mode`, `no_material_semantic_case` |
| `not_configured` | `provider_not_configured`, `local_model_not_configured` |
| `blocked_by_policy` | `network_egress_denied`, `channel_disabled`, `provider_binding_not_authorized`, `scope_not_authorized`, `content_category_not_authorized`, `policy_generation_revoked` |
| `blocked_forbidden_data` | `never_send_detected`, `secret_detected` |
| `classification_uncertain` | `classification_uncertain` |
| `awaiting_human` | `human_approval_required` |
| `human_denied` | `human_denied` |
| `approval_expired` | `human_approval_expired` |
| `succeeded` | `semantic_completed` |
| `refused` | `provider_refused` |
| `timeout` | `provider_timeout` |
| `invalid` | `response_schema_invalid`, `response_content_invalid`, `semantic_judgment_rejected` |
| `unavailable` | `credential_unavailable`, `endpoint_profile_unavailable`, `transport_unavailable`, `provider_rate_limited`, `provider_quota_exhausted`, `retry_budget_exhausted`, `audit_reservation_unavailable`, `receipt_persistence_unknown` |
| `late` | `deadline_authority_lost`, `lease_authority_lost` |
| `stale` | `frontier_changed`, `dependency_changed` |
| `failed` | `coordinator_failure` |

This module must import `VALID_SEMANTIC_REASONS` and call the shared validator; it must not rebuild
the table as a second runtime mapping. It owns only the conversion from evaluator/privacy/
coordinator outcomes to a shared pair.

`ProviderAttemptProvenance` has only facts available to the adapter at return time:
`provider`, `endpoint_profile_id`, `endpoint_profile_version`, `model`, optional bounded
`provider_request_id`, `sdk_version`, `prompt_digest`, `schema_digest`, `policy_digest`,
`privacy_policy_digest`, fixed-string `sampling_params`, `latency_ms`, optional bounded
`token_usage`, optional bounded `cost_fields`, optional closed `failure_class`, and the adapter
outcome `status`. It has no semantic-attempt, authorization, reservation, or receipt identifier
and is not serializable as final finding provenance.

Its exact frozen dataclass shape is:

```text
ProviderAttemptProvenance(provider: str,
                          endpoint_profile_id: str,
                          endpoint_profile_version: str,
                          model: str,
                          sdk_version: str,
                          prompt_digest: str,
                          schema_digest: str,
                          policy_digest: str,
                          privacy_policy_digest: str,
                          sampling_params: SamplingParams,
                          latency_ms: int,
                          status: SemanticStatus,
                          provider_request_id: str | None = None,
                          token_usage: TokenUsage | None = None,
                          cost_fields: CostFields | None = None,
                          failure_class: SemanticFailureClass | None = None)
```

Its `status` is limited to evaluator-returnable `succeeded|refused|timeout|invalid|unavailable|
late`. It has no `reason`; the coordinator chooses the shared reason after classifying the exact
outcome. It reuses the finalized provenance component types so decimal-string and bound validation
cannot drift, but it is intentionally not accepted by the finalized provenance codec.

After the gateway returns and the matching terminal `EgressReceipt` or
`LocalDisclosureReceipt` is durable, the coordinator constructs the
`domain.findings.SemanticProvenance` by adding
`semantic_attempt_id`, `dispatch_kind: external|local_model`, `egress_authorization_id: str |
None`, `local_disclosure_reservation_id: str | None`, `privacy_receipt_id`,
`request_commitment: str | None`, and the final `status` and `reason`. External dispatch requires
only `egress_authorization_id`; local-model dispatch requires only
`local_disclosure_reservation_id`; exactly one is present. `request_commitment` is required for an
external attempt and absent for a local disclosure. Predispatch outcomes have no
`SemanticProvenance`; the completed check remains fully explained by its exact status/reason.
Model output is always labeled `semantic_model_derived`; provenance never claims deterministic
status.

The application coordinator, not this port or a provider adapter, owns the two exact provenance
stage failures. Passing a `ProviderAttemptProvenance` into a finding/event/result finalization path
raises `ProtocolValueError("provider_attempt_provenance_is_not_final")`. Attempting to construct
final `SemanticProvenance` before the matching terminal `EgressReceipt` or
`LocalDisclosureReceipt` is durably readable and identity-matched raises
`ProtocolValueError("privacy_receipt_not_durable")`. These are coordinator-boundary defects, never
provider outcomes or public provider exception text; `application/check.md` owns their transaction
and recovery behavior.

## Errors and edge cases

- No approved adapter/capability is a returned `unavailable` semantic status. Under
  `semantic_required` the check still returns its deterministic result with `incomplete_check`;
  unprofiled endpoints and credential failures never become public exception text.
- Refusal, timeout, invalid, unavailable, privacy block/denial/expiry, late, and stale output are
  returned/recorded, not raised. Under `semantic_required` they complete with deterministic
  findings, no semantic findings, `incomplete_check`, and an exact valid `SemanticReason`.
- `deadline.expired(now_monotonic)` (equivalently
  `deadline.remaining_seconds(now_monotonic) == 0.0`) on entry → return
  `SemanticResultTimeout` immediately
  without any network call.
- Cancellation (`anyio` cancelled exception) is re-raised, never converted; the coordinator's
  durable attempt row resolves the ambiguity.
- The adapter must not access or leak provider exception text, credential bytes, raw endpoint URLs,
  authorization bearer material, or case content into errors or logs.

## Invariants

1. Zero external egress in `local_only`: no external implementation is instantiated. A separately
   configured exact AF_UNIX local-model adapter is a local disclosure sink and still uses the
   privacy fence.
2. One `evaluate` call = at most one physical provider request; durable attempt identity lives in
   the coordinator's `semantic_attempts` rows, one per call.
3. The approved case is complete and closed: nothing is fetched or enriched during `evaluate`;
   targeted source means a bounded recorded excerpt, never a repository handle.
4. A `SemanticResult` is advisory input to deterministic post-validation; no variant can directly
   create a finding, complete an operation, or strengthen coverage.
5. Adapter-returned provisional provenance cannot be published. Final provenance exists only
   after its privacy receipt is durable; predispatch gaps are represented by status/reason alone.
6. The scripted fake (`adapters/providers/fake.md`) implements this exact protocol behind the
   privacy gateway — results,
   delays, refusals, malformed output, late responses — and the coordinator cannot distinguish it
   from the live adapter.
7. Missing/hidden content and unchanged state are distinct typed facts; neither the case builder nor
   model output may collapse one into the other.

## Tests

- `specs/tests/conformance.md`: adversarial fixtures — invented IDs, out-of-case quotes,
  deterministic-status claims, coverage upgrades, stale frontier, duplicate response, refusal,
  timeout, invalid JSON, valid-but-wrong-schema, late result — all fenced by post-validation and
  none able to block deterministic operation.
- Semantic packet fixtures prove deterministic bases, the split frontier/local-check allowlists,
  profile-specific selection, problem-local excerpt inclusion/unrelated exclusion, the omission
  manifest, honest unknown change visibility, and every reviewer-next-step value.
- `specs/tests/conformance.md`: fake-provider scripts drive every `SemanticStatus`; provenance
  fields recorded per attempt.
- Zero-egress subprocess tests permit exact profiled AF_UNIX service/confidential/local-model IPC
  plus release-tested OS credential, user-presence, and session-lifecycle local IPC; they deny
  arbitrary AF_UNIX or bus use, DNS, AF_INET/AF_INET6, proxies, redirects, external provider
  construction, and all five channels.

## Open questions

None.
