# src/yoetz_core/observability/__init__.py — side-effect-free observability package marker

**Wave:** F | **ADRs:** ADR-007 | **Imports (spec-tree):** observability module specs in
`observability/` | **Imported by:** observability tests and explicit submodule imports

## Purpose

Mark `yoetz_core.observability` as the package boundary for logging and privacy controls.

## Public surface

- No reexports. Import observability modules directly.
- `__all__` is absent or empty.

## Behavior

Importing the package must not configure logging handlers, create files, or scan for secrets. It
is only a package marker.

## Errors and edge cases

- Any import-time log configuration is forbidden.
- The marker must not create privacy diagnostics eagerly.

## Invariants

1. Import is inert.
2. No handlers are installed at package import time.
3. Privacy controls remain explicit.

## Tests

- `specs/tests/packaging/test_wheel_and_sdist_contents.py`
- `specs/tests/unit.md`

## Open questions

None.
