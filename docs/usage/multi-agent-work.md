# Working with several agents

Each Yoetz task has its own work record. A parent can delegate part of its work to a child task,
and a project can group independently running tasks. These relationships help agents see unfinished
dependencies and overlapping declared work. They do not authorize an agent to resume another task.

## Delegating work

The parent calls `start` with `mode=delegate` and its current `session_id`. Yoetz creates an accepted
child relationship and returns the child task identity and an expiring `attach_handle`. Give the
complete handle to the intended child in its assignment. The child calls `start` with `mode=attach`
and that handle, then uses its returned session and writer for its own publications, checks, and
receipts. Keep the handle private to the intended child.

If a request times out, reuse its request ID and exact body. Retrying a delegation must recover the
same child. A used or expired handle cannot authorize a different attach request.

A child can alternatively create its own task with `parent_session_id`. That relationship starts
pending. The parent explicitly publishes `child_accepted` or `child_rejected`; acceptance preserves
the child's `self_registered` origin. An accepted relationship cannot later be rejected to remove
its findings from the parent's responsibility.

Host observations may show a provisional subagent annotation before any child starts with Yoetz.
An annotation is evidence of the observed host signal, not evidence that a child task was created,
published work, or received a receipt. Host support is described separately for each integration.

## Seeing child state

Use `status` with `view=lineage`, or `/lineage` in the terminal interface, to see direct children,
their origin and acceptance, work state, session health, and completion gaps. A child also sees its
parent reference. Deeper relationships are recorded, while each view shows one level.

Work and contact are different facts. Work stays open until an explicit closure or a recorded
cancellation, abandonment, or write-off. A missing host end event eventually means contact was
lost. It does not establish permanent activity or immediately mean the work was abandoned.

Publishing `delegation_cancelled` revokes the Yoetz delegation capability. It does not stop the host
process. Writing off or cancelling an accepted child keeps the dependency and its incomplete
outcome visible. Requesting a receipt never closes work.

## Reading a parent receipt

Parent checks evaluate dependency snapshots recorded in the parent's work record. Receipt creation
uses those recorded snapshots and never silently refreshes child state. If a later snapshot exists
after the check, the receipt names the uncovered dependency and requires a new check for a current
conclusion.

Accepted children with unresolved actionable findings prevent clean completion wording. Pending
children and informational findings annotate the receipt. Live children, lost contact, missing
data, and abandoned or written-off work appear as gaps. Yoetz can produce an honest incomplete
receipt while children are still working; an earlier receipt remains unchanged after they finish.

The parent's own obligations must still cover incorporating child work and testing the combined
result. A clean child receipt does not establish that integration happened.

## Projects and coordination

Automatic grouping begins when a repository has a second live task and grouping is enabled.
Different worktrees retain their separate workspace identities. A dormant task alone is never a
reason to resume it, and grouping never chooses a task for attachment.

`status` with `view=project`, `yoetz project status`, and `/project` show the admitted project view.
The CLI status command requires the exact `--session-id` and `--writer-id` held by the task and
one selector, either `--project-id` or `--task-id`; it never attaches or resumes a task.
General projects have `create`, `link`, `unlink`, `amend`, and `dissolve` commands. Repository
`opt-out` and `opt-in` control automatic grouping. Dissolving a project or opting out preserves
accepted delegations, obligations, and existing receipts.

Coordination requires workspace-level observation consent from each source workspace. Consent for
one worktree does not cover another. General or cross-repository coordination also requires approval
for the exact membership generation. Unlinking, dissolving, opting out, or revoking consent
invalidates older queued deliveries immediately. Membership does not permit combining repositories'
content in an external semantic review.

Consent revocation advances the active workspace observation generation and each affected project
membership generation before queued delivery can proceed. If the service is interrupted during that
fence, re-consent remains blocked until the recorded revocation is recovered; a later grant cannot
make an older detection eligible again.

Overlap detection uses declared resource scopes and structured requested items. The same relative
path in two worktrees of one repository is an integration overlap; the same spelling in unrelated
repositories is not. Hosts without attributable resource information report that coverage limit.
Detections normally produce advice. A finding requires an explicitly declared coordination
obligation that remains unaddressed; a recorded disposition addresses it, and a later qualifying
check can resolve the finding.

For an admitted detection, each affected task can receive the repository-relative overlap paths
through the project status projection. Yoetz rechecks both participants' current project
generation and workspace consent, then applies the source and recipient disclosure policies before
reading the encrypted detail. A denied, stale, or unavailable detail is shown as an explicit
omission. Structural catalog rows and MCP text summaries retain only task identities, counts, and
digests; they never carry raw paths.

The ordinary advice view carries the counterpart task plus the project, detection, and membership
generation selector needed to follow the same status dereference. Its resource field is hydrated
for the requesting recipient under the same gates, or remains an explicit repository-excerpt
omission.
