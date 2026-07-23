# src/yoetz/adapters/integrations/codex_skill.py — trusted-project Codex skill file adapter

**Wave:** D | **ADRs:** ADR-005, ADR-007, ADR-010 | **Imports (spec-tree):**
`ports/integrations.py.md`, `config/paths.md`, `observability/privacy.md`,
`resources/manifest.json.md`, `guidance/README.md`, Codex skill specs | **Imported by:**
runtime/CLI integration composition, integration/capability/packaging tests

## Purpose

Implement the exact filesystem/resource mechanics of `IntegrationsPort` for the `codex` harness:
verify the packaged canonical bundle, classify one trusted-project destination, stage/swap only that
directory, write a nonsecret managed marker, and refuse to overwrite or remove modified user-owned
files.

This is the first harness adapter, not the definition of harness integration (ADR-010). Everything
Codex-specific lives here and in the reviewed `HarnessProfile` for `codex`: the `.agents/skills/
yoetz/` root, the Codex skill-header shape, and the Codex capability profiles. The guidance members
it installs are the harness-neutral `resources/guidance/` bytes and are not owned, authored, or
varied by this adapter. A second harness is a sibling adapter with its own profile; it copies the
same guidance and touches nothing here.

This adapter deliberately does not parse or edit Codex TOML/config, register MCP, invoke Codex, run
Git, stage/commit files, update `.gitignore`, download anything, or manage arbitrary destinations.

## Public surface

- `class CodexSkillIntegration(IntegrationsPort)` — implements the four port methods for
  `HarnessId.codex` and rejects any other harness value.
- `CODEX_HARNESS_PROFILE: HarnessProfile` — the reviewed profile: `skill_root=.agents/skills/
  yoetz/`, the Codex frontmatter profile, the exact Codex capability-profile IDs, tested Codex
  version bounds, and an exact `hooks_by_capability_profile` map. Each value is `None` unless that
  exact installed-artifact cell passes E-013; a passing value may declare a trigger arm and/or a
  nonempty closed `observation_events` set. Observation earns `hook_observed` only from real
  observation evidence via `ObservationPort`; trigger-only cells change no coverage.
- Until E-002 capability evidence exists, this constant has empty capability-profile IDs,
  supported versions, and hook map: an explicit unprofiled/unadvertised state, not inferred
  compatibility for either locally observed Codex version.
- `SkillResourceSource.read_bytes(package_path)` — bounded read-only package-resource injection
  seam for unit/packaging tests. Production uses package resources.
- `load_packaged_skill_source(resource_source=None) -> SkillSource` — verifies installed resource manifest before
  returning the exact inventory, composed of the Codex skill header plus the neutral guidance
  members.
- `load_packaged_skill_members(resource_source=None) -> Mapping[str, bytes]` — same verification,
  returning immutable member path→bytes for plugin bundling without duplicating SKILL contents.
- `inspect_destination(target, source) -> DestinationInspection` — descriptor-safe read-only state.
- `build_managed_marker(source, scope) -> bytes` — canonical `.yoetz-install.json`.
- `recover_interrupted_swap(target, expected_preview=None) -> DestinationInspection` — conservative
  structural recovery; never chooses between modified copies.
- `DestinationInspection` is adapter-local and contains normalized descriptor handles plus structural
  state/digests; it is non-serializable/redacted.

## Behavior

### Packaged source

Load only manifest entries for the Codex skill root `skills/codex/yoetz/` plus the neutral guidance
root `guidance/`. Require the exact Codex `SKILL.md`, the exact guidance members named by
`guidance/README.md`, the compatibility manifest, and no extra/collision. Verify size/SHA-256,
the resource-set digest using the generator's runtime-support exclusion rule,
UTF-8/LF/final newline, the Codex-readable skill frontmatter/name, compatibility-manifest
version/protocol/Codex tested set, link containment and public boundary. Unknown Yoetz-private
compatibility fields in `SKILL.md` frontmatter are invalid rather than treated as runtime metadata.
Read via package resources; no cwd/root source/network fallback. Source files are bounded and
immutable for one adapter call.

Before B9 lands the owned packaged manifest/skill/guidance files, production loading fails closed
with `source_invalid`; it never reads a developer checkout. Tests may inject a bounded,
manifest-bound `SkillResourceSource`. The explicit empty E-002 support profile classifies verified
source as `unsupported`/`incompatible`; status remains read-only and source-verifying, while install
is refused until reviewed exact support cells populate all three support collections.

Guidance members are copied byte-for-byte from `guidance/` into the destination layout Codex
expects; this adapter never rewrites, reflows, templates, merges, or per-harness edits their bytes,
so the same member installed by any harness is identical. Only the skill header is Codex-shaped.

The hook-profile map is support metadata, not an installer side effect. This adapter never edits
Codex hook/config files. A trigger-only value may be advertised only when the exact capability run
proves the reviewed host-native mechanism is already present and that its compaction event,
payload/privacy boundary, loop guard, coalescing, and failure behavior match the profile. Otherwise
the cell is `None` and the installed skill's manual resume/compaction instructions remain the path.

### Target validation

Accept the explicit project-root handle from request and:

1. require an existing user-owned directory marked/recognized as the exact trusted project by the
   caller's trust flow; reject filesystem root/home-as-project and ambiguity;
2. open root using no-follow descriptor operations, reject symlink components, hard-linked managed
   files, group/other writable unsafe ancestors where policy requires, traversal/backslash/control/
   case collision, and changes during inspection;
3. derive only `.agents/skills/yoetz`; validate/create `.agents/skills` owner-safe only during
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
`.agents/skills/.yoetz.stage-<request-id>` with owner-only mode and no preexistence. Write each
source via exclusive no-follow temp descriptor, fsync, verify digest, then marker last; fsync stage.

If destination absent, rename stage to `yoetz`, fsync parent and re-inspect exact. If replacing
a previewed modified/partial destination, create a durable swap marker containing only request and
old/new digest sets, rename current to `.yoetz.rollback-<request-id>`, fsync, rename stage into
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
whole directory to `.yoetz.remove-<request-id>`, fsync parent, verify moved inventory, delete
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
7. A trigger-only profile is selected only from exact E-013 evidence, performs re-grounding only,
   and cannot change coverage. An observation-capable profile requires E-013 observation evidence
   and nonempty closed `observation_events`; live ingest remains `ObservationPort`, not this
   filesystem adapter.

## Tests

- `specs/tests/unit/adapters/test_codex_skill_integration.py.md`: manifest/frontmatter/link/marker,
  explicit unprofiled compatibility, fail-closed source loading, status purity and classification.
- `specs/tests/integration.md`: absent/exact/unmanaged/modified/partial/incompatible/unsafe, every
  write/rename/fsync kill point, concurrent modification and conservative recovery.
- `specs/tests/capability.md`: install into real isolated trusted Codex project, discovery, status,
  modified protection and removal; exact trigger-only/unsupported cells follow their respective
  automatic/manual compaction-recovery paths with equal coverage.
- `specs/tests/packaging.md`: source/package/install parity and public-boundary scan.

## Open questions

None.

E-002 is the sole central Codex-capability gate.
