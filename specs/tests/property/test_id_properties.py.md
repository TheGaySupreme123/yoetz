# tests/property/test_id_properties.py — identifier algebra and hostile input properties

**Wave:** A/B | **ADRs:** ADR-002, ADR-005 | **Imports (spec-tree):**
`tests/property/strategies/identifiers.py`, `src/yoetz_core/protocol/ids.py`
**Imported by:** property-based identifier tests

## Purpose

Exercise the registry with valid IDs and one-defect mutations so the prefix/shape contract stays
opaque and kind-specific.

## Public surface

- `test_valid_ids_round_trip_by_kind` — generated valid IDs survive validation.
- `test_single_defect_mutations_fail` — each mutation class fails for the named reason.
- `test_safe_request_id_extraction_never_raises` — hostile dictionaries do not throw.

## Behavior

The property suite checks:

- every kind is covered;
- one-defect mutations fail in the expected way;
- request-id extraction stays bounded and non-raising;
- shrinking preserves the specific failure class.

## Errors and edge cases

- A valid ID accepted for the wrong kind fails the property.
- A hostile extraction path that raises fails the property.

## Invariants

1. ID kinds remain distinct.
2. Hostile request input never escapes the boundary.
3. Shrinks are still kind-specific.

## Tests

- `tests/property/test_id_properties.py`

## Open questions

None.
