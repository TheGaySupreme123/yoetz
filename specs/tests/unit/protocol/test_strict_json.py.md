# tests/unit/protocol/test_strict_json.py — raw JSON parser and canonicalizer fences

**Wave:** A | **ADRs:** ADR-002, ADR-005, ADR-007 | **Imports (spec-tree):**
`src/yoetz_core/protocol/canonical.py`, `src/yoetz_core/domain/values.md`
**Imported by:** the protocol unit suite

## Purpose

Prove the raw-byte JSON parser rejects every non-contractual shape before model construction and
that canonical encoding stays byte-stable for accepted values.

## Public surface

- `test_parse_accepts_canonical_vectors` — canonical fixtures round-trip exactly.
- `test_parse_rejects_duplicate_keys_and_non_utf8` — duplicate names, invalid UTF-8, BOM, and NUL
  fail.
- `test_parse_rejects_float_and_negative_zero` — truth-bearing numbers must be safe integers only.
- `test_parse_rejects_lone_surrogates_and_overflow` — malformed Unicode and unsafe integers fail.
- `test_canonical_encode_is_stable` — object order, locale, and hash seed do not affect bytes.
- `test_canonical_encode_rejects_unsupported_python_types` — non-JSON Python objects do not leak
  through coercion.

## Behavior

The suite locks the strict parser profile:

- duplicate keys are rejected before schema/model validation;
- raw bytes must be valid UTF-8 and must not contain a BOM or NUL;
- numbers outside the safe integer range, floats, and `-0` are rejected;
- lone surrogate escapes and ill-formed strings fail closed;
- canonical encoding of accepted values is deterministic and idempotent;
- accepted values preserve Unicode normalization distinctions rather than silently folding them.

## Errors and edge cases

- A passing test must not rely on Python’s default JSON parser behavior.
- The test fails if accepted bytes differ across hash seeds or locale/TZ variants.

## Invariants

1. Parser rejection happens before model validation.
2. Canonical bytes are stable across platforms and interpreter settings.
3. No unsupported Python type is silently serialized.

## Tests

- `tests/unit/protocol/test_strict_json.py`

## Open questions

None.
