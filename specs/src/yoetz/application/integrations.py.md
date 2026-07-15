# src/yoetz/application/integrations.py — Codex skill preview, consent, status, and removal

**Wave:** D | **ADRs:** ADR-005, ADR-007 | **Imports (spec-tree):**
`ports/integrations.md`, `protocol/errors.md`, `ports/diagnostics.md` | **Imported by:**
CLI `integrate codex skill` commands and integration tests

## Purpose

Own the support use case that lets a user deliberately integrate the packaged Yoetz skill into one
trusted Codex project. It validates intent, enforces preview-before-mutation, keeps prompts/rendering
out of the filesystem adapter, and maps local integration failures without leaking project content.

It is not an MCP tool, public workflow operation, package installer, Codex config editor, or skill
updater daemon.

## Public surface

- `IntegrationService(integrations: IntegrationsPort, diagnostics: DiagnosticsPort)`.
- `preview_codex_skill(IntegrationRequest) -> IntegrationPreview`.
- `install_codex_skill(IntegrationRequest, IntegrationConfirmation) -> IntegrationResult`.
- `status_codex_skill(IntegrationStatusRequest) -> IntegrationStatus`.
- `remove_codex_skill(IntegrationRequest, IntegrationConfirmation) -> IntegrationResult`.
- `IntegrationRequest(request_id, project_root, action, replace_modified=False)`.
- `IntegrationStatusRequest(project_root)`.
- `IntegrationConfirmation(preview_digest, explicitly_accepted, channel)` with
  `interactive|noninteractive_flag`.

The request/confirmation names and exact confirmation-channel vocabulary are shared application
values registered in `specs/INTERFACES.md`.
`project_root` is secret/redacted in representation and excluded from diagnostics.

## Behavior

### Preview/status

Validate action and explicit project-root presence/length/syntax without resolving it. `preview`
converts to the exact port command and returns its structural result. The CLI renderer may, on the
local user's terminal, separately request/display a bounded source-vs-current diff through an
ephemeral adapter view; this service never persists or logs modified bytes.

Status calls the read-only port method and renders state/source/installed digests, compatibility,
marker validity and structural file states. It must not create directories, repair markers, update
files, register MCP, or infer trust from cwd.

### Install

1. Require request action `install` or `replace` and call preview (or accept a preview already shown
   in the same command flow) to establish current digest.
2. Present behavior is outside this file, but execution requires an `IntegrationConfirmation` with
   `explicitly_accepted=True` and exact preview digest. A noninteractive caller supplies both
   `--yes` and `--preview-digest`; otherwise return confirmation-required immediately.
3. If state is `modified|partial|unmanaged`, refuse unless request explicitly set
   `replace_modified=True` before preview and confirmation binds it. A generic yes cannot flip it.
4. Call `install_codex_skill`; on stale preview, return the new-preview requirement and do not retry
   automatically against changed user files.
5. Return structural before/after/digests and bounded next steps: skill install does not mean MCP is
   registered/available and does not prove Codex discovery until capability evidence.

An exact installed state is idempotent no-op. The service never changes requested scope or chooses a
parent/global project to make installation succeed.

### Remove

Preview `remove`, disclose that only a valid exact managed copy can be deleted, require explicit
confirmation/digest, and call port. Modified/partial/unmanaged states are preserved with instructions
for manual review; there is no force path. Successful removal does not delete global config, MCP
registration, package, ledger data, or another skill.

### Error/cancellation/diagnostics

Map integration reasons to CLI invalid/conflict/unsafe/internal exit families. Expected file conflict
is not an internal exception. Re-raise cancellation; retry after uncertain filesystem swap uses same
request/preview only if adapter state still matches, otherwise status + new preview.

Diagnostics contain action, state before/after, compatibility, managed file count, source/installed/
preview digest and bounded reason. Exclude project root, Git remote/branch, modified content/diff,
user/home name, environment, exception and any Yoetz task data.

## Errors and edge cases

- Non-TTY invocation never prompts implicitly or reads stdin that may contain operation JSON.
- Incompatible source/target has no force flag; updating package/Codex is a separate user action.
- A status/preview target may be absent; absence is a normal structural state.
- Cancellation after adapter swap is outcome-unknown; status determines exact/old/unsafe without
  overwriting.
- Integration is optional. Failure never blocks unrelated Codex work unless host/user policy does.

## Invariants

1. Mutating calls require a current exact preview and explicit consent.
2. Service never handles filesystem bytes, package resources, or Codex config directly.
3. Modified content cannot enter diagnostics/evidence or be silently replaced/removed.
4. Skill installation is not described as MCP availability or capability proof.
5. Only trusted-project scope exists in v0.1.

## Tests

- `specs/tests/unit.md`: request/action/confirmation mapping, modified-replace double consent, no-TTY,
  errors/cancellation/privacy.
- `specs/tests/integration.md`: application-to-port ordering and stale-preview/no-auto-retry.
- `specs/tests/subprocess.md`: exact CLI preview/decline/install/status/remove output and exits.
- `specs/tests/capability.md`: real Codex discovery, optional unavailable behavior and byte parity.

## Open questions

None.
