# src/yoetz/application/__init__.py — side-effect-free application package marker

**Wave:** F | **ADRs:** ADR-001, ADR-007 | **Imports (spec-tree):** application module specs in
`application/` | **Imported by:** application tests and explicit submodule imports

## Purpose

Mark `yoetz.application` as the package boundary for the use-case facade and support modules.

## Public surface

- No reexports. Import application modules directly.
- `__all__` is absent or empty.

## Behavior

Importing the package must not build an application object, connect to storage, or choose a
provider profile. Runtime composition remains explicit and separate.

## Errors and edge cases

- Any import-time runtime bootstrap is forbidden.
- The marker must not create a default facade instance.

## Invariants

1. Import is inert.
2. Composition remains explicit.
3. The package exposes no convenience singleton.

## Tests

- `specs/tests/packaging/test_wheel_and_sdist_contents.py`
- `specs/tests/unit.md`

## Open questions

None.
