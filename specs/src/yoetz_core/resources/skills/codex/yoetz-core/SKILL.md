# src/yoetz_core/resources/skills/codex/yoetz-core/SKILL.md — installed Codex cooperative-workflow skill

**Wave:** D | **ADRs:** ADR-002, ADR-005, ADR-007 | **Imports (spec-tree):**
`specs/skills/codex/yoetz-core/SKILL.md`, `specs/skills/codex/yoetz-core/references.md`,
`specs/src/yoetz_core/resources/manifest.json.md` | **Imported by:** package startup/version,
skill installation, capability and packaging tests

## Purpose

Define the exact installed copy of the public Yoetz skill. The wheel-installed file must be
byte-identical to the reviewed source skill and must not drift into a locally edited variant.

## Public surface

- `skills/codex/yoetz-core/SKILL.md` logical resource copy installed at
  `src/yoetz_core/resources/skills/codex/yoetz-core/SKILL.md`.
- Compatible with the two installed references under
  `src/yoetz_core/resources/skills/codex/yoetz-core/references/`.

## Behavior

The packaged file is copied from the reviewed skill source without semantic rewriting. It retains
the same sections, workflow steps, activation guidance, publication policy, handoff rules, resume
behavior, finding response rules, receipt-bounded final wording, and safety/privacy limitations as
the source skill.

The installer validates source and destination bytes, rejects traversal/symlink destinations, and
preserves any preexisting modified installed copy unless explicit overwrite consent is given. The
installed skill never depends on runtime network access or live transcript replay.

## Errors and edge cases

- A digest mismatch between source and packaged skill fails packaging or startup verification.
- Missing reference files or a missing manifest entry fail installed-skill validation.
- A local edit to the installed copy is preserved until the user explicitly approves replacement.

## Invariants

1. Source, packaged, and installed skill bytes are identical.
2. The installed file never widens the public contract.
3. No runtime process may regenerate the skill from hidden state.

## Tests

- `specs/tests/packaging.md`
- `specs/tests/capability.md`
- `specs/tests/conformance.md`

## Open questions

None.
