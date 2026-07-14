# src/yoetz_core/adapters/memory/__init__.py — side-effect-free in-memory adapter package marker

**Wave:** F | **ADRs:** ADR-007 | **Imports (spec-tree):** memory adapter module specs in
`adapters/memory/` | **Imported by:** memory-adapter tests and explicit submodule imports

## Purpose

Mark `yoetz_core.adapters.memory` as the package boundary for in-memory test/runtime adapters.

## Public surface

- No reexports. Import memory modules directly.
- `__all__` is absent or empty.

## Behavior

Importing the package must not allocate ledgers, catalogs, or object stores. It must not imply any
persistent storage support.

## Errors and edge cases

- Import-time creation of mutable shared state is forbidden.
- The marker must not silently substitute the SQLite adapter surface.

## Invariants

1. Import is inert.
2. The package is explicitly non-persistent.
3. No hidden singleton state is created.

## Tests

- `specs/tests/packaging/test_wheel_and_sdist_contents.py`
- `specs/tests/unit.md`

## Open questions

None.
