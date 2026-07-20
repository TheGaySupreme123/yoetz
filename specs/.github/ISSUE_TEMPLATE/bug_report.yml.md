# .github/ISSUE_TEMPLATE/bug_report.yml — structured bug intake

**Wave:** F | **ADRs:** ADR-005, ADR-007 | **Imports (spec-tree):**
`repository/CONTRIBUTING.md`, `.github/ISSUE_TEMPLATE/config.yml.md`
**Imported by:** GitHub issue creation UI

## Purpose

Collect enough structured information to triage bugs without duplicates or public secret leakage.

## Public surface

Future path: `.github/ISSUE_TEMPLATE/bug_report.yml`.

GitHub issue form (`name`, `description`, `title`, `labels`, `body`) with required fields for:

- confirmation that existing issues/PRs were searched for duplicates;
- area (for example protocol, CLI, MCP, privacy, storage, packaging, docs, other);
- reproduction steps;
- expected vs actual behavior;
- version information (`yoetz version --json` when available, else commit/version string);
- impact / severity as the reporter understands it.

Optional fields may capture OS, install method, and logs with an explicit warning not to paste
secrets or live customer data.

## Behavior

Default label may include `bug` and/or `needs-triage`. Body copy must remind reporters that
security vulnerabilities go through `SECURITY.md`, not this form.

## Errors and edge cases

- Forms that encourage pasting secrets or private drafting paths are unacceptable.
- Omitting the duplicate-search confirmation undermines intake rigor.

## Invariants

1. Duplicate search is a required confirmation.
2. Repro and expected/actual behavior are required.
3. Security reports are redirected away from this form.

## Tests

- `tests/packaging.md` — intake template presence when asserted.

## Open questions

None.
