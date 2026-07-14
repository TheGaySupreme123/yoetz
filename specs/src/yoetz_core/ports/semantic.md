# src/yoetz_core/ports/semantic.py — SemanticEvaluatorPort protocol and semantic result types

**Wave:** B (definition) / E (live use) | **ADRs:** ADR-006, ADR-009, ADR-002 | **Imports
(spec-tree):** `protocol/errors.md`, `protocol/coverage.md`, `domain/findings.md`
(SemanticProvenance), `domain/privacy.md` | **Imported by:**
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
- `@dataclass(frozen=True, slots=True) class Deadline` — `expires_at: datetime` (UTC), method
  `remaining_seconds() -> float` (clamped at 0.0).
- `SemanticResult` — a closed union (`type SemanticResult = SemanticResultSuccess |
  SemanticResultRefused | SemanticResultTimeout | SemanticResultInvalid | SemanticResultLate |
  SemanticResultUnavailable`),
  each a frozen dataclass sharing a provisional `provenance: ProviderAttemptProvenance` field.
- `@dataclass(frozen=True, slots=True) class ProviderAttemptProvenance` — what one adapter can
  truthfully return before the coordinator has durably closed the privacy receipt.
- `@dataclass(frozen=True, slots=True) class SemanticProvenance` — finalized attempt provenance,
  constructed only after the matching privacy receipt is durable.
- `@dataclass(frozen=True, slots=True) class SemanticJudgment` — the parsed, schema-conforming
  raw model judgment carried by `SemanticResultSuccess` (candidate findings, per-finding
  uncertainty text, conclusion vocabulary), still untrusted until post-validation.
- `enum SemanticStatus` — `not_requested`, `not_configured`, `blocked_by_policy`,
  `blocked_forbidden_data`, `classification_uncertain`, `awaiting_human`, `human_denied`,
  `approval_expired`, `succeeded`, `refused`, `timeout`, `invalid`, `unavailable`, `late`, `stale`,
  `failed`. `late` means lease/deadline authority was lost; `stale` means frozen dependencies no
  longer match. Privacy statuses are supplied by `application/egress.md`, not provider adapters.
- `enum SemanticReason` — the required machine-readable reason paired with every
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

### `SemanticCase` — pre-egress candidate contract

The check coordinator builds this internal case from a `FrozenCase`, then
`application/egress.md` converts, classifies, authorizes, minimizes, redacts, and scans it. Provider
adapters never receive this type:

- Only: the claims under review, open/relevant obligations, accepted decisions, evidence
  excerpts or digests, diff/command *metadata*, and the deterministic findings already computed —
  each item carried with its canonical ID from `frozen.allowed_ids`.
- Never: raw repositories, secrets, unrelated conversation, full transcripts, object plaintext
  beyond the bounded excerpts, or anything outside `allowed_ids` (ADR-006 and this port's closed
  input contract).
- Every candidate excerpt is subject to the frozen 16 KiB item and 256 KiB case caps; approval does
  not override them.
- Fields: `case_id: str`, `subject_frontier: Frontier`, `dependency_digest: str`,
  `allowed_ids: frozenset[str]`, `policy_id: str`, `policy_version: str`,
  `items: tuple[SemanticCaseItem, ...]`, `question_set: tuple[str, ...]` (the bounded policy
  questions, e.g. does-evidence-support-claim), `case_digest: str` (canonical bytes digest — the
  dedup key in `semantic_jobs`).

### `evaluate`

1. The gateway has already consumed exact privacy authorization. The adapter renders only the
   approved bytes into its exact provider profile's request shape, sets an explicit
   timeout of `deadline.remaining_seconds()` minus its fixed safety margin, and makes exactly one
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
The valid pairs are closed:

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

`ProviderAttemptProvenance` has only facts available to the adapter at return time:
`provider`, `endpoint_profile_id`, `endpoint_profile_version`, `model`, optional bounded
`provider_request_id`, `sdk_version`, `prompt_digest`, `schema_digest`, `policy_digest`,
`privacy_policy_digest`, fixed-string `sampling_params`, `latency_ms`, optional bounded
`token_usage`, optional bounded `cost_fields`, optional closed `failure_class`, and the adapter
outcome `status`. It has no semantic-attempt, authorization, reservation, or receipt identifier
and is not serializable as final finding provenance.

After the gateway returns and the matching terminal `EgressReceipt` or
`LocalDisclosureReceipt` is durable, the coordinator constructs `SemanticProvenance` by adding
`semantic_attempt_id`, `dispatch_kind: external|local_model`, `egress_authorization_id: str |
None`, `local_disclosure_reservation_id: str | None`, `privacy_receipt_id`,
`request_commitment: str | None`, and the final `status` and `reason`. External dispatch requires
only `egress_authorization_id`; local-model dispatch requires only
`local_disclosure_reservation_id`; exactly one is present. `request_commitment` is required for an
external attempt and absent for a local disclosure. Predispatch outcomes have no
`SemanticProvenance`; the completed check remains fully explained by its exact status/reason.
Model output is always labeled `semantic_model_derived`; provenance never claims deterministic
status.

## Errors and edge cases

- No approved adapter/capability is a returned `unavailable` semantic status. Under
  `semantic_required` the check still returns its deterministic result with `incomplete_check`;
  unprofiled endpoints and credential failures never become public exception text.
- Refusal, timeout, invalid, unavailable, privacy block/denial/expiry, late, and stale output are
  returned/recorded, not raised. Under `semantic_required` they complete with deterministic
  findings, no semantic findings, `incomplete_check`, and an exact valid `SemanticReason`.
- `deadline.remaining_seconds() == 0` on entry → return `SemanticResultTimeout` immediately
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
3. The approved case is complete and closed: nothing is fetched or enriched during `evaluate`.
4. A `SemanticResult` is advisory input to deterministic post-validation; no variant can directly
   create a finding, complete an operation, or strengthen coverage.
5. Adapter-returned provisional provenance cannot be published. Final provenance exists only
   after its privacy receipt is durable; predispatch gaps are represented by status/reason alone.
6. The scripted fake (`adapters/providers/fake.md`) implements this exact protocol behind the
   privacy gateway — results,
   delays, refusals, malformed output, late responses — and the coordinator cannot distinguish it
   from the live adapter.

## Tests

- `specs/tests/conformance.md`: adversarial fixtures — invented IDs, out-of-case quotes,
  deterministic-status claims, coverage upgrades, stale frontier, duplicate response, refusal,
  timeout, invalid JSON, valid-but-wrong-schema, late result — all fenced by post-validation and
  none able to block deterministic operation.
- `specs/tests/conformance.md`: fake-provider scripts drive every `SemanticStatus`; provenance
  fields recorded per attempt.
- Zero-egress subprocess tests permit exact profiled AF_UNIX service/confidential/local-model IPC
  plus release-tested OS credential, user-presence, and session-lifecycle local IPC; they deny
  arbitrary AF_UNIX or bus use, DNS, AF_INET/AF_INET6, proxies, redirects, external provider
  construction, and all five channels.

## Open questions

None.
