# src/yoetz_core/resources/skills/codex/yoetz-core/manifest.json — installed skill compatibility manifest copy

**Wave:** D | **ADRs:** ADR-002, ADR-005, ADR-007 | **Imports (spec-tree):**
`specs/skills/codex/yoetz-core/manifest.json.md`,
`specs/src/yoetz_core/resources/manifest.json.md` | **Imported by:** package startup, packaging,
and capability validation

## Purpose

Define the installed byte-identical copy of the Yoetz skill compatibility manifest. It binds the
installed skill to the reviewed reference files and capability profile IDs without exposing local
paths.

## Public surface

- Logical resource: `skills/codex/yoetz-core/manifest.json`.
- Installed package path: `src/yoetz_core/resources/skills/codex/yoetz-core/manifest.json`.
- Canonical JSON shape mirroring the reviewed source manifest with managed member names, sizes,
  SHA-256 values, capability-profile IDs, and a self-digest.

## Behavior

The build copies the reviewed manifest byte-for-byte into the package resource tree. Startup and
installation checks verify the managed-member list, member digests, and supported Codex
capability-profile IDs before the skill is trusted.

The installed manifest must not enumerate absolute checkout paths, home paths, or environment
state. It only records logical member names and reviewed digests.

## Errors and edge cases

- Any mismatch between source and packaged bytes fails packaging or startup verification.
- Missing or extra managed members fail closed.
- A noncanonical manifest or wrong self-digest is invalid.

## Invariants

1. Source and installed manifests are byte-identical.
2. Managed members are explicit and path-agnostic.
3. Capability-profile IDs stay frozen with the reviewed skill.

## Tests

- `specs/tests/packaging/test_wheel_and_sdist_contents.py`
- `specs/tests/packaging/test_resource_byte_parity.py`
- `specs/tests/capability/test_codex_skill_discovery.py`

## Open questions

None.
