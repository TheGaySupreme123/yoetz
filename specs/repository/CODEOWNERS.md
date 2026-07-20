# CODEOWNERS — review ownership for trust boundaries

**Wave:** F | **ADRs:** ADR-005, ADR-007, ADR-009 | **Imports (spec-tree):**
`repository/CONTRIBUTING.md`, `repository/SECURITY.md`, `repository/PRIVACY.md`
**Imported by:** GitHub code-owner review assignment and maintainers

## Purpose

Declare which paths require review attention from the repository maintainer so trust-sensitive
surfaces cannot merge unnoticed. Ownership here is a review signal, not a substitute for CI or
specs. The file lives at the repository root (GitHub also accepts `.github/CODEOWNERS`; root
projection keeps the path under `repository_projection` with other extensionless root files).

## Public surface

Future path: `CODEOWNERS` (repository root).

The file must assign `@TheGaySupreme123` (or a successor team/handle maintained by the project) as
code owner for at least:

- `SECURITY.md`, `PRIVACY.md`, `CODE_OF_CONDUCT.md`;
- `docs/adr/`;
- `schemas/`;
- `migrations/`;
- `.github/workflows/`;
- `specs/INTERFACES.md`, `specs/OPEN_QUESTIONS.md`.

## Behavior

Use standard GitHub CODEOWNERS syntax. Comments may explain that ownership marks trust boundaries
for review, not exclusive authorship. Paths not listed remain open to ordinary review under
`CONTRIBUTING.md`.

When the maintainer handle changes, update this file and its projection together.

## Errors and edge cases

- Omitting privacy, security, ADR, schema, migration, or workflow paths weakens the review signal.
- Inventing non-existent teams or handles that GitHub cannot resolve is incomplete.

## Invariants

1. Trust-boundary paths have an explicit code owner.
2. The owner identity is a real GitHub user or team for this repository.
3. CODEOWNERS does not replace CI gates or the contribution process.

## Tests

- `tests/packaging.md` — when packaging checks assert presence of contributor/governance files.

## Open questions

None.
