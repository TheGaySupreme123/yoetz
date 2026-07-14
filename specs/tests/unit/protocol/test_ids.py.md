# tests/unit/protocol/test_ids.py — identifier contract matrix

**Wave:** A | **ADRs:** ADR-002, ADR-005, ADR-007 | **Imports (spec-tree):**
`src/yoetz_core/protocol/ids.py`, `src/yoetz_core/protocol/errors.md`
**Imported by:** the protocol unit suite

## Purpose

Lock the identifier registry as a pure shape contract. This test file proves that every public ID
kind uses the right prefix, the right UUID spelling, and the right rejection behavior for hostile
inputs.

## Public surface

- `test_new_id_kind_matrix` — every `IdKind` produces a correctly prefixed UUIDv4 string.
- `test_validate_id_rejects_bad_shapes` — wrong prefix, wrong length, nil UUID, upper-case hex,
  non-ASCII, and malformed UUIDs fail.
- `test_validate_id_is_kind_specific` — an ID valid for one kind does not validate for another.
- `test_safe_request_id_from_is_non_raising` — malformed request arguments do not throw and never
  echo raw input.
- `test_actor_prefix_is_convention_only` — caller-asserted actor IDs are format-checked but not
  granted server assurance.

## Behavior

The suite exercises the full `IdKind` matrix and proves:

- generated IDs are lowercase canonical RFC 4122 UUIDv4 strings with the expected prefix;
- `validate_id` accepts only the target kind’s prefix and exact UUID spelling;
- the nil UUID, short/long forms, upper-case forms, and non-ASCII forms are rejected;
- `safe_request_id_from` returns either a valid request ID or `None` and never raises on hostile
  dictionaries;
- a validated identifier is opaque: no ordering, semantic meaning, or round-trip normalization
  beyond the registry shape is allowed.

## Errors and edge cases

- The test fails if validation accepts one kind’s prefix for another kind.
- The test fails if a malformed ID is normalized instead of rejected.
- The test fails if safe extraction copies unbounded user text into an error path.

## Invariants

1. Prefix and kind stay aligned with `specs/INTERFACES.md`.
2. IDs remain opaque UUID-shaped strings.
3. Hostile input never becomes a different valid ID by coercion.

## Tests

- `tests/unit/protocol/test_ids.py`
- fixtures for valid/invalid ID strings in `specs/fixtures/`

## Open questions

None.
