# src/yoetz/ports/__init__.py — side-effect-free ports package marker

**Wave:** F | **ADRs:** ADR-007 | **Imports (spec-tree):** port interface specs in `ports/` |
**Imported by:** port-interface tests and explicit submodule imports

## Purpose

Mark `yoetz.ports` as the package boundary for the abstract port interfaces used by the
application and adapters.

## Public surface

- No reexports. Import port modules directly.
- `__all__` is absent or empty.

## Behavior

Importing the package must not instantiate adapters or perform runtime probing. It is a namespace
boundary only.

## Errors and edge cases

- Any import-time adapter construction is forbidden.
- The marker must not imply that a backend is available.

## Invariants

1. Import is inert.
2. Port interfaces remain explicit.
3. No backend selection happens at package import time.

## Tests

- `specs/tests/packaging/test_wheel_and_sdist_contents.py`
- `specs/tests/unit.md`

## Open questions

None.
