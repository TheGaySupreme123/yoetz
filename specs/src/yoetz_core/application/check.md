# src/yoetz_core/application/check.py — durable deterministic and optional semantic check coordinator

**Wave:** D–E | **ADRs:** ADR-001, ADR-002, ADR-003, ADR-004, ADR-006, ADR-008, ADR-009 | **Imports
(spec-tree):** `protocol/models.md`, `protocol/canonical.md`, `protocol/coverage.md`,
`protocol/errors.md`, `domain/events.md`, `domain/findings.md`, `domain/values.md`,
`kernel/deterministic_checks.md`, `kernel/ranking.md`, `ports/ledger.md`, `ports/objects.md`,
`ports/semantic.md`, `ports/privacy.md`, `ports/clock.md`, `ports/ids.md`,
`application/egress.md`, `application/unit_of_work.md` |
**Imported by:** `application/service.md`

## Purpose

`check` is the one coordinator for deterministic policies and optional semantic evaluation. It
freezes a subject frontier and its material dependencies, persists resumable work before doing
expensive computation or network I/O, fences every provider attempt by operation/job leases, then
deterministically validates and commits the bounded result. The model is advisory evidence, never
a second truth owner, and strict-local remains a complete deterministic path.

## Public surface

- `async execute_check(app: Application, request: CheckRequest) -> CheckResult` — implementation
  behind `Application.check`.
- Application-internal immutable values for the coordinator's deterministic result, semantic job
  plan, post-validation result, and resumed phase. They are not protocol or transport schemas.

The durable orchestration methods named under “Port contract” below are frozen in the shared
`LedgerPort` contract. This module must not downcast `LedgerPort` to a SQLite adapter or import
repository types.

## Behavior

### Validate and identify the logical operation

1. Require an active session/writer pair in one task runtime. Validate `expected_frontier`, actor,
   client, mode, sorted-unique claim/obligation scope, and `max_findings` before opening or
   resuming an operation. Query `TaskRuntime.importer.status(session_id)` as an optional bounded
   UX preflight; any observed pending import returns retryable `OPERATION_PENDING`. This snapshot
   is not authority: `LedgerPort.freeze_case` and the final commit repeat the pending predicate
   atomically.
2. `mode` is exactly `deterministic_only`, `semantic_if_configured`, or `semantic_required`.
   `max_findings` defaults to `MAX_FINDINGS_DEFAULT` (3) and is in
   `1..MAX_FINDINGS_LIMIT` (10). An empty scope means all policy-relevant material at the frozen
   frontier; it never means “nothing checked.” Named scope IDs must exist at that frontier.
3. Derive the actor's assurance from the integration channel and build the canonical logical
   request digest from protocol/schema/request/session/writer IDs, expected frontier, constrained
   actor/client identity, mode, normalized scope, maximum, and active versioned policy/config
   selection. Exclude generated finding/job/attempt/object IDs, encryption randomness, lease
   values, provider request IDs, and later ledger-assigned fields.
4. `semantic_required` means semantic evidence is required for a complete verdict, not that the
   operation may fail to return. Missing approved capability, privacy block, human denial/expiry,
   refusal, timeout, invalid/exhausted/late/stale outcome must preserve the deterministic result and
   complete with `incomplete_check`, no semantic findings, and an exact closed
   `(SemanticStatus, SemanticReason)` pair.

### Durable ten-step coordinator

The following is the public durable check state machine. Phase labels are durable lower bounds; every
resume revalidates the object/row facts for the recorded phase before moving forward.

1. **Freeze and reserve.** Finalize an encrypted bounded resume-case object, then in one short
   atomic write perform the idempotency lookup, catch projections up, verify
   that no import for this session is pending, verify `expected_frontier`, capture subject frontier
   `F`, dependency digest `D`, allowed IDs, active
   policy/config/engine/projection versions, and insert `operations` as
   `pending/reserved` with the current bundle-owner generation and operation lease. Commit before
   any policy execution. An already complete identical operation returns its stored original
   `CheckResult`; a different digest is `IDEMPOTENCY_CONFLICT`.
2. **Run local checks.** Outside a write transaction, run every applicable deterministic policy
   against the immutable case at `F`. Record exact run/skipped/failed policy IDs and reasons,
   normalize the returned `CandidateFinding` values, allocate stable finding IDs through
   `IdPort`, persist the candidate-to-ID map, and finalize the immutable deterministic-result object. CAS
   `reserved → local_ready` only after that object is durable. A resumed operation reopens and
   verifies the object rather than rerunning merely because the process restarted.
3. **Plan semantic/privacy work.** If mode is `deterministic_only` or no semantic case is material,
   record `not_requested` and advance. Otherwise build the bounded internal `SemanticCase` and
   `CandidateContext` from `F`, `D`, allowed IDs, and deterministic results. Persist them encrypted,
   insert the deduplicated semantic job plus privacy proposal identity, and advance to
   `semantic_wait`. Capability/policy absence does not skip this durable accounting under
   `semantic_required`; it becomes a terminal incomplete semantic reason.
