# src/yoetz_core/py.typed — packaged typing marker

**Wave:** F | **ADRs:** ADR-007 | **Imports (spec-tree):** package metadata and packaging tests |
**Imported by:** wheel/sdist validation and installed-import checks

## Purpose

Define the zero-byte typing marker shipped beside the `yoetz_core` package so installed type
checkers can recognize the distribution as typed.

## Public surface

- `py.typed` — zero-byte marker file with no runtime exports.

## Behavior

The file is copied byte-for-byte into the wheel and installed tree. It has no runtime behavior and
must not be rewritten by packaging or import code.

## Errors and edge cases

- Missing, non-empty, or misplaced marker bytes fail packaging parity.
- The marker must not become an importable module.

## Invariants

1. The marker stays zero-byte.
2. It is present in source, wheel, and installed parity checks.
3. It has no runtime side effects.

## Tests

- `specs/tests/packaging/test_wheel_and_sdist_contents.py`
- `specs/tests/packaging/test_resource_byte_parity.py`

## Open questions

None.
