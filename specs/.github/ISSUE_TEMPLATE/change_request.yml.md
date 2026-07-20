# .github/ISSUE_TEMPLATE/change_request.yml — structured design/feature intake

**Wave:** F | **ADRs:** ADR-005, ADR-007 | **Imports (spec-tree):**
`repository/CONTRIBUTING.md`, `OPEN_QUESTIONS.md`, `.github/ISSUE_TEMPLATE/config.yml.md`
**Imported by:** GitHub issue creation UI

## Purpose

Collect a clear problem statement, proposed approach, scope, and design-gate status before any
behavioral PR is opened.

## Public surface

Future path: `.github/ISSUE_TEMPLATE/change_request.yml`.

GitHub issue form with required fields for:

- confirmation that existing issues/PRs were searched for duplicates;
- problem statement (what is wrong or missing for users/contributors);
- proposed approach at the smallest useful scope;
- owning `specs/` path if known (or “unknown — needs triage”);
- whether the change is design-gated (protocol, privacy/egress, storage/durability, release/
  packaging, ADR / `OPEN_QUESTIONS`);
- acceptance criteria for “done.”

Optional fields may capture alternatives considered and non-goals.

## Behavior

Default labels may include `enhancement` and/or `needs-triage`. Body copy must state that
design-gated work requires maintainer acknowledgement on the issue before a PR is opened, per
`CONTRIBUTING.md`.

## Errors and edge cases

- Omitting design-gate self-identification allows premature PRs into sensitive areas.
- Forms that encourage pasting secrets are unacceptable.

## Invariants

1. Duplicate search is a required confirmation.
2. Problem, approach, and acceptance criteria are required.
3. Design-gate status is explicitly captured.

## Tests

- `tests/packaging.md` — intake template presence when asserted.

## Open questions

None.
