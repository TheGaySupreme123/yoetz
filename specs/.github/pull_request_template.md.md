# .github/pull_request_template.md — mandatory PR checklist

**Wave:** F | **ADRs:** ADR-005, ADR-007 | **Imports (spec-tree):**
`repository/CONTRIBUTING.md`, `repository/AGENTS.md`
**Imported by:** every pull request opened against this repository

## Purpose

Force every pull request to surface issue linkage, duplicate search / design-gate status, spec
updates, verification evidence, public/private boundary confirmation, and a commitment to
disposition review comments.

## Public surface

Future path: `.github/pull_request_template.md`.

The template must require contributors to fill:

- linked issue (`Fixes #N` or `Refs #N`);
- confirmation that issues/PRs were searched for duplicates;
- for design-gated work: confirmation of maintainer acknowledgement on the issue;
- owning `specs/` paths updated when behavior changes (or explicit “no behavior change”);
- verification commands run (paste);
- public/private boundary check (`docs/architecture/` and ignored drafting inputs not leaked);
- commitment that every human and code-review-agent comment will be fixed or answered before merge.

## Behavior

The template is mandatory for v0.1. Tone is firm and clear, not hostile. It must not claim the
project rejects all contributions. Incomplete checklists may cause maintainers to request changes
or close/convert the PR per `CONTRIBUTING.md`.

## Errors and edge cases

- A template that omits issue linkage or review-comment disposition is incomplete.
- A template that requires secrets or private drafting paths in the PR body is unacceptable.

## Invariants

1. Every PR is prompted to link a prior issue.
2. Verification evidence is requested in the PR body.
3. Review-comment disposition is an explicit merge expectation.

## Tests

- `tests/packaging.md` — presence of intake templates when packaging asserts governance files.

## Open questions

None.
