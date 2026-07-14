# docs/protocol/compatibility.md — version axes and compatibility policy

**Wave:** A/F | **ADRs:** ADR-002, ADR-003, ADR-005, ADR-006, ADR-007 | **Imports (spec-tree):**
version, schema, migration, resource, skill and capability specs | **Imported by:** protocol guide,
release notes, clients, migration and Codex runbooks

## Purpose

Specify exactly what “compatible” means across Yoetz package/protocol/events/storage/objects/
projections/policies/resources/skills/transports/import formats and external tools. It prevents one
semantic-version range or successful import from becoming a blanket claim.

## Public surface

Stable future-document sections:

1. Compatibility is multi-dimensional
2. Version identity table
3. Reader/writer compatibility rules
4. Event/schema evolution
5. Storage/migration/rollback
6. Object/key/recovery formats
7. Projection/policy/receipt changes
8. CLI/MCP/skill/client compatibility
9. Codex/importer/provider/platform tested sets
10. Deprecation and breaking-change process
11. How to diagnose a mismatch
12. Current support manifest link

The document includes a normative matrix template populated from release facts, never a manually
guessed “latest” table.

## Behavior

### Independent axes

Define and show discovery source for:

- package/commit/release provenance;
- public protocol and operation schema versions;
- each durable event schema name/version;
- canonical encoding/digest domain version;
- storage schema and ordered migration set;
- object envelope/encryption/key/recovery artifact formats;
- projection engine/generation and deterministic policy/config digest;
- receipt schema/render version;
- installed resource-set/skill/reference compatibility;
- CLI contract/MCP SDK/protocol;
- Codex exact tested executable set and JSONL importer format profiles;
- optional provider adapter/profile/model/SDK;
- Python/OS/CPU/ABI/APSW/SQLite source/options/filesystem/key backend.

State explicitly that compatibility on one axis does not imply another. `version --json`, installed
resource manifest, bundle metadata and release capability/support matrices are the authorities.

### Version rule vocabulary

Use four states: `supported` (complete required evidence), `readable` (safe read/inspect but no write
claim), `unsupported` (known denied/failing), and `untested` (no claim). Never collapse untested into
unsupported or supported. “Available/installed” is not a compatibility state.

For package/protocol SemVer, document that major changes may break readers/writers; minor changes may
add backward-compatible optional capabilities only when old readers can preserve/ignore safely;
patch cannot change canonical bytes/schema semantics. Actual release behavior is governed by exact
schema/version manifests, not SemVer optimism.

### Reader/writer matrix

Define conservative rules:

- A client sends only operation schema versions the server advertises and strictly parses the exact
  selected result schema. Unknown required field/variant is not ignored.
- Server may read retained older event versions only when a registered decoder/migration fixture
  exists. Unknown event schema is preserved as canonical accepted data and surfaced as a coverage
  gap; it is never mapped to a known meaning.
- Writers never append under an unsupported protocol/storage/object/canonical version.
- Newer unknown storage schema allows at most tested structural read-only/version guidance; all
  writes/migrations fail closed.
- Old package reading a bundle changed by a newer package is supported only when release matrix says
  so; installing an old wheel is not rollback.

Include matrix rows `older client/current server`, `current client/older server`, `current package/
older bundle`, `older package/current bundle`, `unknown event`, `unknown object`, and exact outcome.

### Event/schema evolution

Event family/version is immutable after release. Fixing semantics requires new version; canonical
bytes/digests stay readable. New optional field is permitted only with explicit default that does not
alter old canonical bytes and schema version policy; otherwise new schema version. New event family
requires registry/schema/reducer/unknown-gap/fixture/skill/docs release update.

Retain golden vectors for every released version. Reducers/checks/receipts name engine/policy and
must not reinterpret old bytes silently. Unknown/redacted data weakens coverage and receipts.

### Storage, object and recovery

Storage migrates forward through contiguous immutable numbered resources, exclusive generation and
verified backup. Migration never rewrites canonical events. No automatic downgrade/reverse SQL.
Rollback means restore a verified pre-migration backup into a new quarantined target, replay, then
atomic catalog switch; current/old package compatibility must be separately proven.

Unknown object/recovery/key format or key-slot mismatch fails payload access/write; no plaintext/
empty fallback. Machine-bound versus portable backup compatibility is explicit and clean-profile
drill-backed. A portable format version is not proof a secret/key is available.

Projection caches may be discarded/rebuilt when a registered projection version supports all events;
projection compatibility never substitutes for ledger compatibility. Policy/receipt wording changes
are versioned and old receipt JSON remains addressable.

### External integrations

MCP compatibility names exact SDK/protocol pairs tested against installed artifacts. Codex support is
an explicit tested set per platform; no intermediate/newer inference. Skill compatibility combines
skill schema, Yoetz protocol range and exact-tested Codex evidence; installation alone is not support.
Codex JSONL importer profiles are exact format observations; unknown lines gap/quarantine.

Provider compatibility names exact adapter/SDK/endpoint/model profile and is optional/advisory. Base
strict-local support is independent. Platform support is exact artifact/runtime/SQLite/filesystem/
key-backend cell, not generic “Python/macOS/Linux.”

### Change and deprecation process

Every change class lists required schema/ADR/fixture/conformance/migration/capability/docs/release-note
updates. Breaking change requires new version axis, upgrade/read strategy, retained old fixture and
known limitation. Deprecations state first deprecated, last supported and removal target versions;
alpha does not promise a duration absent published policy.

Mismatch diagnosis procedure: capture `version --json`, error reason, bundle/manifest versions and
public digests only; never upload bundle/database/object/key/config/path/transcript. Compare against
release manifest and choose upgrade, supported migration, compatible client/skill, or restore—never
force/edit metadata.

## Errors and edge cases

- Current exact version cells are generated/linked from release evidence; stale hard-coded numbers
  in prose fail documentation tests.
- “Backward compatible” must state axis/direction/read-vs-write and evidence.
- Unknown version behavior is fail-preserve/gap, never silently ignore/drop.
- Prerelease external SDK/tool is not adopted into stable support without explicit release evidence.
- Examples use synthetic versions and cannot be mistaken for the current support matrix.

## Invariants

1. Every compatibility claim names axis, direction, operation and exact evidence set.
2. Untested is never inferred supported.
3. Canonical released history is preserved across readers/migrations.
4. Downgrade and restore are distinct; no reverse/in-place rollback is promised.
5. External tool/platform/provider support is exact-cell evidence.

## Tests

- Generate/compare axis names/current cells from `version --json`, schemas/resources and release
  support/capability matrices.
- Documentation lint rejects bare “compatible,” open-ended version ranges and “latest works.”
- Old/new fixture matrix confirms each documented read/write outcome.
- Migration, unknown-event/object, skill/Codex and provider negative cells link to executable tests.
- Public-boundary/link checks run before publication.

## Open questions

None.
