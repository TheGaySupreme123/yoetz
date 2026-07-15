# tests/property/test_privacy_properties.py — privacy/redaction property checks

**Wave:** F | **ADRs:** ADR-005, ADR-007 | **Imports (spec-tree):**
`src/yoetz/observability/privacy.md`
**Imported by:** property-based privacy tests

## Purpose

Search the privacy helpers for accidental leakage, instability, or reversible redaction.

## Public surface

- `test_session_hash_never_equals_plain_id` — hashes never equal raw values.
- `test_redaction_preserves_structure_but_removes_sensitive_text` — output shape remains usable.
- `test_canary_patterns_stay_testable` — synthetic canaries remain detectable.

## Behavior

The property suite varies sensitive strings and proves:

- hashed identifiers do not reveal plaintext;
- redaction keeps the structural shell but strips secrets;
- canary markers remain detectable for test assertions;
- helper output is deterministic.

## Errors and edge cases

- A redaction result that still contains the sensitive input fails.

## Invariants

1. Privacy helpers are deterministic.
2. Sensitive strings are removed, not disguised.
3. Hashing and redaction stay separable.

## Tests

- `tests/property/test_privacy_properties.py`

## Open questions

None.
