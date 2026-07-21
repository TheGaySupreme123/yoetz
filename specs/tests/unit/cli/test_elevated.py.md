# tests/unit/cli/test_elevated.py — consent CLI driver vectors

**Wave:** D | **ADRs:** ADR-015, ADR-016 | **Imports (spec-tree):**
`src/yoetz/cli/elevated.md`, `src/yoetz/service/elevated_bootstrap.md` | **Imported by:**
`specs/tests/unit.md`

## Purpose

Freeze CLI driver behavior for catalog/status/prepare/approve over the elevated consent service.

## Public surface

Pytest module; no exports.

## Behavior

Assert catalog `implemented` flags, prepare vault-initialize projection with phrase placeholder,
refuse phrase-only prepare as `operation_not_implemented`, and leave pending cleared when approve
lacks required FDs after single-shot consume.

## Errors and edge cases

Uses isolated state directories and patched `state_dir`; no real service daemon or TTY.

## Invariants

1. Unimplemented catalog ops cannot be prepared through the CLI driver.
2. Approve never reads secret FDs before pending consent accepts.
3. Missing FDs after accept leave no reusable pending challenge.

## Tests

Self; indexed by `specs/tests/unit.md`.

## Open questions

None.
