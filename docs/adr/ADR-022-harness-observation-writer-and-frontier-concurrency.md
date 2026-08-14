# ADR-022 — Harness observation writer identity and observation-tolerant optimistic concurrency

**Status:** Accepted (2026-08-13), acknowledged for issues #214–#223.
**Amended:** 2026-08-14 for moderator-approved issue #244.
**Implemented by:** `src/yoetz/application/observation_materialize.py`,
`src/yoetz/application/observation_coordinator.py`, `src/yoetz/adapters/memory/ledger.py`,
`src/yoetz/application/publish_work.py`, and `src/yoetz/service/ready_composition.py`.
**Relates to:** ADR-009, ADR-010, ADR-020, and issues #214, #216, #217, #223, and #244.

**Proposed amendment for issue #231:** `provider_not_ready` remains bounded local advice, but the
observation coordinator does not materialize it as an agent-facing finding. Provider readiness is a
machine condition rather than a repair to the recorded work. A requested check still records the
exact semantic status and coverage limitation, and receipts retain that limitation; the amendment
does not make the affected receipt clean. This is the narrow option that preserves proof-based
finding resolution and does not change `ResponseDisposition` or finding-kind traits.

## Context

Live observation and cooperative MCP publication share one task-global ledger frontier. Observation
originally reused the cooperative writer, so each delivered hook moved the frontier held by the
agent and made state-sensitive `publish_work` race continuously. Splitting the writer alone is not
enough because the optimistic guard is task-scoped. `check` has the same problem after freezing a
case: an intervening observation makes its exact-frontier commit fail terminally.

Observation also converted lifecycle prose into claims the agent did not make and derived advice
finding identity from the entire changing envelope snapshot. That made delivery capable of creating
unsupported claims and unbounded duplicate findings.

## Decisions

1. Each task session has a deterministic observation writer derived from task id, session id,
   mapping version `obs-writer/1.0.0`, and role `observation`. Admission derives this writer alongside
   writers admitted by completed starts; it does not fabricate a start operation or add durable
   authority. Existing observation operations are looked up under both this writer and the legacy
   cooperative writer during an upgrade.

2. A record is observation-authored only when all four facts hold: actor id is
   `yoetz:observation-coordinator`, actor type is `harness`, authorship assurance is
   `harness_observed`, and publication channel is `hook_observed` or `engine_derived`. Actor id and
   type are caller-supplied, so they are insufficient alone. Assurance and channel are service
   derived; ordinary requests cannot select either admitted observation channel.

3. A stale `expected_frontier` is tolerated only when it is behind the real head and every
   intervening accepted record satisfies that four-fact predicate. The new batch appends at the real
   task head and reports that head as its subject frontier. Any cooperative, importer, or otherwise
   authored intervening record still conflicts. Dry-run applies the identical rule. Append
   computation and commit share one critical section so no concurrent append can invalidate ledger
   digests or assigned sequences between them.

4. A frozen check retains exact-frontier equality. Observation-authored append attempts while any
   case for the session is frozen receive retryable `OPERATION_PENDING`; the durable outbox retries
   after the check commits. The check verdict is never retargeted to a frontier it did not inspect.

5. Hook envelopes are observation evidence, not agent claims. Completion-like lifecycle signals
   materialize as metadata-only `evidence_recorded` events. A narrow claim path exists only when the
   structural payload explicitly carries an admitted `claim_kind`. Mapping version
   `obs-ledger/1.2.0` separates these identities from historical observation-derived claims.

6. Deterministic advice finding ids are candidate-scoped over policy, kind, rule code, evidence
   refs, and detail token. Candidate subject refs map only their source envelopes to ledger event ids;
   a standing condition with no envelope anchors to the session lifecycle event. Already-recorded
   candidates with the same subject refs are not appended again.

7. A provenance-dispute response disposition is deferred to issue #224. This change removes the
   false observation-authored claim premise but does not add a fourth `ResponseDisposition`, change
   response schemas, migrate SQL constraints, or weaken the rule that a recorded finding remains
   historically visible.

8. Standing provider-readiness advice is not converted into a `material_limitation_omitted`
   finding. That finding kind remains actionable for an omission in the work account. Provider
   configuration and availability remain visible through observation advice before a check and
   through the check coverage vector and receipt limitations after one.

9. Successful routine reads are rate-limited at the task-ledger boundary. The hook adapter labels
   only a closed set of read tools and conservatively parsed single read-only shell commands as
   `routine_read`; shell composition, redirection, mutation flags, unknown commands, and other
   ambiguity fail closed to ordinary materialization. The complete hook envelope remains in the
   bounded local observation store, but its pre-event and successful post-event do not mint
   individual task-ledger records. A failed post-event still materializes action and result records,
   and edits, checks, tests, lifecycle events, and other non-routine observations are unchanged.

10. Every newly accepted observation-authored append records one bounded pending frontier-motion
    notice for the originating Codex session. Contiguous undelivered appends coalesce their
    from/to sequences and record count. A later advice-safe `PostToolUse` hook surfaces that the
    motion was hook-observed, explains that held cooperative publish frontiers remain admissible
    only across observation-authored motion, and directs exact-frontier operations to refresh
    status. The notice is removed only after its bytes reach the hook consumer. It grants no
    authority, changes no optimistic-concurrency predicate, and adds no MCP operation.

## Security and privacy consequences

The observation writer id is derivable from public task/session identifiers, but derivation grants
no publication authority. `publish_work` derives its channel from the closed integration kind; a
caller cannot request `hook_observed` or `engine_derived`, and a spoofed harness actor therefore does
not pass the observation predicate.

ADR-009 includes `other_writer` disclosure provenance. The production privacy enforcer currently
ships without a provenance resolver. When that resolver is implemented, this harness writer must
classify as `engine_derived_from_self_authored`: a hook observation is an observation of the agent's
own action, not unrelated third-party authorship.

## Consequences

Cooperative work can retain a frontier across observation-only delivery, while real cooperative
concurrency still conflicts. Check cases make progress by temporarily applying back-pressure to the
outbox rather than weakening verdict binding. Observation can contribute evidence and bounded
advice without impersonating the agent or minting the same standing finding indefinitely. Routine
reads retain their local observation evidence without producing an unbounded task-ledger trail, and
cooperative writers learn about meaningful observation-authored frontier motion before their next
state-sensitive operation.
