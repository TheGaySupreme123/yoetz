# src/yoetz/cli/__init__.py — side-effect-free CLI package marker

**Wave:** F | **ADRs:** ADR-007 | **Imports (spec-tree):** CLI module specs in `cli/` |
**Imported by:** CLI tests and explicit submodule imports

## Purpose

Mark `yoetz.cli` as the package boundary for command-line entrypoints and rendering helpers.

## Public surface

- No reexports. Import CLI modules directly.
- `__all__` is absent or empty.

## Behavior

Importing the package must not parse argv, open the runtime, or render output. Command execution
stays in the explicit entrypoint modules.

## Errors and edge cases

- Any import-time side effect on stdout/stderr is forbidden.
- The marker must not imply a command has been executed.

## Invariants

1. Import is inert.
2. No argv is consumed at package import time.
3. CLI entrypoints remain explicit.

## Tests

- `specs/tests/packaging/test_wheel_and_sdist_contents.py`
- `specs/tests/unit.md`

## Open questions

None.
