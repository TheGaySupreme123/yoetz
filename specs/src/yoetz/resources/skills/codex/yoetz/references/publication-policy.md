# src/yoetz/resources/skills/codex/yoetz/references/publication-policy.md — installed publication-policy reference

**Wave:** D | **ADRs:** ADR-002, ADR-005 | **Imports (spec-tree):**
`specs/skills/codex/yoetz/references/publication-policy.md`,
`specs/src/yoetz/resources/manifest.json.md` | **Imported by:** installed skill, packaging
tests, capability tests

## Purpose

Define the installed byte-identical copy of the publication-policy reference shipped beside the
Yoetz skill. The file is a packaged resource, not a runtime-generated artifact.

## Public surface

- Logical resource: `skills/codex/yoetz/references/publication-policy.md`.
- Installed package path: `src/yoetz/resources/skills/codex/yoetz/references/publication-policy.md`.

## Behavior

The installed copy matches the reviewed root reference byte-for-byte. It teaches publication
materiality, the 16 event-family cheat sheet, obligation/evidence/claim relationships,
subject-state binding, batching/retry, multi-agent attribution, forbidden content, and the three
mini-flows exactly as reviewed at source, including bounded problem-local excerpt guidance.

The runtime does not synthesize or rewrite the reference. Manifest verification checks byte size
and SHA-256 before the skill is trusted.

## Errors and edge cases

- If the installed reference diverges from source, the package or startup check fails.
- A missing reference file or manifest mismatch blocks skill activation.
- The file is not allowed to pull live network examples or private transcript snippets.

## Invariants

1. The packaged copy is byte-identical to source.
2. The reference stays offline and reviewable.
3. The installed resource never expands the event registry.

## Tests

- `specs/tests/packaging.md`
- `specs/tests/capability.md`
- `specs/tests/conformance.md`

## Open questions

None.
