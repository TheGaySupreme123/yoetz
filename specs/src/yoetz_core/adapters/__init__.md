# src/yoetz_core/adapters/__init__.py — side-effect-free adapter package marker

**Wave:** F | **ADRs:** ADR-007 | **Imports (spec-tree):** adapter subpackage/module specs in
`adapters/` | **Imported by:** package import and packaging tests

## Purpose

Mark `yoetz_core.adapters` as a regular package boundary without turning package import into an
adapter bootstrap hook.

## Public surface

- No reexports. Import concrete adapter modules or subpackages directly.
- `__all__` is absent or empty.

## Behavior

Importing the package performs no network, filesystem, key-store, SQLite, or provider work. It
must not eagerly import sibling submodules or instantiate runtime state. The file may contain only
a docstring and marker-level constants.

## Errors and edge cases

- An eager import that drags in optional dependencies or runtime initialization is a contract
  failure.
- Any import-time side effect beyond establishing the package boundary is forbidden.

## Invariants

1. Package import is inert.
2. The boundary exists for explicit submodule imports only.
3. No convenience API is introduced by the marker.

## Tests

- `specs/tests/packaging/test_wheel_and_sdist_contents.py`
- `specs/tests/unit.md`

## Open questions

None.
