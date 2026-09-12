# ADR-027 — Task lineage and project scope identity

**Status:** Accepted design (2026-09-05), tracked by
[issue #494](https://github.com/TheGaySupreme123/yoetz/issues/494). The recorded foundation
[PR #591](https://github.com/TheGaySupreme123/yoetz/pull/591) is merged; the 0.3 candidate now
implements the Increment-A lineage/project primitives and Increment-B admission, rollup,
coordination-grant, and host-mapping surfaces described here. This ADR records durable design and
implementation scope; native capability evidence and limits are maintained in the host integration
runbooks.
**Implemented by:** this ADR, [`docs/INTERFACES.md`](../INTERFACES.md), and the corresponding
entries in [`docs/OPEN_QUESTIONS.md`](../OPEN_QUESTIONS.md), plus the candidate's lineage/project
application and kernel modules, SQLite catalog/lineage/host-mapping/project-operation adapters,
catalog migrations `0004` and `0005`, the bundle events-family and observation migrations, schemas,
and focused tests. Generated mirrors are
produced by the resource ripple. These source and test paths describe implementation scope; they do
not broaden a host capability profile, whose evidence and limits remain in the host runbooks.
**Relates to:** ADR-003 (layout and one-task-per-bundle), ADR-008 (clients never open bundles),
ADR-009 (privacy and local coordination boundaries), ADR-010 (harness evidence), ADR-022
(observation writer and task-local state), and the #250/#352 no-cross-task-state posture this
decision bounds.

## Context

Before the 0.3 implementation, Yoetz had no identity for work that was part of a larger effort.
Subagent work was invisible for some hosts or retained only as metadata, automatic attachment
historically refused a second task in one workspace, and a receipt spoke for one task ledger. A
2026-08-31 maintainer design session, recorded and amended on 2026-09-05 in issue #494, chose the
model below.

The model must keep four facts separate: grouping (which tasks share a project), work lifecycle
(whether a task is open or closed), session health (whether a host session is still in contact),
and receipt history (which recorded frontier a receipt describes). It must also preserve the
privacy and attribution boundaries that make a task ledger meaningful.

The implementation preserves the one-bundle-per-task layout, does not open a shared writable task
ledger, and does not add an MCP tool. Native host capability claims remain separately evidence-
gated; implementing a host mapping path does not populate or upgrade a capability cell.

## Current 0.3 implementation state

The candidate carries the behavior ratified here through durable source paths. Child tasks have
their own catalog and bundle identity, parent and self-registered lineage use separate origin and
acceptance facts, and parent checks/receipts consume service-stamped frozen dependency manifests.
The project registry stores `prj_` identities, append-only membership generations, repository and
workspace members, and generation-bound coordination grants. Existing `status`/`check`/`receipt`
surfaces carry the lineage and project views; project management remains CLI-only.
Cross-repository child admission is part of the candidate's Increment-B path and requires the
current project-generation coordination grant at both admission and delivery; an ungranted or
stale generation remains refused.

Automatic admission uses the persisted-binding decision table: the automatic
`create_or_attach` path can recover a validated ended-session mapping, while explicit `mode=create`
still rejects an identical identity pair with `workspace_task_exists`. Recovery rechecks workspace,
task, mapping, and pending-row state under the documented locks and preserves predecessor routing.
Host adapters can retain provisional child annotations and bind them only through validated native
or cooperative identities; unsupported or ambiguous host signals remain explicit attribution gaps.

The implementation preserves the existing privacy, attribution, and coverage boundaries. A source
path or focused test does not broaden a host capability profile; the host runbooks remain the durable
record of native support evidence and limits.

## Decisions

### Cross-cutting constraints

1. **No new MCP tools (C1/D11).** Lineage and project behavior uses the six workflow operations
   (`start`, `publish_work`, `check`, `status`, `receipt`, and `respond`) plus the existing
   read-only `read_guidance` support tool: delegation is a `start` mode; lifecycle, acceptance,
   cancellation, and write-off are `publish_work` event kinds; lineage and project views are
   `status` views; child preview is a `check` section; and rollup is a `receipt` section. The
   2026-09-05 pre-trim
   measurement recorded an advertised surface of 204,404 bytes for the policy profile and 204,658
   bytes for the strict profile against the reviewed 205,000-byte ceiling. Those numbers are a
   dated budget snapshot; descriptor/instruction trimming in #504 step 0 precedes schema growth,
   and every later increase names the bytes it consumes. Project management verbs remain CLI-only.

2. **Three independent task facts (C2/D13).**
   - **Work lifecycle**, held per task, is `open | closed | cancelled | abandoned | written_off`.
     The transition owner is explicit: the task's explicit work publication closes work, a parent
     action cancels or writes off accepted work, and the recorded service abandonment policy
     abandons work. A receipt request never changes work lifecycle.
   - **Session health**, held per session, is `active | contact_lost | ended`. The service records
     `active` for a held lease, `contact_lost` when that lease expires without an end event, and
     `ended` only for a host end event or explicit end. A missing host end event never establishes
     permanent liveness.
   - **Receipt history**, owned by receipt finalization, is the latest receipt identity and the
     frontier it describes. A task may receive an incomplete receipt and continue working. The
     derived predicate **live task** is work `open` and at least one session `active`.

3. **Identities stay separate (C3/D6).** The workspace/start identity owns `workspace_ref`, the
   canonical working-tree identity; a linked worktree is its own workspace. The Git/common-root
   and installation-keyed privacy adapters own repository identity, which is the key for an
   implicit repository project. The project registry owns a general project's `prj_`
   identity and its amendable grouping over repositories, workspaces, and tasks. None of these
   identities selects a task for resume, and none collapses into another.

4. **Origin and acceptance are separate (C4/D2).** `origin` is immutable and is one of
   `parent_minted | self_registered | host_observed`. `acceptance` is one of
   `pending | accepted | rejected` and is set only by the parent through a recorded publication.
   Its only transitions are `pending → accepted` and `pending → rejected`; an accepted
   relationship can never become rejected. `mode=delegate` creates `parent_minted` plus
   `accepted` atomically. Only the service and host-observation paths may stamp `host_observed`;
   ordinary publication cannot award it or rewrite an origin. The service owns child-dependency
   manifest stamping; ordinary client fields cannot self-award a manifest.

5. **Parent results use frozen dependency manifests (C5/D12).** Before a parent check or receipt
   uses child facts, a `child-dependencies-recorded` manifest is written to the parent ledger by
   the service-owned stamping path. The
   manifest carries the child identity, origin, acceptance, child frontier, check/receipt identity,
   coverage, findings state, `lineage_authority_revision`, and an optional project
   `membership_generation`. The pure kernel evaluates only that recorded snapshot, never a live
   child bundle. The lineage coordinator records changed child facts; receipt generation only
   reuses an already recorded manifest and never records or refreshes one (#500).

6. **User-controlled content stays out of structure (C6).** Project titles, descriptions, and
   host labels are encrypted objects rendered through the existing disclosure policy. They never
   enter structural JSON, catalog columns, tables, logs, or errors. Membership and lineage rows
   contain bounded identities, commitments, states, generations, and relations only.

7. **Terminology follows the trust boundary (C7).** Observation consent is workspace-level and
   separate from egress consent. `workspace_ref` is a workspace or working-tree identity, not a
   project identity. The first-class `prj_` object is a grouping and is not the existing
   workspace observation consent.

8. **Guidance follows behavior (C8).** Agent-facing guidance and generated trees change with the
   issue that adds the behavior an agent performs: #499 owns delegation guidance and #504 owns the
   descriptor/instruction trim. #566 remains the fixed-point consolidation issue for committed
   `.agents` trees. The 0.3 candidate updates the source guidance for the implemented behavior;
   generated mirrors must still be produced by the resource ripple, and guidance never claims a
   host capability without its exact evidence cell.

9. **Lineage has bounded disclosure authority (C9/D14).** An accepted parent–child relationship
   authorizes exactly three service-mediated channels:
   1. a child read used to build the frozen dependency manifest;
   2. disclosure of that manifest's bounded structural facts into the parent agent's context; and
   3. child-derived structural input to a parent semantic check.

   Each channel retains the child's source provenance and every existing category, never-send,
   task-scope, minimization, and authorization restriction. Project membership is unnecessary for
   lineage. Cross-repository lineage is prohibited in increment A and is revisited under #502 in
   increment B. The manifest's `lineage_authority_revision` records the governing lineage rule.
   `AuthorizationScope.contains()` remains unchanged; a project membership or lineage edge does
   not create egress authority.

## Dated amendments to the 2026-08-31 decisions

The following amendments are ratified with this ADR. Their reasons are part of the decision rather
than implementation notes.

| Decision | Amendment (2026-09-05) | Reason |
|---|---|---|
| D2 | Use three immutable origins plus a separate parent-controlled acceptance field. | Codex can expose a subagent identity without an agent-side `start`; acceptance must not rewrite provenance. |
| D6 | When `projects.auto_grouping` is enabled, birth an implicit project at the second **live** task in the same repository; repository membership is the grouping key and never a resume selector. | Worktrees are the modal multi-agent setup, while sequential sessions must not create a project or select a task by possession. |
| D7 | Project coordination is local disclosure authorized by each source workspace's consent and, for general or cross-repository projects, an explicit generation-bound coordination grant. The privacy egress lattice is unchanged. | Membership is a mutable graph; cross-repository semantic dispatch is outside this series, so a new egress scope kind would add authority without a new permitted channel. |
| D8 | Retire `workspace_task_exists` only from automatic admission after the #497 decision table is implemented; explicit `mode=create` sibling admission remains. | The existing conflict is part of the ended-session recovery path; removing it before replacement would strand predecessor work and pending observations. |
| D9 | Coordination detectors are advice-first. A finding requires a declared, unaddressed coordination obligation; a disposition addresses it and a later qualifying check resolves it. The ordinary agent-published declaration binds the exact detection, project, membership generation, recipient task, and existing open obligation; ordinary file/source obligations and acknowledgements never infer that binding. A live admitted task with no revealable attributable paths receives only a per-task `unobservable` coverage row; that row is not a pair detection, finding, counterpart disclosure, or path claim. | Intentional collaborative edits should not create an unconditional finding storm, and a frozen check must be able to prove which obligation authorized the finding. Coverage gaps must remain durable without inventing overlap evidence. |
| D11 | Add no MCP tool and pay every later schema or descriptor increase from the reviewed advertised-surface budget after #504 step 0. | The 2026-09-05 pre-trim measurement left 596 policy bytes and 342 strict bytes of headroom in that reviewed snapshot. |
| D12 | Record child facts in a frozen parent dependency manifest before a parent result uses them. | Parent results must be reproducible from the parent ledger and cannot depend on mutable child state. |
| D13 | Keep grouping, work lifecycle, session health, and receipt history as independent facts with separate owners and transitions. | Conflating them turns a missing host event, an open task, and an incomplete receipt into the wrong claim. |
| D14 | Give lineage its own three-channel disclosure authority and preserve each child's restrictions; acceptance never widens those restrictions. | A rollup crosses task scopes, so its authority must be explicit, bounded, and impossible to escape by rejecting an accepted relationship later. |

## Task lineage and project scope

1. **A child is a real task with its own bundle.** The layout remains `catalog.sqlite3` plus
   `tasks/<task-id>/`; a child is another `tsk_` with another bundle. The catalog records
   `parent_task_id`, `depth` (0 for a root), `lineage_digest`, `origin`, `acceptance`, and
   `work_state`. Nesting may be recorded to any supported depth, subject to #499's configured
   depth and fan-out ceilings with typed refusals; presentation remains one level. Session health
   is a per-session fact, not a route shortcut. Clients never open a sibling or child bundle; the
   service projects their permitted views.

2. **Creation paths have unequal provenance.** `parent_minted` is the blessed path: the parent
   service call allocates the child and records the edge. `self_registered` is the fallback: a
   child `start` names a parent task or validated parent selector the caller already holds and the
   service records the edge with weaker, receipt-visible provenance. Host delegate signals do not
   mint a child by themselves.
   A host-observed signal first creates a provisional `host_observed` annotation under one
   correlation identity with pending acceptance and no bundle; it becomes a child only when an
   accepted delegation or cooperative self-registration binds that identity. Issues #506–#508 own
   the evidence and host-specific mapping decisions.

3. **Work and session state do not collapse.** A child may be `open`, `closed`, `cancelled`,
   `abandoned`, or `written_off` as work, while each session independently reports
   `active`, `contact_lost`, or `ended`. `abandoned` is terminal and incomplete; `cancelled` and
   `written_off` are recorded outcomes, not completion. A live or abandoned child is a recorded
   parent gap. Only a new manifest and a qualifying recheck can clear a live-child gap in a later
   receipt; the old receipt remains immutable.

4. **Parent rollup is one level and severity-dependent.** A parent receipt projects only direct
   children from the frozen manifest. Current actionable findings on accepted children block
   clean-completion wording; the receipt is still produced and names the child and finding.
   Pending-acceptance children and informational findings annotate only. An accepted live child
   is an open gap and an accepted abandoned child is an incomplete gap. Grandchildren are visible only through their direct parent. The clean-parent
   wording remains coverage-bounded and never says that Yoetz verified every child.

5. **A project is a grouping object, not an egress scope.** `IdKind.project` uses server-generated
   `prj_` identifiers under the same lowercase UUIDv4 rule as the other server kinds. The project
   registry is implemented in the candidate through the #495/#496-owned surfaces. Its initial kinds
   are `repository` (implicit) and `general` (explicit and amendable); membership kinds are
   `repository`, `workspace`, and `task`. Membership rows are append-only and carry a monotonic
   `membership_generation`. A repository commitment or workspace commitment is a membership fact,
   never the project's identity. Membership generations remain the authority for coordination
   delivery.

6. **Project birth and opt-out are repository-scoped.** With `projects.auto_grouping` enabled, the
   second concurrent live task in one repository materializes an implicit repository project.
   When disabled, the task is admitted without creating a project row (#497). A general or
   multi-repository project is created explicitly. An implicit project persists when concurrency drops to one. A repository
   may opt out through `projects.auto_grouping` of automatic grouping and cross-task disclosure;
   opting out never erases accepted
   delegations, obligations, or recorded receipt dependencies. Project management is implemented in
   the #505-owned CLI surface.

7. **Coordination is local and generation-bound.** A fact from a member task enters coordination
   only when that source workspace's own observation consent is active. Consent for one worktree
   never silently covers another worktree, and there is no repository-wide standing grant. General
   or cross-repository coordination requires a recorded `coordination_grants` authorization bound
   to the current membership generation. Revocation, unlink, dissolve, and opt-out advance that
   generation and stop queued flow at admission/delivery. External semantic dispatch that bundles
   content from two repositories is outside this series.

8. **Only shared-mutable state moves out of task bundles.** The #498 inventory records each table
   and cache by owner, key, provenance, object root, retention, and concurrency rule. The candidate's
   catalog migration `0004` moves only the shared-mutable workspace-to-session routing and the
   lineage/project authorities that need catalog scope; task-owned provenance remains in its task
   bundle. Verification-job scheduling retains its existing ownership and per-workspace running-job
   uniqueness. No blanket relocation of every workspace-keyed table is authorized, and no shared
   writable ledger is introduced. The catalog and bundle migrations apply this ownership boundary;
   compatibility and rollback follow ADR-003 and the storage/recovery runbooks.

9. **Admission preserves continuity boundaries.** The candidate implements #497's persisted
   binding/selector decision table on automatic `create_or_attach`: a validated ended-session
   mapping may recover a task, while explicit `mode=create` still admits a sibling only when its
   identity pair is new and retains `workspace_task_exists` for an identical pair. Attach uniqueness
   remains the pair `(workspace_ref_commitment, external_ref_commitment)`, and a persisted same-host
   binding or explicit selector is the only continuity proof. Repository or project membership never
   selects a task for resume. Recovery and migration compatibility follow the durable contracts in
   ADR-003, ADR-022, and the storage/recovery runbooks; native host limits remain in the host
   integration runbooks.

10. **Bounded membership reversal.** The candidate's project membership and status projection
    authorize a service-rendered `status` view of bounded sibling task identity and state only when
    the workspace and membership generations are current. They never authorize attaching to,
    resuming, or selecting a sibling task, and never widen content or egress authority. Possession
    of a workspace reference alone remains insufficient to discover or attach another task.

11. **Host mapping stays evidence-gated.** The candidate implements a generic provisional host
    annotation registry and validated binding paths consumed by the Claude Code and Codex child
    signals; Cursor's current native path records unsupported or unbound delegate signals unless a
    cooperative task/session identity is supplied. Issues #506, #507, and #508 remain responsible
    for host-specific acceptance. E-013 and exact capability cells are not flipped by this ADR. No
   host event name alone earns `host_observed`, a child task, or `hook_observed` coverage.

12. **Project mutations have a durable retry journal.** Catalog migration `0005` adds
    `project_operations`, keyed by `(installation_id, request_id)`. The application boundary binds
    the canonical operation identity with an installation-keyed HMAC before reserving the row
    through `ProjectOperationJournalPort`; unkeyed title or description digests are not stored.
    `SqliteProjectOperationJournal` applies those
    reservations and phase changes in `BEGIN IMMEDIATE` transactions. The closed operation set is
    `create`, `link`, `unlink`, `amend`, `dissolve`, `opt_out`, `opt_in`, `grant`, and `revoke`.

    A journaled request advances monotonically through `reserved`, `text_ready`, `effect_pending`,
    and `completed`. Create and amend reserve the source task and route generation before the
    text store is called, alongside their object identities; create also reserves its project
    identity. Later phases record only `ProjectTextRef` structural pointers. The
    journal never stores title or description plaintext. Completion stores one canonical structural
    response and its digest, so a lost response can return the exact prior result without applying
    the catalog effect twice. Reusing a request ID with a different digest or operation is a
    conflict, and a stale phase or source/route identity remains refused.

    An authenticated retry of a completed request returns only its saved structural response
    before rechecking mutable route admission. This narrow recovery exception reports the earlier
    outcome even if its route or grant has since changed; it produces no new effect or plaintext
    disclosure and does not describe current authority. Incomplete requests must still satisfy
    current admission, route, and effect fences before making progress.

    The project catalog's membership-generation and route compare-and-swap checks remain the effect
    fence. A journal row is catalog-shared retry authority for the project command only; it does not
    select a task, move task-owned observation data, or grant workspace consent, disclosure,
    provider, semantic, or egress authority. Direct pre-journal application callers may omit the
    request ID and retain the compatibility seam; durable control composition supplies the journal
    when retry identity is required.

## Resolved questions

| Question | Resolution |
|---|---|
| Q1: Is a host-observed child a task or annotation? | A provisional lineage annotation under one correlation identity with `host_observed` origin and pending acceptance; it becomes one child only when accepted delegation or cooperative self-registration binds to it. |
| Q2: Can a live-child gap clear? | Only in a new receipt after a new frozen manifest and qualifying recheck. The old receipt is immutable. |
| Q3: How many general projects may contain one task? | In v1, the implicit repository project plus at most one general project. Detection identities are project-scoped. |
| Q4: Who declares coordination obligations? | An agent declares or accepts them explicitly. Detectors provide advice; automatic obligations require a separately ratified standing policy. |
| Q5: Where does automatic admission land? | The 0.3 candidate implements the Increment-B persisted-binding decision table while retaining explicit sibling admission and ended-session recovery. Its recovery and compatibility boundaries follow ADR-003, ADR-022, and the storage/recovery runbooks. |
| Q6: Can a repository opt out of implicit grouping? | Yes. Opt-out stops automatic grouping and cross-task disclosure but never erases accepted delegations, obligations, or recorded receipt dependencies. |

## Consequences

Agents and contributors have one public identity model, and the 0.3 candidate carries its wire
fields, catalog records, admission, rollup, coordination grants, and host-mapping paths without
changing the meaning of a child or project. The four-kind `AuthorizationScope` remains unchanged;
project membership and lineage do not widen egress authority. A child still has its own bundle, and
parent receipts remain coverage-bounded and consume frozen manifests.

The bounded membership reversal is implemented as a service-rendered status projection. It permits
bounded sibling identity and state after current workspace and membership checks, but does not grant
task attachment, task resume, content disclosure, or external semantic dispatch. A live, abandoned,
cancelled, or written-off child remains an explicit parent gap according to its recorded state.
Automatic admission recovery and migration compatibility follow the storage and recovery contracts.
Exact native-host capability evidence and limits are maintained in the host integration runbooks.

## Alternatives considered

**Keep subagents as metadata-only evidence.** Rejected: accepted delegation and self-registration
would remain unrepresentable, while host-observed facts would have no bounded lineage annotation.

**One shared writable ledger for a repository or project.** Rejected: it collapses writer identity,
replay, and the client boundary, and recreates the cross-task snapshot leak addressed by #250/#352.

**Treat the workspace as the project.** Rejected: a repository can have multiple linked worktrees,
and general or multi-repository efforts need an amendable grouping whose identity is not one path.

**Insert a project kind into the privacy egress lattice.** Rejected: project coordination is local
disclosure with generation-bound grants; `AuthorizationScope` and its `contains()` relation remain
unchanged, and cross-repository semantic dispatch is deferred.

**Infer children from host event names.** Rejected: E-013 requires installed-artifact evidence and
the host-specific decisions in #506–#508 before a capability cell or host-observed mapping may be
earned.

**Dissolve an implicit repository project when concurrency drops to one.** Rejected: dissolution
would flap and lose membership history; explicit dissolve belongs to #505.
