# .github/ISSUE_TEMPLATE/config.yml — issue form routing

**Wave:** F | **ADRs:** ADR-005, ADR-007 | **Imports (spec-tree):**
`repository/SECURITY.md`, `repository/CODE_OF_CONDUCT.md`, `repository/CONTRIBUTING.md`
**Imported by:** GitHub issue creation UI

## Purpose

Disable blank issues and route security and conduct reports away from the public issue tracker.

## Public surface

Future path: `.github/ISSUE_TEMPLATE/config.yml`.

Must:

- set `blank_issues_enabled: false`;
- provide contact links for security (pointing reporters at `SECURITY.md` / private vulnerability
  reporting) and conduct (pointing at `CODE_OF_CONDUCT.md` / `conduct@yoetz.dev`).

## Behavior

Ordinary bugs and change requests use the structured issue forms. Security and conduct must not be
filed as public issues. The config may include a short about/name/url per contact link suitable for
GitHub’s issue template schema.

## Errors and edge cases

- Enabling blank issues undermines duplicate search and structured intake.
- Routing security reports to a public issue URL is unacceptable.

## Invariants

1. Blank issues are disabled.
2. Security and conduct have explicit non-issue contact links.

## Tests

- `tests/packaging.md` — governance/intake file presence when asserted.

## Open questions

None.
