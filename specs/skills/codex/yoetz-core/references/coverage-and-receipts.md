# skills/codex/yoetz-core/references/coverage-and-receipts.md — Codex coverage and receipt reference

**Wave:** D | **ADRs:** ADR-002, ADR-005, ADR-007 | **Imports (spec-tree):**
`specs/INTERFACES.md`, `specs/src/yoetz_core/domain/receipts.md`,
`specs/src/yoetz_core/protocol/coverage.md` | **Imported by:** the installed Yoetz skill and
capability/packaging tests

## Purpose

Define the reference document that teaches how coverage, findings, freshness, and receipt wording
work together at completion time. It gives the installed skill the exact conservative language
needed to avoid overstating evidence.

## Public surface

- `skills/codex/yoetz-core/references/coverage-and-receipts.md` — public markdown reference shipped
  beside the skill.

## Behavior

The reference contains:

- the six coverage dimensions, exact enum values, and default ordering;
- why coverage is a vector rather than a score;
- weakest-material-dependency examples;
- deterministic-vs-semantic origin and provenance rules;
- freshness, redaction, unknown-schema, and import-gap examples;
- finding disposition semantics;
- JSON receipt field mapping and derived markdown rules;
- approved and forbidden completion-wording examples.

The reference stays no stronger than the owning protocol. It uses wording such as “recorded claim”
and “digest recorded” where the evidence is self-asserted or partial, and it treats stale or
redacted material as a real limitation.

## Errors and edge cases

- If the coverage registry or receipt schema changes, the reference must change with it.
- A wording example that claims proof, verification, or completeness beyond the frozen coverage is
  invalid.
- Missing or corrupt reference bytes must fail installed-skill validation.

## Invariants

1. Coverage language never outruns the public contract.
2. Receipt wording is bounded by the weakest material dependency.
3. The reference remains usable offline.

## Tests

- `specs/tests/capability.md`
- `specs/tests/conformance.md`
- `specs/tests/packaging.md`

## Open questions

None.
