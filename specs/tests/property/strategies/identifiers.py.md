# tests/property/strategies/identifiers.py — generated identifier strategies

**Wave:** B | **ADRs:** ADR-001, ADR-002, ADR-005 | **Imports (spec-tree):**
`src/yoetz/protocol/ids.py`
**Imported by:** property-based identifier tests

## Purpose

Generate valid and single-defect identifier values for every registry kind.

## Public surface

- `strategy_valid_ids` — `SearchStrategy[tuple[IdKind, str]]` values grouped by kind; actor values
  are format-valid caller assertions and all other values use the canonical UUID shape.
- `strategy_invalid_ids` — `SearchStrategy[tuple[IdKind, object, str]]` values carrying one wrong
  prefix, wrong length, wrong case, nil UUID, non-ASCII, or actor-format defect and its exact
  expected protocol reason.
- `strategy_request_id_dicts` — the retained historical helper name for a
  `SearchStrategy[object]` of ordinary and hostile mapping-boundary inputs.

## Behavior

The strategy module must support:

- round-tripping valid IDs through validation;
- one-defect mutation of a valid ID per failure case;
- arbitrary non-mappings, ordinary mappings that omit or corrupt the request-ID field, and hostile
  custom `Mapping` values whose `.get("request_id")` raises;
- shrinking to the exact defect class the test names.

A Python `dict` cannot represent two equal `"request_id"` keys. The ambiguous/duplicate lookup
case is therefore represented only by the hostile custom `Mapping` above; the strategy does not
invent a duplicate-key parser or claim that a normal dict preserves duplicate keys. Wrong-kind
objects are likewise outside `strategy_invalid_ids`: a non-`IdKind` kind is a programmer-defect
`TypeError("id_kind_wrong_type")`, not a one-defect protocol reason.

## Errors and edge cases

- A strategy that conflates kinds is too weak.
- A strategy that generates identifiers outside the registry is wrong.
- A strategy that models duplicate dictionary keys or treats a raw kind string as protocol-invalid
  data is wrong.

## Invariants

1. Kind-specific prefix semantics are preserved.
2. Invalids are one mutation away from valids where practical.
3. Request-id extraction inputs stay hostile.

## Tests

- `tests/property/strategies/identifiers.py`

## Open questions

None.
