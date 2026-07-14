# src/yoetz_core/adapters/keys/__init__.py — side-effect-free key-backend package marker

**Wave:** F | **ADRs:** ADR-007 | **Imports (spec-tree):** key-backend module specs in
`adapters/keys/` | **Imported by:** key-backend tests and explicit submodule imports

## Purpose

Mark `yoetz_core.adapters.keys` as the package boundary for backend-specific key handling.

## Public surface

- No reexports. Import backend modules directly.
- `__all__` is absent or empty.

## Behavior

Importing the package must not open the key store, prompt for secrets, or choose a backend
implicitly. Backend selection stays in the composition layer.

## Errors and edge cases

- Any import-time secret access is forbidden.
- The marker must not fall back to a default backend on import.

## Invariants

1. Import is inert.
2. Backend choice remains explicit.
3. No secret material is touched at package import time.

## Tests

- `specs/tests/packaging/test_wheel_and_sdist_contents.py`
- `specs/tests/unit.md`

## Open questions

None.
