# src/yoetz/adapters/sqlite/__init__.py — side-effect-free SQLite adapter package marker

**Wave:** F | **ADRs:** ADR-001, ADR-003, ADR-007 | **Imports (spec-tree):** SQLite adapter module
specs in `adapters/sqlite/` | **Imported by:** SQLite-adapter tests and explicit submodule imports

## Purpose

Mark `yoetz.adapters.sqlite` as the package boundary for durable storage and recovery modules.

## Public surface

- No reexports. Import SQLite modules directly.
- `__all__` is absent or empty.

## Behavior

Importing the package must not open SQLite connections, inspect runtime PRAGMAs, create files, or
touch migration state. It exists only to make the submodules importable.

## Errors and edge cases

- Import-time connection setup is forbidden.
- The marker must not implicitly choose a writable database path.

## Invariants

1. Import is inert.
2. SQLite state is never created at package import time.
3. Explicit submodule imports own the behavior.

## Tests

- `specs/tests/packaging/test_wheel_and_sdist_contents.py`
- `specs/tests/unit.md`

## Open questions

None.