4. **Prepare privacy case, resolve authority, and claim one attempt.** Invoke `PrivacyCoordinator`
   to classify, intersect policy, minimize/redact/scan locally, reserve the exact prepared case,
   and create/resume an approval proposal. `awaiting_human` is
   durable and returns `OPERATION_PENDING` without exposing preview content to MCP. Denial, expiry,
   policy/forbidden-data block, uncertainty, or absent approved capability terminally closes
   semantic work with the exact `SemanticReason` selected from the status/reason matrix in
   `ports/semantic.md`. Only an approved prepared case may claim an attempt while the parent operation is
   `pending/semantic_wait` and
   its owner generation, owner nonce, lease generation, and expiry are live. A live job lease is
   `OPERATION_PENDING`; queued, expired, or stale-generation work is fenced and reclaimed. The
   claim writes a new immutable attempt row with its ID/ordinal/provider request ID and captured
   owner/lease generations. `started` becomes `expired` on reclaim; `response_durable` becomes
   `late` while retaining its object; terminal attempts stay terminal. A job lease never outlives
   the parent operation lease.
5. **Authorize and call outside SQLite.** `PrivacyCoordinator.evaluate_semantic` revalidates the
   exact approved prepared bytes, mints then atomically consumes one exact authorization, and
   invokes the outbound gateway with a hard deadline. One authorization means at most one physical provider
   request. A new retry requires a new authorization and privacy receipt. The provider adapter
   returns only `ProviderAttemptProvenance`; after the matching terminal receipt is durable, the
   coordinator constructs final `SemanticProvenance`. Normal v0.1 operation retains no raw
   refused/invalid provider plaintext. A semantic result is not selectable until its structural
   commitment-bearing egress receipt is durable. No network call, parsing, encryption,
   fsync, prompt, or approval wait occurs inside a database transaction.
6. **Post-validate and select.** Deterministically reject or downgrade wrong schema/version,
   invented/out-of-case IDs or quotes, deterministic-status claims, coverage/authorship upgrades,
   disallowed finding kinds/count/severity/conclusions, missing uncertainty/provenance,
   duplicates without new basis, stale `F`/`D`, or output after cancellation/deadline/supersession.
   Select an attempt only in a short CAS that still matches the operation and job generations,
   active attempt, `F`, and `D`. At most one attempt is selected; late/non-selected objects remain
   audit data and cannot steer.
7. **Renew in authority order.** When renewal is needed, renew the parent operation first and its
   live job lease(s) in the same transaction. A new bundle-owner generation immediately invalidates
   both regardless of clock time. Losing authority aborts local steering/finalization; the
   successor reclaims and resumes.
8. **Close semantic work.** The coordinator owns at most two retries after the initial attempt,
   only for the approved timeout/connection/429 classes, with jittered bounded backoff inside one
   total monotonic deadline. Refusal is terminal and is never retried with identical case bytes.
   Invalid output, refusal, timeout, unavailable/exhausted attempts, privacy block, forbidden data,
   classification uncertainty, human denial/expiry, audit failure, late, or stale outcome terminates
   the job with an exact valid semantic status/reason pair and weakened coverage; it does not quarantine the
   public check. Under `semantic_required` it forces `incomplete_check`. Once
   all required jobs are terminal and none has a live lease, CAS
   `semantic_wait → ready_to_finalize`.
9. **Rank and commit.** Revalidate candidate output against `F` and `D`, combine deterministic and
   selected semantic findings, and call `rank_findings` with the request maximum. In the final
   bounded atomic write, reverify the operation lease/current owner and material dependency
   revisions, repeat the no-pending-import predicate for this session, append one
   `check_recorded` plus one `finding_recorded` per returned finding, persist
   exact coverage/status/reason/versions, finalized optional attempt provenance, and suppressed count, store the canonical structural result,
   set `complete/terminal`, clear leases, and commit. Only then acknowledge. A stale semantic
   result is labeled stale and cannot steer; any completed result states that limitation.
10. **Replay or resume.** Same request ID and digest returns the stored original result without
    rerunning. Current generation plus live operation/job lease returns retryable
    `OPERATION_PENDING`. Expiry or stale owner generation permits fenced CAS reclaim and resume
    from verified durable state. Only corruption, an impossible transition, or contradictory
    durable identity is quarantinable.

### Verdict and coverage

- Verdict is exactly `action_required`, `no_issue_detected`, `insufficient_coverage`, or
  `incomplete_check`; the token `pass` is forbidden.
