# src/yoetz_core/adapters/integrations/codex_skill.py — trusted-project Codex skill file adapter

**Wave:** D | **ADRs:** ADR-005, ADR-007 | **Imports (spec-tree):**
`ports/integrations.md`, `config/paths.md`, `observability/privacy.md`,
`resources/manifest.json.md`, skill and reference specs | **Imported by:** runtime/CLI integration
composition, integration/capability/packaging tests

## Purpose

Implement the exact filesystem/resource mechanics for `IntegrationsPort`: verify the packaged
canonical skill bundle, classify one trusted-project destination, stage/swap only that directory,
write a nonsecret managed marker, and refuse to overwrite or remove modified user-owned files.

This adapter deliberately does not parse or edit Codex TOML/config, register MCP, invoke Codex, run
Git, stage/commit files, update `.gitignore`, download anything, or manage arbitrary destinations.

## Public surface

- `class CodexSkillIntegration(IntegrationsPort)` — implements four port methods.
- `load_packaged_skill_source() -> CodexSkillSource` — verifies installed resource manifest before
  returning the exact inventory.
- `inspect_destination(target, source) -> DestinationInspection` — descriptor-safe read-only state.
- `build_managed_marker(source, scope) -> bytes` — canonical `.yoetz-install.json`.
- `recover_interrupted_swap(target, expected_preview=None) -> DestinationInspection` — conservative
  structural recovery; never chooses between modified copies.
- `DestinationInspection` is adapter-local and contains normalized descriptor handles plus structural
  state/digests; it is non-serializable/redacted.

## Behavior

### Packaged source

Load only manifest entries for logical skill root `skills/codex/yoetz-core/`. Require exact
`SKILL.md`, named references, compatibility manifest and no extra/collision. Verify size/SHA-256,
UTF-8/LF/final newline, skill frontmatter/name/version/protocol/Codex tested set, link containment and
public boundary. Read via package resources; no cwd/root source/network fallback. Source files are
bounded and immutable for one adapter call.

### Target validation

Accept the explicit project-root handle from request and:

1. require an existing user-owned directory marked/recognized as the exact trusted project by the
   caller's trust flow; reject filesystem root/home-as-project and ambiguity;
2. open root using no-follow descriptor operations, reject symlink components, hard-linked managed
   files, group/other writable unsafe ancestors where policy requires, traversal/backslash/control/
   case collision, and changes during inspection;
3. derive only `.agents/skills/yoetz-core`; validate/create `.agents/skills` owner-safe only during
   confirmed install, never preview/status;
4. inspect expected files and marker under byte/count caps. Unexpected members cause `partial`/
   `modified`, not deletion.

Do not inspect repository contents, Git remote, task files, other skills, global Codex config, or a
parent directory to find a more convenient root. Target path never appears in repr/error/log/evidence.

### Classification and preview

`inspect_destination` returns:

- absent: directory does not exist;
- installed_exact: valid self-digested marker and exact expected file inventory/digests/source set;
- modified: marker absent/invalid or at least one existing expected/extra file differs;
- partial: only part of managed inventory exists or interrupted marker/staging is present;
- incompatible: exact/managed content targets unsupported skill/protocol/Codex set;
- unsafe: containment/ownership/link/TOCTOU rule fails.

Compute preview changes from expected relative paths and digests; never return existing bytes. A
bounded local diff, when requested by CLI, is generated in memory from UTF-8 expected/current files,
shown directly and discarded; binary/oversized/invalid text shows digest-only. It is never logged or
written to a temp file.

Preview digest uses canonical fields: action, fixed scope, opaque target file identity commitment
(device/inode/root nonce, not public path), source resource-set/files, current marker/files, replace
flag, compatibility and warnings. It is recomputed immediately before apply.

### Marker

`.yoetz-install.json` is canonical JSON schema `yoetz.codex-skill-install/1`, containing adapter/
skill/protocol/resource-set versions, scope `trusted_project`, sorted managed file path/size/SHA-256,
and self-digest. It has no installation timestamp, absolute/relative project root, user/host, Git
identity, Codex config, task/session, secret, or environment. It marks installation ownership but is
not authentication.

### Atomic install and replacement

Before write, verify explicit confirmation and exact preview/current descriptors. Create stage under
`.agents/skills/.yoetz-core.stage-<request-id>` with owner-only mode and no preexistence. Write each
source via exclusive no-follow temp descriptor, fsync, verify digest, then marker last; fsync stage.

If destination absent, rename stage to `yoetz-core`, fsync parent and re-inspect exact. If replacing
a previewed modified/partial destination, create a durable swap marker containing only request and
old/new digest sets, rename current to `.yoetz-core.rollback-<request-id>`, fsync, rename stage into
place, fsync, reverify. Only then remove rollback and marker using descriptor-safe exact paths. Never
merge/per-file overwrite a modified directory.

Crash recovery validates stage/current/rollback/marker:

- one complete exact new plus matching marker → finish cleanup;
- old complete matching preview and no active new → restore old;
- both complete with matching structural swap marker → choose based on recorded committed phase;
- any changed/unexpected/ambiguous bytes → stop `partial|modified`, preserve all and require status/
  new preview/manual action.

### Status and removal

Status only runs packaged-source and destination inspection. It opens no write descriptor.

Removal requires current valid marker and exact inventory/digests equal confirmed preview. Rename the
whole directory to `.yoetz-core.remove-<request-id>`, fsync parent, verify moved inventory, delete
only those marker-listed exact files/directories, fsync, and return absent. Any extra/changed/missing/
unsafe file aborts before rename. After a crash, an exact removal staging directory and marker may be
completed; ambiguity preserves it. Never force-delete modified/unmanaged content.

## Errors and edge cases

- Resource mismatch, target/destination change, symlink/hardlink, unsafe permissions, disk full,
  failed fsync/rename, concurrent editor, case collision, invalid marker or size cap maps to closed
  integration reasons.
- Existing `.agents` may be user-owned; adapter creates only missing `.agents/skills` components after
  confirmation and never chmods/replaces an existing unsafe directory.
- `installed_exact` source version change produces previewed replacement, not automatic update.
- A directory manually matching source but without valid marker is preserved as unmanaged modified.
- No modified bytes/path/raw OS error enter diagnostics; bounded relative managed filenames are
  public constants.

## Invariants

1. Writes occur only inside the exact project skill destination and adapter-owned siblings.
2. Installed skill/reference bytes equal verified package resources.
3. Exact preview + confirmation + unchanged descriptors precede every mutation.
4. Modified/unmanaged content is never silently overwritten or removed.
5. Crash leaves old/new complete or preserved ambiguity, never a falsely exact state.
6. Adapter performs no network, Git, Codex config, MCP registration, or package mutation.

## Tests

- `specs/tests/unit.md`: manifest/frontmatter/link/marker/preview digest and classification fixtures.
- `specs/tests/integration.md`: absent/exact/unmanaged/modified/partial/incompatible/unsafe, every
  write/rename/fsync kill point, concurrent modification and conservative recovery.
- `specs/tests/capability.md`: install into real isolated trusted Codex project, discovery, status,
  modified protection and removal.
- `specs/tests/packaging.md`: source/package/install parity and public-boundary scan.

## Open questions

None.

E-002 is the sole central Codex-capability gate.
