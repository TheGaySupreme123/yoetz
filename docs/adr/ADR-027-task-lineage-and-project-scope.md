# ADR-027 — Task lineage and project scope identity

**Status:** Accepted (2026-09-05), owner-directed in
[issue #494](https://github.com/TheGaySupreme123/yoetz/issues/494). Docs-only ratification; wire,
catalog, admission, rollup, consent ceremony, and host mapping land in issues #495–#508.
**Implemented by:** this ADR, [`docs/INTERFACES.md`](../INTERFACES.md) (lineage and project names,
states, and consent boundaries), and [`docs/OPEN_QUESTIONS.md`](../OPEN_QUESTIONS.md) (E-013 and
F-021 posture). No module generates `prj_`, stores lineage, or retires `workspace_task_exists`
yet.
**Relates to:** ADR-003 (layout and one-task-per-bundle), ADR-008 (clients never open bundles),
ADR-009 (authorization lattice), ADR-010 (harness port; host mapping stays evidence-gated),
ADR-022 (observation writer and workspace-keyed local store), and the #250/#352
no-cross-task-state posture this decision bounds.

## Context

Yoetz has no identity for "part of a task" or "several tasks in one effort". Subagent work is
invisible (Claude Code) or metadata-only evidence (Codex `SubagentStart` / `SubagentStop`). A
second concurrent agent in one workspace is refused (`workspace_task_exists`). A receipt can speak
only for a single ledger.

The one-task-per-workspace guard and the #250/#352 no-cross-task-state posture were load-bearing
while observation, start-catalog attach, and advice snapshots assumed a single live occupant. They
also made honest multi-agent work unrepresentable. A 2026-08-31 maintainer design session chose
the model below; this ADR is the public ratification.

This decision does not change on-disk layout, does not open a shared writable ledger, and does not
populate a harness capability cell.

## Decisions

1. **A child is a real task with its own bundle.** Lineage does not split a ledger. ADR-003
   decision 4 stays: the layout is `catalog.sqlite3` + `tasks/<task-id>/`, and one task owns one
   bundle. A child is another `tsk_` with another bundle. Clients never open a sibling or child
   bundle (ADR-008). Rollup, status, and coordination are service-side projections.

2. **Lineage lives in the catalog.** The catalog holds `parent_task_id` (nullable `tsk_`),
   `lineage_depth` (non-negative integer; a root is 0), and
   `lineage_creation_provenance` (`parent_minted` | `child_self_registered`). Nesting may be
   recorded to any depth. Wire field shapes are #495; catalog columns are #496.

3. **Two creation paths, unequal provenance.** `parent_minted` is the blessed path: the parent
   service call allocates the child and records the edge. `child_self_registered` is the fallback:
   a child `start` names a parent task the caller already holds; the edge is recorded with weaker,
   receipt-visible provenance. Neither path is inferred from a host event name.

4. **Abandoned is a terminal incomplete state.** A lineage child has catalog lifecycle
   `live`, `complete`, `abandoned`, or `cancelled` in addition to ordinary `TaskRouteState`.
   `abandoned` is terminal and incomplete. `cancelled` is a recorded terminal close that is not
   completion. Delegation mint/attach/self-register/abandon/cancel are #499.

5. **Receipt rollup is one-level and severity-dependent.** A parent receipt projects only its
   direct children. Actionable current findings on a direct child **block** the parent receipt.
   Informational child findings **annotate**. A live child is a recorded **open gap**. An
   abandoned child is a recorded **incomplete gap**. A grandchild is visible only through its
   parent. Parent check and status child views are #500 and #501. Coverage-bounded wording is
   unchanged: a clean parent rollup is not "Yoetz verified" the children.

6. **A project is a first-class object.** Accepted `IdKind.project` uses prefix `prj_` and is
   server-generated under the same `<prefix>_<lowercase UUIDv4>` rule as every other server kind.
   The live `IdKind` enum does not yet include it (#495). A project is host-agnostic. Workspace or
   git binding is one membership kind, not the object's identity. Membership kinds begin as
   `workspace` (the trusted `repository_privacy_commitment`, never public `workspace_ref`) and
   `task`. Further non-filesystem kinds may be added later without changing the project id.
   Project kind is `workspace_bound` (implicit) or `general` (explicit and amendable).

7. **Hybrid birth.** The second concurrent live task in a workspace materializes an implicit
   `workspace_bound` project. A `general` or multi-repo project is created explicitly. An implicit
   project persists when concurrency later drops to one task; dissolve is #505. CRUD, link, unlink,
   and amend are #505. Project status surfaces are #504.

8. **Consent extends the ADR-009 lattice; it does not invent a second same-workspace ceremony.**
   `AuthorizationScopeKind` gains `project` between `machine` and `workspace`. A project scope
   carries `installation_id` and `project_id`. `contains()` is membership-aware and is not
   decidable from the scope tuple alone: `machine` contains `project`; `project` contains a
   `workspace`, `task`, or `request` whose catalog membership belongs to that project;
   `workspace` never contains `project`. Same-workspace coordination under a `workspace_bound`
   project inherits the existing workspace grant and needs no new grant. A `general` or otherwise
   cross-workspace project requires an explicit project-scope grant before coordination, rollup
   projection across those workspaces, or membership mutation. Overlay storage and the ceremony
   are #502. Today's four-kind enum and structural `contains()` remain executable until then.

9. **F-021 "project-level confirmation" is not this object.** Observation consent remains one
   confirmation via a private workspace commitment, separate from egress consent (ADR-009
   decision 16 / resolved F-021). That existing workspace observation consent is not the `prj_`
   project and is not a project-scope grant.

10. **Observation writers stay per task and session.** ADR-022 decision 1 is unchanged. Decision
    14 is unchanged: a mapped session's advice snapshot is never silently fed a workspace-wide
    aggregate. Once multiple live tasks share a workspace, the bundle-resident workspace-keyed
    observation local store from `migrations/bundle/0004.sql`
    (`observation_inspection_snapshots`, `observation_workspace_session_routes`,
    `observation_session_advice`) is logically project-scoped and must not remain in one task
    bundle. Issue #496 relocates it to the catalog or project home. Until that relocation,
    `workspace_task_exists` still keeps the store single-task-safe.

11. **Bounded reversal of #250 / #352.** Cross-task state exists only as catalog lineage, project
    membership, and service-side rollup or coordination under decision 8. Each task keeps its own
    ledger and writers. There is no shared writable ledger. Coordination detectors (file/path
    overlap, plan/obligation overlap) are #503 and remain advisory, consent-gated, and
    non-authoritative for completion.

12. **Retired guards and replacement invariants.**

    - `workspace_task_exists` remains live until #497. Today's rule: a new `external_ref` in a
      workspace that already has a non-quarantined task (`initializing` counts) returns that
      typed `SESSION_CONFLICT` and discloses no selector. Replacement invariant: attach uniqueness
      stays `(workspace_ref_commitment, external_ref_commitment)`; many live tasks may share a
      workspace; the second live task materializes the implicit project. `mode=create` as an
      explicit-sibling hatch is re-specified in #497. Hook auto-attach recovery that today
      requires the workspace's sole non-quarantined route is re-specified there as well. Service
      audit of remaining single-task assumptions is #498.
    - Bundle-resident workspace-keyed state from `migrations/bundle/0004.sql` is named above;
      replacement home is #496.
    - `list_workspace_task_ids` generalizes toward project membership listing in #496 and remains
      off the public MCP surface until a later issue adds one.

13. **Host mapping stays evidence-gated.** Claude Code Task-tool children, Codex
    `SubagentStart` / `SubagentStop` correlation, and the Cursor subagent/delegate decision are
    #506, #507, and #508. E-013 is not flipped. This ADR populates no capability cell and does
    not make `hook_observed` earnable from a delegate signal.

## Consequences

Agents and contributors have one public place for the identity model. Later issues can add wire
fields, catalog columns, admission, rollup, and host adapters without re-litigating whether a
child is a task or whether a project is a workspace.

The current executable contract is unchanged: `workspace_task_exists` still fires, observation
0004 still lives in the task bundle, and `AuthorizationScopeKind` still has four members. Docs
that describe those live guards must keep saying they are current until the owning follow-up
issue. Packaged guidance (`guidance/workflow.md` and skill copies) is deliberately not rewritten
in this change.

A parent receipt that later rolls up children will be stronger than today's single-ledger
receipt and still coverage-bounded. An abandoned or live child is a recorded gap, not a silent
pass.

## Alternatives considered

**Keep subagents as metadata-only evidence.** Rejected: receipts then cannot speak for delegated
work except as an untyped gap, and Claude Code children remain invisible.

**One shared writable ledger for a workspace.** Rejected: it collapses writer identity, replay,
and ADR-008's client boundary, and it reintroduces the cross-task snapshot leak #250/#352
closed.

**Treat the workspace as the project.** Rejected: general and multi-repo efforts need an
amendable object whose identity is not a filesystem binding.

**Require a new grant for every same-workspace sibling.** Rejected: the existing workspace grant
already names that repository; a second ceremony would not add a new trust boundary.

**Infer children from host event names as soon as the names exist.** Rejected: E-013 still
requires installed-artifact evidence before a capability cell or a mint path may bind those
events.

**Dissolve an implicit project when concurrency drops to one.** Rejected: dissolve would flap
and lose membership history; #505 owns an explicit dissolve.
