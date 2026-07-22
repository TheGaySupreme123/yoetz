# src/yoetz/ports/integrations.py — explicit harness-integration filesystem boundary

**Wave:** D | **ADRs:** ADR-005, ADR-007, ADR-010 | **Imports (spec-tree):** `protocol/errors.md`,
`version.md` | **Imported by:** `application/integrations.py.md`,
`adapters/integrations/codex_skill.py.md`, CLI integration composition and capability tests

## Purpose

Define the narrow boundary for installing the canonical harness-neutral Yoetz guidance into one
trusted project, in the exact layout one agent harness expects. Integration changes user-owned
repository files, so it must be previewed, digest-bound, consented, and conflict-safe. This port
prevents the CLI/application from manipulating skill directories directly and prevents a generic
plugin/config writer from emerging.

The port names the capability, never one harness. Codex is the first adapter, not the definition
(ADR-010). Adding a first-party harness is exactly one `HarnessId` value plus one adapter under
`adapters/integrations/`; it changes no method, no shared type, and no guidance content, which is
owned once under `guidance/` and never duplicated per harness. This is what lets a fork add a
harness first-party without editing the shared registry.

v0.1 integrates only skill-shaped guidance files. It does not edit any harness's global/project
config, register MCP, install executables, modify source package resources, or manage arbitrary
skills. A harness Yoetz has not profiled needs no integration at all: it reaches the same six
operations and the same guidance through the MCP baseline (`mcp/resources.md`).

## Public surface

- `class IntegrationsPort(Protocol)` with async methods:
  - `preview_skill(harness: HarnessId, command: SkillPreviewCommand) -> IntegrationPreview`;
  - `install_skill(harness: HarnessId, command: SkillApplyCommand) -> IntegrationResult`;
  - `status_skill(harness: HarnessId, command: SkillStatusCommand) -> IntegrationStatus`;
  - `remove_skill(harness: HarnessId, command: SkillApplyCommand) -> IntegrationResult`.
- `HarnessId` — closed enum; v0.1 membership is exactly `codex`. An unregistered value is
  `INVALID_REQUEST` at the boundary and never reaches an adapter.
- `HarnessProfile(harness_id, skill_root, frontmatter_profile, capability_profile_ids,
  supported_versions, hooks_by_capability_profile: Mapping[str, HarnessHookProfile | None])` — the
  frozen per-harness descriptor. It is
  reviewed packaged data, not caller input: `skill_root` is the exact relative install directory
  (`.agents/skills/yoetz/` for `codex`), and `frontmatter_profile` is the harness's required
  skill-header shape. The three support collections may be jointly empty to represent an explicit
  unprofiled/unadvertised harness while E-002 remains open. Otherwise capability-profile IDs and
  supported versions are both nonempty, and `hooks_by_capability_profile` has exactly one explicit
  value for every capability-profile ID and no other key. A v0.1 value may be trigger-only,
  observation-capable (`observation_events` nonempty), both, or `None` after E-013 passes. For
  first-party Codex, observation is required once capability-proven; unproven cells keep
  `observation_events=()` and cannot emit `hook_observed` (ADR-005, ADR-010).
- `HarnessHookProfile(trigger_event, trigger_payload_profile_id, evidence_case_ids,
  trigger_action="reground_status", duplicate_policy="coalesce", loop_policy="single_flight",
  failure_policy="best_effort", observation_events)` — the closed two-armed
  hook descriptor. Event/payload/evidence/`observation_events` identifiers are exact bounded values
  from one capability cell; wildcard/range values are invalid. `observation_events` may be nonempty
  for first-party Codex when capability-proven; otherwise exactly `()`. It distinguishes
  **observation hooks**, which report what the harness saw and are the only arm that earns
  `hook_observed` (only from real observation evidence via `ObservationPort`), from **trigger
  hooks**, which freeze one exact lifecycle event and the `reground_status` action, prompt the
  agent to call `status`, and earn no coverage.
- `IntegrationScope` — single v0.1 value `trusted_project`.
- `IntegrationAction` — `install|replace|remove|noop`.
- `IntegrationState` — `absent|installed_exact|modified|partial|incompatible|unsafe`.
- `IntegrationReason` — `confirmation_required|preview_stale|target_untrusted|target_unsafe|
  source_invalid|destination_conflict|modified_copy|partial_install|version_incompatible|
  marker_invalid|write_failed|remove_refused`.
- `IntegrationTarget(scope, project_root)` — redacted opaque target; never serialized/logged. The
  adapter validates an exact trusted repository root; cwd is never implicit.
- `SkillSource(harness_id, skill_version, protocol_range, harness_tested_set, resource_set_digest,
  files: tuple[IntegrationFile, ...])`. `harness_tested_set` is the profile's tested host-version
  set; it may be empty only for the explicit unprofiled/unsupported source case and then adapter
  compatibility is `unsupported` and state is `incompatible`. The shared guidance members it
  carries are identical across harnesses by construction.
