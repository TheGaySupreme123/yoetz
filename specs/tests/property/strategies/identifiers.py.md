# tests/property/strategies/identifiers.py — generated identifier strategies

**Wave:** B | **ADRs:** ADR-001, ADR-002, ADR-005 | **Imports (spec-tree):**
`src/yoetz/protocol/ids.py`
**Imported by:** property-based identifier tests

## Purpose

Generate valid and single-defect identifier values for every registry kind.

## Public surface

- `strategy_valid_ids` — valid IDs grouped by kind.
- `strategy_invalid_ids` — wrong prefix, wrong length, wrong case, nil UUID, and non-ASCII forms.
- `strategy_request_id_dicts` — hostile dictionaries for safe request-id extraction.

## Behavior

The strategy module must support:

- round-tripping valid IDs through validation;
- one-defect mutation of a valid ID per failure case;
- hostile dicts that omit, duplicate, or corrupt the request-id field;
- shrinking to the exact defect class the test names.

## Errors and edge cases

- A strategy that conflates kinds is too weak.
- A strategy that generates identifiers outside the registry is wrong.

## Invariants

1. Kind-specific prefix semantics are preserved.
2. Invalids are one mutation away from valids where practical.
3. Request-id extraction inputs stay hostile.

## Tests

- `tests/property/strategies/identifiers.py`

## Open questions

None.
