# src/yoetz/application/integrations.py — harness skill preview, consent, status, and removal

**Wave:** D | **ADRs:** ADR-005, ADR-007, ADR-010 | **Imports (spec-tree):**
`ports/integrations.py.md`, `protocol/errors.md` | **Imported by:**
CLI `integrate <harness> skill` commands and integration tests

## Purpose

Own the support use case that lets a user deliberately integrate the packaged Yoetz guidance into
one trusted project, in the layout their agent harness expects. It validates intent, enforces
preview-before-mutation, keeps prompts/rendering out of the filesystem adapter, and maps local
integration failures without leaking project content.

It is harness-neutral: it carries an exact `HarnessId` through to the port and holds no per-harness
branch, path, or content of its own (ADR-010). Codex is the only v0.1 value.

It is not an MCP tool, public workflow operation, package installer, harness config editor, or skill
updater daemon.

## Public surface

- `IntegrationService(integrations: IntegrationsPort, diagnostics: IntegrationDiagnosticSink)`.
- `preview_skill(IntegrationRequest) -> IntegrationPreview`.
- `install_skill(IntegrationRequest, IntegrationConfirmation) -> IntegrationResult`.
- `status_skill(IntegrationStatusRequest) -> IntegrationStatus`.
- `remove_skill(IntegrationRequest, IntegrationConfirmation) -> IntegrationResult`.
- `IntegrationRequest(request_id, harness, project_root, action, replace_modified=False)`.
- `IntegrationStatusRequest(harness, project_root)`.
- `IntegrationConfirmation(preview_digest, explicitly_accepted, channel)` with
  `interactive|noninteractive_flag`.
- `IntegrationDiagnostic` — frozen path-free structural observation containing only harness,
  action, phase/outcome, before/after state, compatibility, managed-file count, structural digests,
  and an optional closed `IntegrationReason`; `IntegrationDiagnosticSink.record_integration`
  consumes it. It is separate from startup `DiagnosticsPort`/`StartupCheckResult`.

The request/confirmation names and exact confirmation-channel vocabulary are shared application
values registered in `specs/INTERFACES.md`.
`project_root` is secret/redacted in representation and excluded from diagnostics.
The ordinary control mapping preserves `harness` as a required field on preview, status, and
execute bodies. In v0.1 its only wire value is the schema constant `codex`; the service never
defaults a missing wire discriminator before constructing either request value.

## Behavior

### Preview/status

Validate the `HarnessId` against the closed registry, then validate action and explicit project-root
presence/length/syntax without resolving it. An unregistered harness is `INVALID_REQUEST` before any
port call. `preview` converts to the exact port command and returns its structural result. The CLI
renderer may, on the local user's terminal, separately request/display a bounded source-vs-current
diff through an ephemeral adapter view; this service never persists or logs modified bytes.

Status calls the read-only port method and renders state/source/installed digests, compatibility,
marker validity and structural file states. It must not create directories, repair markers, update
files, register MCP, or infer trust from cwd. The service never defaults, guesses, or infers a
harness from the environment, cwd, installed editors, or running processes; the user names it.

### Install

1. Require request action `install` or `replace` and call preview (or accept a preview already shown
   in the same command flow) to establish current digest.
2. Present behavior is outside this file, but execution requires an `IntegrationConfirmation` with
   `explicitly_accepted=True` and exact preview digest. A noninteractive caller supplies both
   `--yes` and `--preview-digest`; otherwise return confirmation-required immediately.
3. If state is `modified|partial|unmanaged`, refuse unless request explicitly set
   `replace_modified=True` before preview and confirmation binds it. A generic yes cannot flip it.
4. Call `install_skill` with the requested harness; on stale preview, return the new-preview
   requirement and do not retry automatically against changed user files.
5. Return structural before/after/digests and bounded next steps: skill install does not mean MCP is
   registered/available and does not prove harness discovery until capability evidence.

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

Diagnostics contain harness ID, action, state before/after, compatibility, managed file count,
source/installed/preview digest and bounded reason. Exclude project root, Git remote/branch,
modified content/diff, user/home name, environment, exception and any Yoetz task data.

## Errors and edge cases

- Non-TTY invocation never prompts implicitly or reads stdin that may contain operation JSON.
- Incompatible source/target has no force flag; updating the package or the harness is a separate
  user action.
- A status/preview target may be absent; absence is a normal structural state.
- Cancellation after adapter swap is outcome-unknown; status determines exact/old/unsafe without
  overwriting.
- Integration is optional and per-harness. Failure never blocks unrelated harness work unless
  host/user policy does, and never blocks the MCP baseline, which needs no integration at all.
- Installing for one harness neither installs, upgrades, nor invalidates another's copy.

## Invariants

1. Mutating calls require a current exact preview and explicit consent.
2. Service never handles filesystem bytes, package resources, or harness config directly.
3. Modified content cannot enter diagnostics/evidence or be silently replaced/removed.
4. Skill installation is not described as MCP availability or capability proof.
5. Only trusted-project scope exists in v0.1.
6. The service holds no per-harness branch, path, or content; it forwards an exact `HarnessId`.
7. The harness is always named by the user and never inferred from the environment.

## Tests

- `specs/tests/unit/application/test_integrations.py.md`: request/action/confirmation mapping,
  modified-replace double consent, no-TTY,
  errors/cancellation/privacy; unregistered harness rejected before any port call; no environment
  inference of the harness.
- `specs/tests/integration.md`: application-to-port ordering and stale-preview/no-auto-retry; the
  synthetic second harness profile proves no service-side branch exists.
- `specs/tests/subprocess.md`: exact CLI preview/decline/install/status/remove output and exits.
- `specs/tests/capability.md`: real Codex discovery, optional unavailable behavior and byte parity.

## Open questions

None.
