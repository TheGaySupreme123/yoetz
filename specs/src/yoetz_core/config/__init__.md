# src/yoetz_core/config/__init__.py — side-effect-free config package marker

**Wave:** F | **ADRs:** ADR-007 | **Imports (spec-tree):** config module specs in `config/` |
**Imported by:** config tests and explicit submodule imports

## Purpose

Mark `yoetz_core.config` as the package boundary for configuration models, load rules, and paths.

## Public surface

- No reexports. Import config modules directly.
- `__all__` is absent or empty.

## Behavior

Importing the package must not read user config files, environment variables, or platform paths.
It exists only to make the submodules importable.

## Errors and edge cases

- Any import-time config loading is forbidden.
- The marker must not fabricate default configuration values.

## Invariants

1. Import is inert.
2. Configuration is not loaded implicitly.
3. Explicit submodule imports own the behavior.

## Tests

- `specs/tests/packaging/test_wheel_and_sdist_contents.py`
- `specs/tests/unit.md`

## Open questions

None.
