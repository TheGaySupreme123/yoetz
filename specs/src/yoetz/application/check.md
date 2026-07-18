# src/yoetz/application/check.py — durable deterministic and optional semantic check coordinator

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
   client, mode, unique bounded claim/obligation scope, and `max_findings` before opening or
   resuming an operation. Query `TaskRuntime.importer.status(session_id)` as an optional bounded
   UX preflight; any observed pending import returns retryable `OPERATION_PENDING`. This snapshot
   is not authority: `LedgerPort.freeze_case` and the final commit repeat the pending predicate
   atomically.
2. `mode` is exactly `deterministic_only`, `semantic_if_configured`, or `semantic_required`.
   `max_findings` defaults to `MAX_FINDINGS_DEFAULT` (3) and is in
   `1..MAX_FINDINGS_LIMIT` (10). Normalize an omitted scope to the required internal/event value
   `CheckScopeModel(claim_ids=(), obligation_ids=())`; otherwise copy each validated tuple into
   unsigned-ASCII ID order. The two tuples remain separate and duplicate-free. Both empty means all
   policy-relevant material at the frozen frontier; it never means “nothing checked.” Every named
   scope ID must exist at that frontier.
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

### Direct scope and policy accounting

For a nonempty normalized scope, the explicitly named claim and obligation IDs are the only direct
selection roots. A deterministic or semantic candidate is in scope only when its complete
`subject_refs` directly intersects one of those typed IDs. The frozen case may include referenced
actions, results, evidence, events, plans, and other companions required to evaluate a selected
root, but traversing such a dependency never promotes it or its neighbors into an additional
selection root. A whole-case scope has no such intersection filter. This exact direct rule is also
the later status applicability rule; prose similarity and transitive graph reachability never
stand in for a recorded scope match.

The selected built-in pack set is always nonempty. Record one exact `CheckPolicyExecution` for
each selected `PolicyVersion`, in canonical pack-ID order (`research-evidence` then
`work-integrity` when both are present), even though deterministic finding emission continues to
use its separately frozen work-integrity-then-research-evidence order. A pack with no rule root in
the direct nonempty scope records `skipped/scope_excluded`; a pack that evaluated its applicable
roots and found no issue still records `run/completed`. Other skipped and failed reasons use only
the closed check-result matrix. Before finalization, require `policies` and `policy_executions` to
have the same length, identity, version, and canonical order.

### Durable ten-step coordinator

The following is the public durable check state machine. Phase labels are durable lower bounds; every
resume revalidates the object/row facts for the recorded phase before moving forward.

1. **Freeze and reserve.** Call the port's bounded prepare/build/publish/final-reservation
   protocol. Its prepare snapshot repeats idempotency and the no-pending-import gate, verifies
   `expected_frontier`, and captures subject frontier `F`, active projection identity, and the exact
   policy/config/engine/projection revisions. With no write transaction held, it derives dependency
   digest `D`, pages the authoritative accepted-record prefix through `F`, and builds the exact
   `DeterministicCase` (projection, allowed IDs, per-ref coverage, typed gaps), and only then
   canonicalizes, encrypts, and durably finalizes the bounded resume-case object. A final short
   atomic write repeats idempotency/import checks and atomically revalidates current head `F`,
   projection identity, `D`, `expected_frontier`, owner generation, and the already-durable object
   metadata before inserting `operations` as `pending/reserved` with its object pointer and lease.
   No replay, case construction, hashing, encryption, filesystem I/O, or object opening occurs in
   that write transaction. Commit before policy execution. An already complete identical
   operation returns its stored original `CheckResult`; a different digest is
   `IDEMPOTENCY_CONFLICT`; a failed final revalidation leaves no operation row and only an orphan-GC
   candidate.
2. **Run local checks.** Outside a write transaction, run every applicable deterministic policy
   against `FrozenCase.case`, the immutable pure `DeterministicCase` at `F`, applying the direct
   scope rule above before any candidate can enter ranking. Record exact
   run/skipped/failed policy IDs and reasons,
   concatenate assessment tuples in the exact `work-integrity` then `research-evidence` order,
   and verify the kernel's one-value-per `(policy_id, rule_id, complete subject_refs tuple)`
   cardinality. The coordinator performs no second prose-based deduplication and does not choose a
   "strongest" value for duplicate keys; a duplicate is an internal policy-wiring defect. Allocate
   stable finding IDs in that exact emission order through
   `IdPort`, persist the candidate-to-ID map together with every exact `FindingBasis`, and finalize
   the immutable deterministic-result object. Basis records contain rule/fact/state/visibility/
   coverage refs only and stay encrypted; they do not enlarge public finding schemas. CAS
   `reserved → local_ready` only after that object is durable. A resumed operation reopens and
   verifies the object rather than rerunning merely because the process restarted.
