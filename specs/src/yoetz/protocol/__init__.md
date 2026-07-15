# src/yoetz/protocol/__init__.py — side-effect-free protocol package marker

**Wave:** F | **ADRs:** ADR-002, ADR-007 | **Imports (spec-tree):** protocol module specs in
`protocol/` | **Imported by:** protocol tests and explicit submodule imports

## Purpose

Mark `yoetz.protocol` as the package boundary for canonicalization, IDs, coverage, schemas,
and error handling.

## Public surface

- No reexports. Import protocol modules directly.
- `__all__` is absent or empty.

## Behavior

Importing the package must not perform validation, canonicalization, or schema generation. The
package exists only as an import boundary.

## Errors and edge cases

- Any import-time parsing or model construction is forbidden.
- The marker must not import protocol submodules eagerly.

## Invariants

1. Import is inert.
2. Protocol behavior stays in explicit modules.
3. No hidden canonicalization occurs at package import time.

## Tests

- `specs/tests/packaging/test_wheel_and_sdist_contents.py`
- `specs/tests/unit.md`

## Open questions

None.
