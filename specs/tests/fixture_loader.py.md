# tests/fixture_loader.py — read-only fixture corpus loader

**Wave:** D–F | **ADRs:** ADR-002, ADR-005, ADR-007 | **Imports (spec-tree):**
`specs/fixtures/README.md`, `specs/tests/conftest.py.md` | **Imported by:** all suites that need
reviewed fixture bytes

## Purpose

Provide the single approved way for tests to read reviewed fixture bytes and parsed fixture values.
The loader is read-only, manifest-bound, and deterministic.

## Public surface

- `FixtureLoader` — immutable loader bound to the reviewed fixture manifest.
- `build_fixture_loader() -> FixtureLoader` — construct the shared loader from the reviewed corpus.
- `load_fixture_bytes(path: str) -> bytes` — read exact bytes for a reviewed fixture.
- `load_fixture_json(path: str)` — parse a reviewed JSON fixture after byte validation.

## Behavior

The loader resolves only reviewed fixture paths declared in `fixtures/manifest.json`. It verifies
size and SHA-256 before returning bytes or parsed JSON. It never writes files, regenerates fixture
content, consults host paths, or falls back to alternate copies. Canonical JSON fixtures are
returned exactly as reviewed; non-JSON byte fixtures are returned as raw bytes only.

## Errors and edge cases

- Path traversal, missing manifest entry, digest mismatch, or unexpected file kind fails closed.
- The loader must not silently coerce invalid bytes into a different reviewed fixture.
- Any non-review corpus bytes are a test failure, not an alternate source of truth.

## Invariants

1. Fixture bytes are read-only and review-bound.
2. The loader has no write path.
3. No fixture path is discovered outside the manifest.

## Tests

- `specs/tests/unit.md`
- `specs/tests/property.md`
- `specs/tests/integration.md`
- `specs/tests/conformance.md`

## Open questions

None.
