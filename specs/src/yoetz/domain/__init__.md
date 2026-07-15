# src/yoetz/domain/__init__.py — side-effect-free domain package marker

**Wave:** F | **ADRs:** ADR-007 | **Imports (spec-tree):** domain module specs in `domain/` |
**Imported by:** domain tests and explicit submodule imports

## Purpose

Mark `yoetz.domain` as the package boundary for values, events, findings, and receipts.

## Public surface

- No reexports. Import domain modules directly.
- `__all__` is absent or empty.

## Behavior

Importing the package must not instantiate domain objects or validate external input. It is a
boundary marker only.

## Errors and edge cases

- Any import-time construction of default domain state is forbidden.
- The marker must not hide payload-specific module imports.

## Invariants

1. Import is inert.
2. Domain modules remain explicit.
3. No hidden canonicalization happens at package import time.

## Tests

- `specs/tests/packaging/test_wheel_and_sdist_contents.py`
- `specs/tests/unit.md`

## Open questions

None.
