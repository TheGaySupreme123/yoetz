# src/yoetz_core/resources/skills/codex/yoetz-core/references/coverage-and-receipts.md — installed coverage-and-receipts reference

**Wave:** D | **ADRs:** ADR-002, ADR-005, ADR-007 | **Imports (spec-tree):**
`specs/skills/codex/yoetz-core/references/coverage-and-receipts.md`,
`specs/src/yoetz_core/resources/manifest.json.md` | **Imported by:** installed skill, packaging
tests, capability tests

## Purpose

Define the installed byte-identical copy of the coverage-and-receipts reference shipped beside the
Yoetz skill. The file is a packaged resource and must not be regenerated from local state.

## Public surface

- Logical resource: `skills/codex/yoetz-core/references/coverage-and-receipts.md`.
- Installed package path: `src/yoetz_core/resources/skills/codex/yoetz-core/references/coverage-and-receipts.md`.

## Behavior

The installed copy matches the reviewed root reference byte-for-byte. It teaches coverage
dimensions, conservative defaults, weakest-material dependency, deterministic-vs-semantic
provenance, freshness and redaction gaps, finding disposition, receipt field mapping, and the
bounded wording rules used at completion time.

The runtime verifies byte size and digest through the resource manifest before using the file.

## Errors and edge cases

- If the installed reference diverges from source, the package or startup check fails.
- A missing manifest entry or digest mismatch blocks skill activation.
- The file must not contain private identifiers, live transcript excerpts, or unreviewed wording
  changes.

## Invariants

1. The packaged copy is byte-identical to source.
2. Coverage language never overclaims the evidence.
3. The installed resource remains offline and deterministic.

## Tests

- `specs/tests/packaging.md`
- `specs/tests/capability.md`
- `specs/tests/conformance.md`

## Open questions

None.
