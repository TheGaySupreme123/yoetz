# tests/unit/adapters/test_codex_skill_integration.py — Codex skill source and unprofiled status

**Wave:** D | **ADRs:** ADR-005, ADR-007, ADR-010 | **Imports (spec-tree):**
`src/yoetz/adapters/integrations/codex_skill.py.md`,
`src/yoetz/ports/integrations.py.md`, `src/yoetz/resources/manifest.json.md` |
**Imported by:** the adapter unit suite

## Purpose

Lock the Codex layout/profile, manifest-bound resource loading, path-free managed marker, inert
package import, and honest unsupported/incompatible state while E-002 remains open.

## Public surface

- empty reviewed Codex support-profile assertions;
- injected manifest/resource verification and mutation rejection;
- managed-marker privacy assertions;
- read-only unprofiled status and install refusal;
- integration package import inertness.

## Behavior

A bounded in-memory resource source supplies canonical package and compatibility manifests plus the
six expected members. The adapter verifies size/digest/frontmatter and shared-member layout. Any
mutation fails closed without developer-tree fallback. Status against an owner-private temporary
project returns `incompatible`/`unsupported` and creates no `.agents` directory. Install is refused
with `version_incompatible` before filesystem mutation.

## Errors and edge cases

- Missing or changed package bytes are `source_invalid`.
- Empty support collections are explicit and jointly empty, not wildcard support.
- Marker bytes contain no project root or user path.

## Invariants

1. No Codex version is advertised without E-002 evidence.
2. Production resource lookup never falls back to the checkout.
3. Status and package import are side-effect free.
4. Skill/reference bytes remain manifest-bound.

## Tests

- `tests/unit/adapters/test_codex_skill_integration.py`

## Open questions

E-002 remains an empirical release gate; it is not resolved by this unit suite.