3. **Plan semantic/privacy work.** If mode is `deterministic_only` or no semantic case is material,
   record `not_requested` and advance. Otherwise load the effective `ReviewContextProfile` and
   build the bounded internal `ReviewPacket`, `SemanticCase`, and `CandidateContext` from `F`, `D`,
   `FrozenCase.case.allowed_ids` as `frontier_refs`, the durably pinned deterministic IDs as
   `local_check_refs`, and deterministic
   assessments. For each pinned assessment call the sole
   `ports.semantic.project_review_assessment` mapper. A representable assessment preserves every
   fact/ref association and uses the schema's bare `FindingKind` token for both `finding_kind` and
   `rule_id`. A valid internal assessment with any outbound ref field over 16 is skipped as one
   whole assessment with its exact structural `not_selected` omission; no ref is truncated and the
   full basis remains in the encrypted local result and material coverage fold. A malformed basis
   rejects case construction rather than becoming an omission. The packet contains
   goal/obligations/claims/decisions, a material ordered timeline,
   exact bases/change observations/coverage, mechanically linked recorded excerpts permitted by the
   profile, and an omission manifest. It never browses Git/filesystem or asks a provider what else
   to fetch. Persist them encrypted,
   insert the deduplicated semantic job plus privacy proposal identity, and advance to
   `semantic_wait`. Capability/policy absence does not skip this durable accounting under
   `semantic_required`; it becomes a terminal incomplete semantic reason.
4. **Prepare privacy case, resolve authority, and claim one attempt.** Invoke `PrivacyCoordinator`
   to enforce context-profile selection, classify, intersect policy, minimize/redact/scan locally,
   reserve the exact prepared case,
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
   coordinator constructs final `SemanticProvenance`. It first proves the matching terminal receipt
   is durably readable and identity-matched; absence, nonterminal state, or mismatch raises the
   registered coordinator defect `privacy_receipt_not_durable` and leaves the operation resumable.
   Passing adapter-returned `ProviderAttemptProvenance` directly into a finding, event, or public
   result raises `provider_attempt_provenance_is_not_final`; it is never coerced by the final
   provenance codec. Normal v0.1 operation retains no raw
   refused/invalid provider plaintext. A semantic result is not selectable until its structural
   commitment-bearing egress receipt is durable. No network call, parsing, encryption,
   fsync, prompt, or approval wait occurs inside a database transaction.
6. **Post-validate and select.** Deterministically reject or downgrade wrong schema/version,
   invented/out-of-case IDs or quotes, deterministic-status claims, coverage/authorship upgrades,
   disallowed finding kinds/count/severity/conclusions, missing uncertainty/provenance, challenges
   without a supported discrepancy/direct agent message/closed requested next step, refs outside
   `frontier_refs ∪ local_check_refs`, claims that hidden/unrecorded code means unchanged code,
   duplicates without new basis, stale `F`/`D`, or output after cancellation/deadline/supersession.
   For an accepted `ReviewerChallenge`, resolve broad cited refs through the frozen projection and
   pinned local deterministic map to sorted event/obligation/claim roots; reject an unresolved or
   empty root set. The challenge maps to the existing semantic finding summary/detail and remains
   `semantic_model_derived`; it cannot rewrite the paired deterministic basis, choose the eventual
   `Finding.policy_id` / `Finding.policy_version`, or serialize a same-check finding ID as a public
   subject. The final finding identity is derived later from the accepted `FindingKind`'s unique
   built-in pack owner, never from reviewer prose.
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
   selected semantic findings, compute `RankingContext` from the terminal policy/semantic facts and
   the component-wise weakest material coverage of `FrozenCase.case.coverage_by_ref`, every
   assessment/basis and candidate, semantic dependencies/outcomes, every typed case gap (including
   rootless/global gaps), and all candidates before capping,
   then call `rank_findings(deterministic, semantic, context, request.max_findings)`. Before each
   phase transition, renewal, reclaim, and final commit, reconstruct the two-field `FrozenCase`
   with its unchanged pure case and the replacement current lease returned by the port; a spent
   lease is never reused. In the final bounded atomic write, reverify that embedded current
   operation lease/current owner and material dependency
   revisions, repeat the no-pending-import predicate for this session, append one
   `check_recorded` plus one `finding_recorded` per returned finding, persist
   exact coverage/status/reason/versions, finalized optional attempt provenance, and suppressed count, store the canonical structural result,
   set `complete/terminal`, clear leases, and commit. If `F` or `D` is no longer current, that same
   transaction appends no event, stores one terminal `FRONTIER_CONFLICT` failure with the exact
   `frontier_changed|dependency_changed` safe reason, clears leases, and commits; same-ID replay
   returns that failure and a check against the new state requires a new request ID. The appended
   `CheckRecordedPayload` carries the required normalized scope and the exact policy-execution
   tuple from the durable result; neither is reconstructed later from verdict or findings. Only a current
   commit is acknowledged. An individual late/wrong-frontier semantic attempt may be labeled
   `stale` and cannot steer, but that attempt-level status is distinct from final frozen-case
   currentness. Reviewer
   messages exist only inside semantic findings that survive validation/ranking. At any cap of at
   least two (including the default of three),
   the ranker's single material-challenge slot guarantees the highest-ranked priority-1/2 challenge
   reaches the ordinary `finding_summary` agent-context projection without a second advisory schema;
   `max_findings=1` and priority-3 explanations retain ordinary rank semantics.
