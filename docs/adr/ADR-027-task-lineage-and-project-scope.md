# ADR-027 — Task lineage and project scope identity

**Status:** Accepted (2026-09-05), owner-directed in
[issue #494](https://github.com/TheGaySupreme123/yoetz/issues/494). This is a docs-only
ratification. Wire fields, catalog storage, admission, rollup, coordination grants, and host
mapping remain separately owned by issues #495–#508.
**Implemented by:** this ADR, [`docs/INTERFACES.md`](../INTERFACES.md), and the corresponding
entries in [`docs/OPEN_QUESTIONS.md`](../OPEN_QUESTIONS.md). No module generates `prj_`, stores
lineage, admits multiple automatic tasks, or retires `workspace_task_exists` as a result of this
decision.
**Relates to:** ADR-003 (layout and one-task-per-bundle), ADR-008 (clients never open bundles),
ADR-009 (privacy and local coordination boundaries), ADR-010 (harness evidence), ADR-022
(observation writer and task-local state), and the #250/#352 no-cross-task-state posture this
decision bounds.

## Context

Yoetz has no identity for work that is part of a larger effort. Subagent work is invisible for
some hosts or is retained only as metadata, automatic attachment has historically refused a second
task in one workspace, and a receipt currently speaks for one task ledger. A 2026-08-31 maintainer
design session, reviewed and amended on 2026-09-05 in issue #494, chose the model below.

The model must keep four facts separate: grouping (which tasks share a project), work lifecycle
(whether a task is open or closed), session health (whether a host session is still in contact),
and receipt history (which recorded frontier a receipt describes). It must also preserve the
privacy and attribution boundaries that make a task ledger meaningful.

This decision does not change the on-disk layout, open a shared writable task ledger, add an MCP
tool, or populate a host capability cell.

## Decisions

### Cross-cutting constraints

1. **No new MCP tools (C1/D11).** Lineage and project behavior uses the existing seven model-facing
   operations (`start`, `publish_work`, `check`, `status`, `receipt`, `respond`, and
   `read_guidance`): delegation is a `start` mode; lifecycle, acceptance, cancellation, and
   write-off are `publish_work` event kinds; lineage and project views are `status` views; child
   preview is a `check` section; and rollup is a `receipt` section. The 2026-09-05 pre-trim
   measurement recorded an advertised surface of 204,404 bytes for the policy profile and 204,658
   bytes for the strict profile against the reviewed 205,000-byte ceiling. Those numbers are a
   reviewed budget snapshot, not a claim about the current post-merge surface. Descriptor/instruction
   trimming in #504 step 0 lands before schema growth, and every later increase names the bytes it
   consumes. Project management verbs remain CLI-only.

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
   implicit repository project. The future project registry owns a general project's `prj_`
   identity and its amendable grouping over repositories, workspaces, and tasks. None of these
   identities selects a task for resume, and none collapses into another.

4. **Origin and acceptance are separate (C4/D2).** `origin` is immutable and is one of
   `parent_minted | self_registered | host_observed`. `acceptance` is one of
   `pending | accepted | rejected` and is set only by the parent through a recorded publication.
   Its only transitions are `pending → accepted` and `pending → rejected`; an accepted
   relationship can never become rejected. `mode=delegate` creates `parent_minted` plus
   `accepted` atomically. Only the service and host-observation paths may stamp `host_observed`;
   ordinary publication cannot award it or rewrite an origin.

5. **Parent results use frozen dependency manifests (C5/D12).** Before a parent check or receipt
   uses child facts, a `child-dependencies-recorded` manifest is written to the parent ledger. The
   manifest carries the child identity, origin, acceptance, child frontier, check/receipt identity,
   coverage, findings state, `lineage_authority_revision`, and an optional project
   `membership_generation`. The pure kernel evaluates only that recorded snapshot, never a live
   child bundle.

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
   `.agents` trees. This ratification does not rewrite packaged guidance or claim a host capability.

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
| D6 | Birth an implicit project at the second **live** task in the same repository; repository membership is the grouping key and never a resume selector. | Worktrees are the modal multi-agent setup, while sequential sessions must not create a project or select a task by possession. |
| D7 | Project coordination is local disclosure authorized by each source workspace's consent and, for general or cross-repository projects, an explicit generation-bound coordination grant. The privacy egress lattice is unchanged. | Membership is a mutable graph; cross-repository semantic dispatch is outside this series, so a new egress scope kind would add authority without a new permitted channel. |
| D8 | Retire `workspace_task_exists` only from automatic admission after the #497 decision table is implemented; explicit `mode=create` sibling admission remains. | The existing conflict is part of the ended-session recovery path; removing it before replacement would strand predecessor work and pending observations. |
| D9 | Coordination detectors are advice-first. A finding requires a declared, unaddressed coordination obligation; a disposition addresses it and a later qualifying check resolves it. | Intentional collaborative edits should not create an unconditional finding storm. |
| D11 | Add no MCP tool and pay every later schema or descriptor increase from the reviewed advertised-surface budget after #504 step 0. | The 2026-09-05 pre-trim measurement left 596 policy bytes and 342 strict bytes of headroom in that reviewed snapshot. |
| D12 | Record child facts in a frozen parent dependency manifest before a parent result uses them. | Parent results must be reproducible from the parent ledger and cannot depend on mutable child state. |
| D13 | Keep grouping, work lifecycle, session health, and receipt history as independent facts with separate owners and transitions. | Conflating them turns a missing host event, an open task, and an incomplete receipt into the wrong claim. |
| D14 | Give lineage its own three-channel disclosure authority and preserve each child's restrictions; acceptance never widens those restrictions. | A rollup crosses task scopes, so its authority must be explicit, bounded, and impossible to escape by rejecting an accepted relationship later. |

## Task lineage and project scope

1. **A child is a real task with its own bundle.** The layout remains `catalog.sqlite3` plus
   `tasks/<task-id>/`; a child is another `tsk_` with another bundle. The catalog records
   `parent_task_id`, `depth` (0 for a root), `lineage_digest`, `origin`, `acceptance`, and
   `work_state`. Session health is a per-session fact, not a route shortcut. Clients never open a
   sibling or child bundle; the service projects their permitted views.

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
   children from the frozen manifest. Current actionable child findings block the parent receipt;
   informational findings annotate it. A live child is an open gap and an abandoned child is an
   incomplete gap. Grandchildren are visible only through their direct parent. The clean-parent
   wording remains coverage-bounded and never says that Yoetz verified every child.

5. **A project is a grouping object, not an egress scope.** `IdKind.project` uses server-generated
   `prj_` identifiers under the same lowercase UUIDv4 rule as the other server kinds. The project
   registry is future work in #495/#496. Its initial kinds are `repository` (implicit) and
   `general` (explicit and amendable); membership kinds are `repository`, `workspace`, and `task`.
   Membership rows are append-only and carry a monotonic `membership_generation`. A repository
   commitment or workspace commitment is a membership fact, never the project's identity.

6. **Project birth and opt-out are repository-scoped.** The second concurrent live task in one
   repository materializes an implicit repository project. A general or multi-repository project
   is created explicitly. An implicit project persists when concurrency drops to one. A repository
   may opt out of automatic grouping and cross-task disclosure; opting out never erases accepted
   delegations, obligations, or recorded receipt dependencies. Project management is #505.

7. **Coordination is local and generation-bound.** A fact from a member task enters coordination
   only when that source workspace's own observation consent is active. Consent for one worktree
   never silently covers another worktree, and there is no repository-wide standing grant. General
   or cross-repository coordination requires a recorded `coordination_grants` authorization bound
   to the current membership generation. Revocation, unlink, dissolve, and opt-out advance that
   generation and stop queued flow at admission/delivery. External semantic dispatch that bundles
   content from two repositories is outside this series.

8. **Only shared-mutable state moves out of task bundles.** The #498 inventory classifies every
   table and cache by owner, key, provenance, object root, retention, and concurrency rule before
   #496 writes a catalog migration. Task-owned provenance remains in its task bundle. The expected
   shared-mutable candidates are workspace-to-session routing from `0004` and the verification-job
   scheduling authority from `0003` (including its per-workspace running-job uniqueness). Job
   results, inspection snapshots, and session advice remain task-owned unless the inventory proves
   otherwise. No blanket relocation of every workspace-keyed table is authorized, and no shared
   writable ledger is introduced.

9. **Admission preserves continuity boundaries.** `workspace_task_exists` remains executable on
   the automatic `create_or_attach` path until #497's decision table is implemented. Explicit
   `mode=create` already admits a sibling. Attach uniqueness remains the pair
   `(workspace_ref_commitment, external_ref_commitment)`, and a persisted same-host binding or
   explicit selector is the only continuity proof. Repository or project membership never selects
   a task for resume. The service-wide assumptions audit is #498.

10. **Bounded membership reversal.** Once the project membership and status projection exist,
    membership in a live, consented project may authorize a service-rendered `status` view of
    sibling task identity and state. It never authorizes attaching to, resuming, or selecting a
    sibling task, and it never widens content or egress authority. Until that implementation lands,
    possession of a workspace reference alone remains insufficient to discover or attach another
    task.

11. **Host mapping stays evidence-gated.** Claude Code Task-tool children, Codex
    `SubagentStart` / `SubagentStop` correlation, and Cursor delegate mapping are #506, #507, and
    #508. E-013 is not flipped by this ADR. No host event name alone earns `host_observed`, a child
    task, or `hook_observed` coverage.

## Resolved questions

| Question | Resolution |
|---|---|
| Q1: Is a host-observed child a task or annotation? | A provisional lineage annotation under one correlation identity with `host_observed` origin and pending acceptance; it becomes one child only when accepted delegation or cooperative self-registration binds to it. |
| Q2: Can a live-child gap clear? | Only in a new receipt after a new frozen manifest and qualifying recheck. The old receipt is immutable. |
| Q3: How many general projects may contain one task? | In v1, the implicit repository project plus at most one general project. Detection identities are project-scoped. |
| Q4: Who declares coordination obligations? | An agent declares or accepts them explicitly. Detectors provide advice; automatic obligations require a separately ratified standing policy. |
| Q5: Where does automatic admission land? | #497 is Increment B. Increment A proves explicit sibling and delegation safety while preserving recovery. |
| Q6: Can a repository opt out of implicit grouping? | Yes. Opt-out stops automatic grouping and cross-task disclosure but never erases accepted delegations, obligations, or recorded receipt dependencies. |

## Consequences

Agents and contributors have one public identity model, while later issues can add wire fields,
catalog columns, admission, rollup, coordination grants, and host adapters without reopening the
meaning of a child or project. The current executable contract remains unchanged: the four-kind
`AuthorizationScope`, `workspace_task_exists`, one task per bundle, and the existing observation
ownership continue to govern until their owning issues land.

The bounded reversal is limited to a future service-side status projection. It does not grant task
attachment, task resume, content disclosure, or external semantic dispatch. A parent receipt that
later rolls up children remains coverage-bounded, and a live or abandoned child is an explicit gap,
never a silent pass.

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
