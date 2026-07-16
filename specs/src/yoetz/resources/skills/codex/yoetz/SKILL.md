# src/yoetz/resources/skills/codex/yoetz/SKILL.md — installed Codex skill header

**Wave:** D | **ADRs:** ADR-002, ADR-005, ADR-007, ADR-010 | **Imports (spec-tree):**
`specs/skills/codex/yoetz/SKILL.md`, `specs/guidance/README.md`,
`specs/src/yoetz/resources/manifest.json.md` | **Imported by:** package startup/version,
skill installation, capability and packaging tests

## Purpose

Define the exact installed copy of the Codex skill header. The wheel-installed file must be
byte-identical to the reviewed source skill and must not drift into a locally edited variant.

This resource is the Codex-shaped part only: frontmatter, activation, layout, and links. The
workflow, publication policy, and coverage rules it links to are the shared `guidance/` resources,
packaged once and installed beside it (ADR-010). This file is not their packaged copy and must not
duplicate them.

## Public surface

- `skills/codex/yoetz/SKILL.md` logical resource copy installed at
  `src/yoetz/resources/skills/codex/yoetz/SKILL.md`.
- Installed alongside the four shared guidance members from `src/yoetz/resources/guidance/`, which
  the Codex adapter places under the skill's `references/` directory.

## Behavior

The packaged file is copied from the reviewed skill source without semantic rewriting. It retains
the same frontmatter, activation guidance, Codex tool/command compatibility, installed layout, and
links into the shared guidance as the source skill.

Its content is verified to contain no restatement of a rule owned by `guidance/`: a workflow step,
publication rule, or coverage wording appearing here rather than in its owner is drift and fails the
build.

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
