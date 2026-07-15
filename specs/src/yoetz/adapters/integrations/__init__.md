# src/yoetz/adapters/integrations/__init__.py — side-effect-free integration-adapter package marker

**Wave:** F | **ADRs:** ADR-007 | **Imports (spec-tree):** integration adapter module specs in
`adapters/integrations/` | **Imported by:** integration-adapter tests and explicit submodule
imports

## Purpose

Mark `yoetz.adapters.integrations` as the package boundary for integration-specific adapter
helpers.

## Public surface

- No reexports. Import integration modules directly.
- `__all__` is absent or empty.

## Behavior

Importing the package must not construct a Codex skill adapter or scan the filesystem for a
managed skill manifest. It exists only so the integration adapter module can be imported
explicitly.

## Errors and edge cases

- Any import-time adapter setup is forbidden.
- The marker must not create hidden defaults for skill handling.

## Invariants

1. Import is inert.
2. Integration behavior stays in the explicit module.
3. No hidden skill installation occurs at package import time.

## Tests

- `specs/tests/packaging/test_wheel_and_sdist_contents.py`
- `specs/tests/unit.md`

## Open questions

None.
