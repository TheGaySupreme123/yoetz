# src/yoetz/ports/integrations.py — explicit external-tool integration filesystem boundary

**Wave:** D | **ADRs:** ADR-005, ADR-007 | **Imports (spec-tree):** `protocol/errors.md`,
`version.md` | **Imported by:** `application/integrations.md`,
`adapters/integrations/codex_skill.md`, CLI integration composition and capability tests

## Purpose

Define the narrow boundary for installing the canonical Yoetz Codex skill into one trusted project.
Integration changes user-owned repository files, so it must be previewed, digest-bound, consented,
and conflict-safe. This port prevents the CLI/application from manipulating `.agents` directly and
prevents a generic plugin/config writer from emerging.

v0.1 integrates only the Codex skill files. It does not edit global/project Codex config, register
MCP, install executables, modify source package resources, or manage arbitrary skills.

## Public surface

- `class IntegrationsPort(Protocol)` with async methods:
  - `preview_codex_skill(command: CodexSkillPreviewCommand) -> IntegrationPreview`;
  - `install_codex_skill(command: CodexSkillApplyCommand) -> IntegrationResult`;
  - `status_codex_skill(command: CodexSkillStatusCommand) -> IntegrationStatus`;
  - `remove_codex_skill(command: CodexSkillApplyCommand) -> IntegrationResult`.
- `IntegrationScope` — single v0.1 value `trusted_project`.
- `IntegrationAction` — `install|replace|remove|noop`.
- `IntegrationState` — `absent|installed_exact|modified|partial|incompatible|unsafe`.
- `IntegrationReason` — `confirmation_required|preview_stale|target_untrusted|target_unsafe|
  source_invalid|destination_conflict|modified_copy|partial_install|version_incompatible|
  marker_invalid|write_failed|remove_refused`.
- `IntegrationTarget(scope, project_root)` — redacted opaque target; never serialized/logged. The
  adapter validates an exact trusted repository root; cwd is never implicit.
- `CodexSkillSource(skill_version, protocol_range, codex_tested_set, resource_set_digest,
  files: tuple[IntegrationFile, ...])`.
- `IntegrationFile(relative_path, size, sha256, media_type)` — allowed canonical skill/reference or
  managed marker member.
- `CodexSkillPreviewCommand(request_id, target, requested_action, replace_modified)`.
- `CodexSkillApplyCommand(request_id, target, requested_action, preview_digest,
  explicitly_accepted, replace_modified)`.
- `CodexSkillStatusCommand(target)`.
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

Source is the installed, manifest-verified packaged skill bundle: `SKILL.md`, exactly named reference
files, compatibility metadata, and no runtime-generated instruction. Source, repository canonical
files and packaged resources are byte-identical. No network/update channel or checkout fallback.

Target scope is exactly `<trusted-project>/.agents/skills/yoetz/`. The project root is supplied
explicitly and must be an existing trusted repository directory owned by the user. No global/user
Codex scope, parent search, nested repository guess, workspace name match, symlink path, or caller-
chosen destination relative path exists in v0.1.

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
- `incompatible`: do not install unless the previewed source itself is compatible with installed
  Yoetz/Codex support policy; no `force` bypass in v0.1.

Managed marker `.yoetz-install.json` is canonical structural JSON containing marker schema, adapter/
skill/protocol/resource-set versions, exact managed relative file digests/sizes, scope, and marker
self-digest. It contains no absolute project path, username, time, Git remote/branch, task data or
secret. Skill/reference files remain byte-identical to package resources.

For replacement, stage a complete new directory and a structural swap marker. Rename old destination
to an owner-only rollback sibling, rename new into place, fsync parent, verify, then remove rollback
only after success. A crash recovery first validates marker/current/rollback digests; ambiguity or
modified bytes stops for explicit preview rather than choosing a copy. The adapter never edits an
individual modified file in place.

### Status

Status is read-only and recomputes source/destination/marker digests. It distinguishes exact managed
install, identical content without valid managed marker (reported `modified`/unmanaged), modified
managed file, missing/extra expected member (`partial`), source/installed protocol/Codex mismatch,
and unsafe target. It does not update marker or call network.

### Remove

Remove requires preview/action confirmation and current state `installed_exact` with a valid marker
whose every managed file digest matches. Move the exact directory to a sibling removal staging name,
fsync parent, verify no unexpected/unmanaged member existed, then delete staging and fsync. If any
file/marker is modified, missing, extra, unsafe, or changed after preview, refuse and preserve it.
There is no force-remove-modified behavior in v0.1.

## Errors and edge cases

- Nonexistent/untrusted project, nested/symlinked destination, broad permissions, case collision,
  oversized/unreadable/changing files or package-resource mismatch fails before mutation.
- Failure/crash during absent install leaves either absent or complete exact directory; replacement
  leaves old or new complete and a bounded recovery marker. It never silently resolves modified
  ambiguity.
- A manually created byte-identical directory without the managed marker is not deletion-safe.
- No method edits `.codex`, MCP configuration, Git index, `.gitignore`, package sources, or any path
  outside the exact skill directory/sibling staging marker.
- Errors/status/evidence use state/reason/digests/relative managed paths only, never project path or
  modified file content.

## Invariants

1. Every mutation is bound to a fresh preview and explicit consent.
2. Modified/unmanaged copies are never overwritten or removed by default.
3. Installed skill/reference bytes equal verified packaged resources.
4. Scope is one explicitly supplied trusted project and one fixed destination.
5. Integration cannot edit Codex/MCP config or arbitrary files.
6. Status is read-only and network-free.

## Tests

- `specs/tests/unit.md`: state/action lattice, preview digest, marker schema, compatibility and safe
  error shapes.
- `specs/tests/integration.md`: absent/exact/modified/partial/unmanaged install/replace/remove,
  symlink/path/permission/TOCTOU and crash-swap recovery.
- `specs/tests/capability.md`: real Codex discovery plus source/wheel/installed parity and modified-
  copy protection.
- `specs/tests/packaging.md`: manifest/source bytes and no unexpected integration resource.

## Open questions

None.

Global/user skill scope is deferred to v0.2.
