# src/yoetz/adapters/objects/__init__.py — side-effect-free object-store package marker

**Wave:** F | **ADRs:** ADR-007 | **Imports (spec-tree):** object-store module specs in
`adapters/objects/` | **Imported by:** object-store tests and explicit submodule imports

## Purpose

Mark `yoetz.adapters.objects` as the package boundary for object-store and envelope helpers.

## Public surface

- No reexports. Import object-store modules directly.
- `__all__` is absent or empty.

## Behavior

Importing the package must not create directories, write envelope files, inspect the filesystem
layout, or read package resources. It is only a package boundary.

## Errors and edge cases

- Any import-time object-store initialization is forbidden.
- The marker must not depend on a configured data directory.

## Invariants

1. Import is inert.
2. No storage side effects occur.
3. Explicit submodule imports own the behavior.

## Tests

- `specs/tests/packaging/test_wheel_and_sdist_contents.py`
- `specs/tests/unit.md`

## Open questions

None.
