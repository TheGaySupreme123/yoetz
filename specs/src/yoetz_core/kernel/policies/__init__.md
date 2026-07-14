# src/yoetz_core/kernel/policies/__init__.py — side-effect-free policy-pack package marker

**Wave:** F | **ADRs:** ADR-007 | **Imports (spec-tree):** policy module specs in `kernel/policies/`
| **Imported by:** policy tests and explicit submodule imports

## Purpose

Mark `yoetz_core.kernel.policies` as the package boundary for the frozen policy packs.

## Public surface

- No reexports. Import policy modules directly.
- `__all__` is absent or empty.

## Behavior

Importing the package must not select a policy pack, evaluate claims, or run semantic logic. It is
only a package boundary for the policy modules.

## Errors and edge cases

- Any import-time policy evaluation is forbidden.
- The marker must not imply a default waiver or finding result.

## Invariants

1. Import is inert.
2. Policy packs remain explicit.
3. No hidden judgments happen at package import time.

## Tests

- `specs/tests/packaging/test_wheel_and_sdist_contents.py`
- `specs/tests/unit.md`

## Open questions

None.