- `IntegrationFile(relative_path, size, sha256, media_type)` — allowed canonical skill/reference or
  managed marker member.
- `SkillPreviewCommand(request_id, target, requested_action, replace_modified)`.
- `SkillApplyCommand(request_id, target, requested_action, preview_digest,
  explicitly_accepted, replace_modified)`.
- `SkillStatusCommand(target)`.
- `IntegrationPreview(action, state_before, source_digest, installed_digest,
  compatibility, file_changes, warnings, preview_digest)`.
- `IntegrationStatus(state, source_digest, installed_digest, compatibility, file_states,
  managed_marker_valid)`.
- `IntegrationResult(action, state_before, state_after, source_digest, installed_digest,
  changed_files, preview_digest)`.
- `IntegrationError(reason, safe_details)` — bounded, no target path/file content.

All names above are shared cross-module types requiring registry entries. `file_changes` are bounded
structural changes (`create|replace|remove|unchanged`, relative canonical path, before/after digest/
size). A local human diff is a separate ephemeral renderer input and never enters logs/evidence.

## Behavior

### Source and target contract

Source is the installed, manifest-verified packaged bundle for the requested `HarnessId`: the
harness's skill header, the harness-neutral guidance members copied byte-for-byte from
`resources/guidance/`, compatibility metadata, and no runtime-generated instruction. Source,
repository canonical files, and packaged resources are byte-identical. No network/update channel or
checkout fallback. Two harnesses installing the same guidance member install the same bytes; a
harness adapter may choose the header and layout its host requires, never the guidance content.

Target scope is exactly `<trusted-project>/<profile.skill_root>`, resolved only from the reviewed
`HarnessProfile` for the requested harness — `.agents/skills/yoetz/` for `codex`. The project root
is supplied explicitly and must be an existing trusted repository directory owned by the user. No
global/user harness scope, parent search, nested repository guess, workspace name match, symlink
path, or caller-chosen destination relative path exists in v0.1. A caller cannot supply, override,
or extend `skill_root`; an unregistered `HarnessId` is rejected before any path is resolved.

### Preview

Preview is read-only and:

1. verifies packaged resource manifest and source file inventory/digests/compatibility;
2. validates project root/destination containment, ownership, permissions and no symlink/hardlink/
   traversal/case collision;
3. reads only expected destination/marker files under strict caps and stat-before/after checks;
4. classifies absent, exact, modified, partial, incompatible, or unsafe;
5. computes structural file changes and compatibility warnings;
6. returns a canonical preview digest over target identity commitment, action, source set, current
   destination file/marker digests, compatibility and replace policy.

Preview never writes a marker/directory. It may produce an ephemeral bounded unified diff for an
interactive local renderer, but modified user bytes never enter a port result, diagnostic, public
evidence, exception, or persisted plan.

### Install/replace

Apply requires `explicitly_accepted=True`, an exact recomputed preview digest, and `action=install`
or `replace`. If destination changed since preview, return `preview_stale` without mutation.

- `absent`: stage complete source and managed marker in an owner-only sibling directory, fsync all,
  verify bytes, atomically rename to destination, fsync parent.
- `installed_exact`: return idempotent `noop`; never rewrite just to change timestamps.
- `modified|partial`: default `modified_copy`/`partial_install`, preserving all files. Replacement is
  legal only when preview explicitly requested `replace_modified=True`, the user confirmed that
  exact digest, and current bytes still match preview. Never infer consent from `--yes` alone.
- `incompatible`: do not install unless the previewed source itself is compatible with the installed
  Yoetz protocol and the profile's harness support policy; no `force` bypass in v0.1.

Managed marker `.yoetz-install.json` is canonical structural JSON containing marker schema,
`harness_id`, adapter/skill/protocol/resource-set versions, exact managed relative file digests/
sizes, scope, and marker self-digest. It contains no absolute project path, username, time, Git
remote/branch, task data or secret. Skill and guidance files remain byte-identical to package
resources. A marker written by one harness adapter is not valid for another; a mismatched
`harness_id` is `marker_invalid`, never a silent adoption of another harness's directory.

For replacement, stage a complete new directory and a structural swap marker. Rename old destination
to an owner-only rollback sibling, rename new into place, fsync parent, verify, then remove rollback
only after success. A crash recovery first validates marker/current/rollback digests; ambiguity or
modified bytes stops for explicit preview rather than choosing a copy. The adapter never edits an
individual modified file in place.

### Status

Status is read-only and recomputes source/destination/marker digests. It distinguishes exact managed
install, identical content without valid managed marker (reported `modified`/unmanaged), modified
managed file, missing/extra expected member (`partial`), source/installed protocol or harness-version
mismatch, and unsafe target. It does not update marker or call network. Status for one harness reads
only that harness's `skill_root` and never reports another harness's install state.

### Remove

Remove requires preview/action confirmation and current state `installed_exact` with a valid marker
whose every managed file digest matches. Move the exact directory to a sibling removal staging name,
fsync parent, verify no unexpected/unmanaged member existed, then delete staging and fsync. If any
file/marker is modified, missing, extra, unsafe, or changed after preview, refuse and preserve it.
There is no force-remove-modified behavior in v0.1.

