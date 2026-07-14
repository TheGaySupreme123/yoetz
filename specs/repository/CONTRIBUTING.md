# CONTRIBUTING.md — contributor workflow and review expectations

**Wave:** F | **ADRs:** ADR-005, ADR-007 | **Imports (spec-tree):** `specs/README.md`,
`tests/unit.md`, `tests/conformance.md`, `tests/packaging.md`
**Imported by:** new contributors, maintainers, and release reviewers

## Purpose

This file tells contributors how to make changes that fit the project’s contract and how to get
those changes reviewed. It should reduce guesswork without becoming a second copy of the specs
tree.

## Public surface

The document must cover:

- local setup expectations;
- how to find the authoritative specs;
- branch and patch submission expectations;
- test commands or test families to run before asking for review;
- when to update specs instead of coding around ambiguity;
- how release-related changes are reviewed.

## Behavior

The contributor guide should direct people toward the spec tree first. It should explain that the
implementation must follow the file-level specs and interface registry, and that changes to public
behavior need a spec update before or alongside code.

It should tell contributors to:

- make the smallest change that satisfies the spec;
- run the relevant tests for the touched area;
- avoid mixing unrelated behavioral changes in one patch;
- keep release and security-sensitive changes clearly labeled;
- preserve the public/private boundary and avoid adding private material to release files.

The guide may describe a review flow:

- propose the change;
- update the relevant spec if behavior changes;
- run the relevant test slice;
- hand off for review when the behavior and docs agree.

v0.1 uses this lightweight spec-first pull-request flow without a mandatory pull-request template.
Commands name only tools frozen by the release locks; contributor prose does not create a second
formatter, linter, type-checker, or review policy.

## Errors and edge cases

- A contribution that changes behavior without touching the authoritative spec is incomplete.
- A contribution guide that implies unreviewed shortcuts for release artifacts is misleading.
- The guide must not encourage editing generated or frozen artifacts by hand.

## Invariants

1. Specs govern behavior; the contribution guide only helps you use them.
2. Review expectations are clear before work starts.
3. Release-critical changes get the same rigor as code changes.
4. Contributors are pointed to the right test families, not a vague “run everything” demand.

## Tests

- `tests/unit.md` — touched file families and behavioral expectations.
- `tests/conformance.md` — when a change crosses runtime/storage boundaries.
- `tests/packaging.md` — when the change affects release artifacts or public metadata.

## Open questions

None.

F-005 is the sole central development type-checker gate.