- `action_required` means at least one returned unresolved material finding requires action,
  except that a terminal missing required semantic result takes precedence as `incomplete_check`.
  `no_issue_detected` means no unresolved deterministic completion-integrity issue was found at
  the recorded coverage, never that all work is correct. Missing/redacted/unknown material,
  semantic unavailability under optional mode, or stale dependencies may select
  `insufficient_coverage`. A required deterministic policy failure, or any terminal
  semantic/privacy failure under `semantic_required`, selects `incomplete_check`.
- Coverage is the component-wise weakest material dependency plus sorted explicit gaps. Semantic
  findings remain `semantic_model_derived`; deterministic post-validation never upgrades their
  origin.
- Result includes policies run/skipped/failed, semantic status/reason, optional finalized attempt
  provenance, subject and result
  frontiers, returned findings, suppressed count, coverage, and relevant versions. It never hides
  lower-priority suppression or a failed/skipped check.
- The coordinator and `CheckRecordedPayload` use the same `SemanticStatus` spelling. A solely
  late/non-selected result is `late`; a result rejected because `F` or `D` changed is `stale`.
  A late earlier attempt followed by a selected valid retry leaves the overall event status
  `succeeded` while attempt provenance retains the earlier `late` outcome.
- `semantic_reason` is always present and must be valid for `semantic_status`. Predispatch outcomes
  have `semantic_provenance=None`; an attempted provider/local-model outcome may expose only final
  provenance whose receipt is already durable. No renderer or MCP client derives a reason from
  prose, generic coverage gaps, or provider exception text.

### Port contract

`INTERFACES.md` and both memory/SQLite adapters share these authority-bearing `LedgerPort`
behaviors:

- `advance_check_phase`;
- `enqueue_semantic_job`;
- `claim_semantic_job`;
- `record_attempt_outcome`;
- `select_attempt`;
- `renew_leases`;
- `reclaim_operation`.

`freeze_case(session_id, writer_id, expected_frontier, request_id, request_digest)` receives both
authority-bearing values explicitly. `advance_check_phase` receives the optional durable-object
reference required by a recoverable phase. The application must not use `hasattr`, adapter casts,
or a concrete repository escape hatch.

## Errors and edge cases

- Invalid mode/scope/max or a scope ID absent at `F` → `INVALID_REQUEST`; stale expected frontier
  → `FRONTIER_CONFLICT`; same ID/different digest → `IDEMPOTENCY_CONFLICT`.
- No approved provider/local model, policy block, human denial/expiry, refusal, timeout, invalid
  output, retry exhaustion, audit failure, late, or stale outcome under `semantic_required` is
  recorded in the exact semantic status/reason pair and completes with deterministic results and
  `incomplete_check`; it is not `PROVIDER_UNAVAILABLE`. Raw provider/preview/validation text is
  never public.
- Cancellation before reservation has no durable effect. After reservation, cancellation is
  re-raised without claiming failure; a live attempt may later become durable/late. During the
  shielded final commit, the durable operation row decides. Same-ID retry returns complete,
  pending, or reclaims expired/stale work.
- If `F` or `D` changes before selection/finalization, stale semantic output cannot steer. The
  operation either completes with deterministic/stale coverage under the recorded policy or
  returns the contract's frontier conflict; it never publishes the stale candidate as current.
- Object/key/storage failures before final commit leave resumable state and/or orphan encrypted
  objects. Only verified corruption or contradictory state quarantines.

## Invariants

1. The deterministic kernel is the only component that creates final findings/verdicts from
   candidate evidence; no provider result writes directly to the ledger.
2. Every selected semantic result is tied to the exact `F`, `D`, case digest, and live generation
   that authorized it.
3. One consumed privacy authorization creates at most one physical provider request, one durable
   attempt, and one egress receipt.
4. `local_only` performs no external LLM activity and still completes deterministic checks. A
   zero-network claim additionally binds `network_egress_permitted=false` and all five channel rows
   disabled; exact approved local IPC is local disclosure, not network egress.
5. Acknowledgement follows the complete/terminal commit; timeout or cancellation never proves
   failure.
6. The memory and SQLite adapters expose and implement one identical check orchestration contract.

## Tests

- `specs/tests/unit.md`: mode/scope/max boundaries, logical digest exclusions, deterministic
  policy accounting, verdict/coverage matrix, adversarial semantic post-validation and ranking.
- `specs/tests/conformance.md`: all phases, replay/conflict/pending/reclaim, exact canonical
  result/event parity between memory and SQLite, one-selected-attempt invariant.
- `specs/tests/integration.md`: fake/live-profile absence, refusal/timeout/invalid/429/connection
  retry budget, dependency staleness, lease renewal and owner-generation loss.
- `specs/tests/subprocess.md`: kill/cancel at every durable phase and provider/commit boundary;
  strict-local network-denied six-operation flow.
- `specs/tests/property.md`: state-machine generation of phase/lease/attempt transitions; no stale
  generation can select or finalize and no terminal transition reverses.

## Open questions

None.
