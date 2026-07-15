# skills/codex/yoetz/references/publication-policy.md — Codex publication policy reference

**Wave:** D | **ADRs:** ADR-002, ADR-005 | **Imports (spec-tree):**
`specs/INTERFACES.md`, `specs/skills/codex/yoetz/SKILL.md` | **Imported by:** the installed
Yoetz skill and capability/packaging tests

## Purpose

Define the concise reference document that teaches when material work should be published into the
Yoetz ledger and when it should stay out of the record. This file is normative for the installed
skill only insofar as it restates the frozen public contract.

## Public surface

- `skills/codex/yoetz/references/publication-policy.md` — public markdown reference shipped
  beside the skill.

## Behavior

The reference contains:

- a materiality checklist with positive and negative examples;
- the 16 event-family cheat sheet, including when each family is appropriate and what it does not
  prove;
- obligation/evidence/claim relationships;
- subject-state binding and stale-evidence examples;
- event batching, writer-sequence, expected-frontier, and retry examples;
- multi-agent assignment and attribution rules;
- forbidden-content guidance;
- three worked mini-flows: code change, research task, and plan revision. The code-change flow shows
  a bounded state-bound changed hunk/enclosing symbol plus linked test/failure evidence, and
  contrasts it with forbidden repository-wide or unrelated source publication;

All examples use stable typed IDs, canonical integers, timestamp strings, and bounded payloads.
The document does not widen the public workflow contract and does not introduce new event families.

## Errors and edge cases

- If the event registry changes, the reference must be updated in lockstep or the skill build fails.
- Example payloads that contain secrets, real repository paths, or production identifiers are
  invalid.
- A reference that contradicts `INTERFACES.md` is a build failure, not a soft warning.

## Invariants

1. The reference teaches publication discipline, not hidden reasoning or full transcripts.
2. Examples remain small, concrete, and offline-verifiable.
3. The 16-family cheat sheet matches the frozen registry exactly.
4. Problem-local excerpts remain ordinary evidence in existing event families; they never create a
   source-browsing operation or imply independent observation.

## Tests

- `specs/tests/capability.md`
- `specs/tests/conformance.md`
- `specs/tests/packaging.md`

## Open questions

None.
