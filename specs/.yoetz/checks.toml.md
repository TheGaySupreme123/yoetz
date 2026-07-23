# .yoetz/checks.toml — exact project approved-check policy

**Wave:** D | **ADRs:** ADR-009, ADR-010 | **Imports (spec-tree):**
`specs/src/yoetz/application/observation_check_policy.md` |
**Imported by:** local setup/check CLI and ready observation verification

## Purpose

Propose the fixed, reviewable project checks that may run after a trusted-local exact-digest
confirmation. Repository content is never self-activating authority.

## Public surface

Schema `yoetz.approved-check-policy/1`; sorted `[[checks]]` entries with exact `id`, `argv`,
`timeout_seconds`, and `network`.

## Behavior

The raw file bytes are SHA-256 bound. Only the identical confirmed digest is executable. Commands
run as exact argv with `shell=False`; no interpolation, free-form shell, or inherited environment.

## Errors and edge cases

Unknown keys, duplicate IDs, unsafe argv, symlinks, oversize bytes, or a changed digest are
untrusted and execute nothing. Network entries remain unsupported absent separate authority.

## Invariants

1. The file contains no credential or workspace-specific absolute path.
2. Editing any byte suspends all entries.
3. Trust state is outside the repository and workspace-scoped.

## Tests

Policy-parser, setup, observe-CLI, sandbox, and verification-worker tests.

## Open questions

None.
