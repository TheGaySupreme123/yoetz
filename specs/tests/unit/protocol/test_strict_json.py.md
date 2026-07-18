# tests/unit/protocol/test_strict_json.py — raw JSON parser and canonicalizer fences

**Wave:** A | **ADRs:** ADR-002, ADR-005, ADR-007 | **Imports (spec-tree):**
`src/yoetz/protocol/canonical.py`, `tests/fixture_loader.py`, `fixtures/manifest.json`
**Imported by:** the protocol unit suite

## Purpose

Prove the raw-byte JSON parser rejects every non-contractual shape before model construction and
that canonical encoding stays byte-stable for accepted values.

## Public surface

- `test_parse_accepts_canonical_vectors` — canonical fixtures round-trip exactly.
- `test_parse_snapshots_bytearray_input` — mutable input is copied once before decode and parse.
- `test_parse_rejects_duplicate_keys_and_non_utf8` — duplicate names, invalid UTF-8, BOM, and NUL
  fail.
- `test_parse_rejects_float_and_negative_zero` — truth-bearing numbers must be safe integers only.
- `test_parse_rejects_lone_surrogates_and_overflow` — malformed Unicode and unsafe integers fail.
- `test_canonical_encode_is_stable` — object insertion order does not affect bytes and array order
  remains significant.
- `test_canonical_encode_rejects_unsupported_python_types` — non-JSON Python objects do not leak
  through coercion.

## Behavior

The suite locks the strict parser profile:

- fixture bytes come from root `fixtures/` through the manifest-bound `tests/fixture_loader.py`,
  never from the Markdown spec tree or an installed-resource fallback;
- `bytes` parse directly and `bytearray` is snapshotted once to immutable bytes before inspection;
- duplicate keys are rejected before schema/model validation;
- raw bytes must be valid UTF-8 and must not contain a BOM or NUL;
- numbers outside the safe integer range, floats, and `-0` are rejected;
- lone surrogate escapes and ill-formed strings fail closed;
- canonical encoding of accepted values is deterministic and idempotent;
- accepted values preserve Unicode normalization distinctions rather than silently folding them.

## Errors and edge cases

- A passing test must not rely on Python’s default JSON parser behavior.
- The test fails if equivalent object insertion orders produce different bytes or if array order is
  silently normalized.
- The exact multi-process environment matrix belongs to
  `tests/conformance/protocol/test_canonical_cross_process.py`; this unit module does not create a
  second matrix.

## Invariants

1. Parser rejection happens before model validation.
2. Canonical bytes are stable across platforms and interpreter settings.
3. No unsupported Python type is silently serialized.

## Tests

- `tests/unit/protocol/test_strict_json.py`

## Open questions

None.
