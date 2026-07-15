# src/yoetz/kernel/__init__.py — side-effect-free kernel package marker

**Wave:** F | **ADRs:** ADR-007 | **Imports (spec-tree):** kernel module specs in `kernel/` |
**Imported by:** kernel tests and explicit submodule imports

## Purpose

Mark `yoetz.kernel` as the package boundary for reducers, ranking, receipt building, and
deterministic policy checks.

## Public surface

- No reexports. Import kernel modules directly.
- `__all__` is absent or empty.

## Behavior

Importing the package must not run policy checks, build receipts, or replay events. The package is
only a namespace boundary.

## Errors and edge cases

- Any import-time derivation of state is forbidden.
- The marker must not import submodules eagerly.

## Invariants

1. Import is inert.
2. Kernel behavior stays in explicit modules.
3. No hidden evaluation happens at package import time.

## Tests

- `specs/tests/packaging/test_wheel_and_sdist_contents.py`
- `specs/tests/unit.md`

## Open questions

None.