10. **Replay or resume.** Same request ID and digest returns the stored original result without
    rerunning. Current generation plus live operation/job lease returns retryable
    `OPERATION_PENDING`. Expiry or stale owner generation permits fenced CAS reclaim; after the CAS
    commits, the coordinator resumes by opening and authenticating the exact stored resume object
    named by the operation row. It never rebuilds or republishes a case for that row. Only a
    binding-mismatched/missing stored object, corruption, an impossible transition, or
    contradictory durable identity is quarantinable.

### Verdict and coverage

- Verdict is exactly `action_required`, `no_issue_detected`, `insufficient_coverage`, or
  `incomplete_check`; the token `pass` is forbidden.
- `RankingContext.completeness` is derived exactly as registered in `INTERFACES.md`; it is never a
  renderer/provider choice. `required_incomplete` takes precedence as `incomplete_check`.
  Otherwise `action_required` means at least one returned finding has its registered
  `actionable=true` trait.
  `no_issue_detected` means no unresolved deterministic completion-integrity issue was found at
  the recorded coverage, never that all work is correct. Missing/redacted/unknown material,
  semantic unavailability under optional mode, or stale dependencies may select
  `insufficient_coverage`. A required deterministic policy failure, or any terminal
  semantic/privacy failure under `semantic_required`, selects `incomplete_check`.
- Coverage is the component-wise weakest material dependency plus sorted explicit gaps across the
  full pre-cap check context. It is not recomputed from returned finding IDs, so suppressing or
  diversity-replacing a weak finding cannot strengthen it. Semantic
  findings remain `semantic_model_derived`; deterministic post-validation never upgrades their
  origin.
- Result includes exact `CheckPolicyExecution` records in canonical requested-pack order, semantic
  status/reason, optional finalized attempt provenance, subject and result
  frontiers, returned findings, suppressed count, coverage, and relevant versions. It never hides
  lower-priority suppression or a failed/skipped check.
- The coordinator and `CheckRecordedPayload` use the same `SemanticStatus` spelling. A solely
  late/non-selected provider attempt is `late`; an attempt rejected against its authorized
  frontier/dependency is `stale`. A change to the final frozen case's `F` or `D` instead terminally
  fails the operation with `FRONTIER_CONFLICT` and appends no check event.
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
authority-bearing values explicitly and returns `FrozenCase | CheckCommitResult`; the latter is a
terminal same-digest replay returned without rerunning. Within that one port call, the case is
built before its resume object; the final reservation is the only step that may install the
object pointer, and only after atomically revalidating idempotency/import/head/projection/
dependency authority. Reclaim returns the stored object rather than rebuilding it.
`advance_check_phase` receives the optional durable-object reference required by a recoverable
phase and returns a replacement lease. Every advance, renewal, or reclaim replaces
`FrozenCase.lease` before the next authority-bearing call. Finalization passes exact
`policy_executions` and returns `CheckCommitResult`. The application must not use `hasattr`,
adapter casts, or a concrete repository escape hatch.

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
- If final frozen `F` or `D` changes before finalization, stale output cannot steer and the only
  outcome is the terminal `FRONTIER_CONFLICT` described above; no check/finding event is appended.
  Attempt-level `stale` remains a semantic status only when an otherwise current coordinator
  rejects a particular late/wrong-authority attempt.
- If an allowed problem-local excerpt was never recorded, the packet records `not_recorded` and
  weakens coverage where material. It never infers an empty diff or reads the workspace to fill the
  gap.
- Object/key/storage failures before final commit leave resumable state and/or orphan encrypted
  objects. Only verified corruption or contradictory state quarantines.
- `privacy_receipt_not_durable` and `provider_attempt_provenance_is_not_final` are internal
  `ProtocolValueError` reasons owned at this coordinator boundary. Neither is mapped to a provider
  refusal/unavailable result, and neither may be exposed as raw public text. The former resumes
  receipt verification/persistence; the latter is a programmer defect that prevents finalization.

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
7. The reviewer-to-agent loop reuses finding/respond/publish/check; model output cannot add a
   workflow operation, event family, context-fetch round, or waiver path.
8. A semantic finding/event/result never contains provisional adapter provenance, and final
   provenance never exists before its matching terminal privacy receipt is durable.

## Tests

- `specs/tests/unit.md`: mode/scope/max boundaries, logical digest exclusions, deterministic
  policy accounting and bases, review-profile packet selection, split reference allowlists,
  verdict/coverage matrix, adversarial semantic post-validation and ranking.
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
