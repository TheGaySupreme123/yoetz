# tests/property/test_id_properties.py — identifier algebra and hostile input properties

**Wave:** A/B | **ADRs:** ADR-002, ADR-005 | **Imports (spec-tree):**
`tests/property/strategies/identifiers.py`, `src/yoetz/protocol/ids.py`
**Imported by:** property-based identifier tests

## Purpose

Exercise the registry with valid IDs and one-defect mutations so the prefix/shape contract stays
opaque and kind-specific.

## Public surface

- `test_valid_ids_round_trip_by_kind` — generated valid IDs survive validation.
- `test_single_defect_mutations_fail` — each mutation class fails for the named reason.
- `test_safe_request_id_extraction_never_raises` — arbitrary objects and hostile mappings do not
  throw.
- `test_wrong_kind_programmer_defect_propagates` — arbitrary non-`IdKind` kinds raise exactly
  `TypeError("id_kind_wrong_type")`, including through `is_valid_id`.

## Behavior

The property suite checks:

- every kind is covered: non-actor generated values use canonical UUIDv4 spelling and actor values
  are validation-only caller assertions;
- one-defect mutations fail in the expected way;
- request-id extraction stays bounded and non-raising for arbitrary objects, ordinary mappings,
  and custom mappings whose `.get("request_id")` raises;
- the duplicate/ambiguous source-key property uses only that hostile custom `Mapping`; it never
  claims that a Python `dict` can retain duplicate equal keys or introduces a parser API;
- wrong-kind objects propagate the exact programmer-defect `TypeError` and are never converted to
  a protocol reason or `False`;
- shrinking preserves the specific failure class.

## Errors and edge cases

- A valid ID accepted for the wrong kind fails the property.
- A hostile extraction path that raises fails the property.
- A raw kind token treated as an identifier-value failure fails the property.

## Invariants

1. ID kinds remain distinct.
2. Hostile request input never escapes the boundary.
3. Shrinks are still kind-specific.

## Tests

- `tests/property/test_id_properties.py`

## Open questions

None.
