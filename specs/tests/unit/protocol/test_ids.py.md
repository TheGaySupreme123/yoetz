# tests/unit/protocol/test_ids.py — identifier contract matrix

**Wave:** A | **ADRs:** ADR-002, ADR-005, ADR-007 | **Imports (spec-tree):**
`src/yoetz/protocol/ids.py`, `src/yoetz/protocol/errors.md`
**Imported by:** the protocol unit suite

## Purpose

Lock the identifier registry as a pure shape contract. This test file proves that every public ID
kind uses the right prefix, the right UUID spelling, and the right rejection behavior for hostile
inputs.

## Public surface

- `test_new_id_kind_matrix` — every non-actor `IdKind` produces a correctly prefixed UUIDv4
  string; `IdKind.ACTOR` raises exactly `actor_id_not_generated`.
- `test_validate_id_rejects_bad_shapes` — wrong prefix, wrong length, nil UUID, upper-case hex,
  non-ASCII, and malformed UUIDs fail.
- `test_validate_id_is_kind_specific` — an ID valid for one kind does not validate for another.
- `test_safe_request_id_from_is_non_raising` — malformed request arguments do not throw and never
  echo raw input.
- `test_safe_request_id_requires_exact_builtin_str` — a `str` subclass, coercible object, raising
  mapping, and oversized value all return `None` without scanning/echoing hostile content.
- `test_direct_validation_accepts_str_subclasses_without_coercion` — ordinary generated and actor
  validators accept a valid `str` subclass as a string instance and return the identical object,
  while the hostile request extractor still rejects it.
- `test_actor_prefix_is_convention_only` — caller-asserted actor IDs are format-checked but not
  granted server assurance.
- `test_wrong_kind_is_programmer_defect` — raw kind strings and foreign enums raise exactly
  `TypeError("id_kind_wrong_type")` from generation, validation, and boolean validation.
- `test_no_identifier_parse_surface` — the protocol module exports no `parse_id`, reverse-prefix
  lookup, or equivalent kind-from-ID accessor.
- `test_id_reasons_are_exact_central_registry_subset` — every owned reason is present before and
  after importing sibling protocol modules, with no runtime registration.

## Behavior

The suite exercises the full `IdKind` matrix and proves:

- all twenty-seven non-actor generated IDs are lowercase canonical RFC 4122 UUIDv4 strings with
  the expected prefix, while `new_id(IdKind.ACTOR)` raises the exact registered
  `actor_id_not_generated` reason;
- `validate_id` accepts only the target kind’s prefix and exact UUID spelling;
- direct `validate_id`/`validate_actor_id` accept real `str` subclasses by inspecting the actual
  runtime class, reject spoofed or raising `__class__`, use only bounded built-in string
  operations, and return the identical object without coercion; this is intentionally distinct
  from safe request extraction;
- the nil UUID, short/long forms, upper-case forms, and non-ASCII forms are rejected;
- `safe_request_id_from` returns either a valid request ID or `None` and never raises on hostile
  `Mapping` values, a spoofed/raising `__class__`, or lookup failure including `BaseException`; the
  duplicate-lookup case is a custom mapping whose `.get("request_id")` raises, because a Python
  `dict` cannot contain duplicate equal keys; only
  `type(candidate) is str` can pass, so hostile subclasses cannot override
  behavior on the public error path;
- a non-`IdKind` kind is an ordinary programmer defect: `new_id`, `validate_id`, and
  `is_valid_id` all raise exactly `TypeError("id_kind_wrong_type")`, which is not a protocol
  reason and is not swallowed by the boolean wrapper;
- all nine owned reasons are an exact subset of `PROTOCOL_REASON_CODES` independent of module
  import order, and actor format failure remains exactly `actor_id_malformed` (never remapped to
  `invalid_actor_id`);
- a validated identifier is opaque: no ordering, semantic meaning, or round-trip normalization
  beyond the registry shape is allowed. The CAN-008 fixture field `parsed_kind` is test-case
  metadata selecting the expected `IdKind`; it does not imply or authorize a runtime parser.

## Errors and edge cases

- The test fails if validation accepts one kind’s prefix for another kind.
- The test fails if a malformed ID is normalized instead of rejected.
- The test fails if safe extraction copies unbounded user text into an error path.
- The test fails if importing `ids.py` mutates a reason registry or changes a prior failure reason.
- The test fails if the module grows a reverse-prefix or `parse_id` public surface.

## Invariants

1. Prefix and kind stay aligned with `specs/INTERFACES.md`.
2. IDs remain opaque UUID-shaped strings.
3. Hostile input never becomes a different valid ID by coercion.

## Tests

- `tests/unit/protocol/test_ids.py`
- fixtures for valid/invalid ID strings in `specs/fixtures/`

## Open questions

None.