### Hook arms

`HarnessHookProfile` distinguishes two arms, and the distinction is a coverage distinction rather
than a mechanical one.

A **trigger hook** fires on a harness lifecycle event — context compaction is the motivating case —
and prompts the agent to re-ground by calling `status`. It earns no coverage. It observes nothing:
the only disclosure it causes is the `status` result, bounded by the ordinary provenance rules and
the `agent_context` ceiling exactly as an agent-initiated `status` call is, and it discloses nothing
that call would not already have returned. A trigger hook therefore touches no coverage lattice
value, strengthens no claim, and changes no honesty wording; a task whose agent re-grounded because
a trigger fired is indistinguishable, in recorded evidence, from one whose agent called `status` on
its own judgment.

An **observation hook** reports to Yoetz what the harness saw the agent do, and it is the only arm
that earns the `hook_observed` publication channel or artifact-observation class (ADR-005), and only
when real observation evidence exists. For first-party Codex this arm is a required v0.1
capability once the exact cell is proven; live ingest is owned by `ObservationPort` (local control,
not a seventh MCP tool), not by this filesystem port.

Whether a specific harness exposes usable trigger or observation points is capability evidence
rather than a spec choice. E-013 must freeze the exact event, payload/privacy boundary, coalescing
and loop guard, gap codes, optional-host failure behavior, and installed-artifact case IDs before
that exact v0.1 capability cell selects a `HarnessHookProfile` with either arm. Every unproven cell
selects `None` or keeps `observation_events=()`. The profile declaration itself performs no
configuration mutation: this port still does not install hooks or edit host config, and the
capability case must prove the reviewed host-native mechanism is present.

## Errors and edge cases

- Nonexistent/untrusted project, nested/symlinked destination, broad permissions, case collision,
  oversized/unreadable/changing files or package-resource mismatch fails before mutation.
- Failure/crash during absent install leaves either absent or complete exact directory; replacement
  leaves old or new complete and a bounded recovery marker. It never silently resolves modified
  ambiguity.
- A manually created byte-identical directory without the managed marker is not deletion-safe.
- No method edits any harness's configuration directory, MCP configuration, Git index, `.gitignore`,
  package sources, or any path outside the exact skill directory/sibling staging marker.
- An unregistered, absent, or misspelled `HarnessId` fails at the boundary with `INVALID_REQUEST`
  before target resolution, source loading, or any filesystem read.
- Errors/status/evidence use state/reason/digests/relative managed paths only, never project path or
  modified file content.

## Invariants

1. Every mutation is bound to a fresh preview and explicit consent.
2. Modified/unmanaged copies are never overwritten or removed by default.
3. Installed skill/guidance bytes equal verified packaged resources.
4. Scope is one explicitly supplied trusted project and one profile-fixed destination.
5. Integration cannot edit harness/MCP config or arbitrary files.
6. Status is read-only and network-free.
7. The port is harness-neutral: no method, shared type, or guidance member names one harness, and
   adding a harness adds an adapter plus a `HarnessId` value only.
8. Guidance content is identical across harnesses; only the header and layout may differ.
9. No v0.1 profile declares observation hooks, so no integration strengthens coverage over the MCP
   baseline. A trigger-only exact profile cannot strengthen it either: it observes nothing and
   earns no coverage.

## Tests

- `specs/tests/unit.md`: state/action lattice, preview digest, marker schema, compatibility and safe
  error shapes; unregistered `HarnessId` rejection before path resolution; `skill_root` is
  profile-derived and not caller-influenced.
- `specs/tests/integration.md`: absent/exact/modified/partial/unmanaged install/replace/remove,
  symlink/path/permission/TOCTOU and crash-swap recovery; a foreign `harness_id` marker is
  `marker_invalid` rather than adopted.
- `specs/tests/capability.md`: real Codex discovery plus source/wheel/installed parity and modified-
  copy protection.
- `specs/tests/packaging.md`: manifest/source bytes and no unexpected integration resource; every
  harness's installed guidance members are byte-identical to `resources/guidance/` and to each
  other.
- A second synthetic test-only harness profile exercises the port's neutrality without shipping a
  second real integration, proving the fork path needs no registry edit.
- Exact-profile map tests reject missing/extra/inferred hook entries; passing trigger-only cells
  coalesce duplicate lifecycle notifications, call `status`, avoid loops, and leave coverage
  unchanged, while unsupported cells retain the manual recovery path.

## Open questions

None.

Global/user skill scope is deferred to v0.2.

Additional first-party `HarnessId` values are deferred to v0.2 and are additive by construction:
they require an adapter and a profile, not a port or guidance change. Observation for first-party
Codex is in v0.1 via `ObservationPort` once E-013 proves the cell; this filesystem port still
grants no hook-install/configuration authority. A trigger-only cell remains valid recovery
ergonomics and earns no coverage.
