# tests/unit/domain/test_event_payloads.py — event payload validation and immutability

**Wave:** A/B | **ADRs:** ADR-002, ADR-003, ADR-005 | **Imports (spec-tree):**
`src/yoetz/domain/events.md`, `src/yoetz/domain/values.md`, `src/yoetz/protocol/models.md`
**Imported by:** the domain unit suite

## Purpose

Lock the family-by-family payload contracts for accepted events so each event shape stays exact and
immutable.

## Public surface

- `test_each_event_family_validates_required_and_optional_fields` — every family accepts the
  reviewed happy path and rejects one missing-required example.
- `test_event_payloads_are_frozen` — payloads do not mutate after construction.
- `test_boundary_model_conversion_preserves_exact_shape` — wire/boundary conversion stays lossless.
- `test_subject_state_and_reference_fields_remain_bounded` — evidence and state refs are exact.
- `test_generated_payload_encode_decode_is_byte_stable` — Hypothesis-generated valid family
  payloads round-trip to identical canonical bytes across hash-seed/locale/timezone controls.

## Behavior

The suite covers all event families in the registry and checks:

- required/optional field presence;
- family-specific enum/value validation;
- frozen dataclass behavior;
- exact conversion between boundary and domain payload shapes;
- one dedicated rejection per family invariant;
- generated payload encode/decode/re-encode identity for every family, including Unicode and
  boundary sizes, under multiple `PYTHONHASHSEED`, locale, and timezone controls.

## Errors and edge cases

- Unknown family names are not accepted as generic payloads.
- Payloads cannot mutate after validation.
- A generated strategy that omits a family or filters away boundary cases fails the suite.

## Invariants

1. Event payloads are family-specific and frozen.
2. No family silently widens its contract.
3. Boundary conversion preserves exact meaning.
4. Accepted payloads have one stable canonical byte representation independent of environment.

## Tests

- `tests/unit/domain/test_event_payloads.py`

## Open questions

None.
