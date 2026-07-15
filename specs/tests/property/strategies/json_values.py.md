# tests/property/strategies/json_values.py — generated JSON value strategies

**Wave:** B | **ADRs:** ADR-001, ADR-002, ADR-003, ADR-004 | **Imports (spec-tree):**
`src/yoetz/protocol/canonical.md`, `src/yoetz/domain/values.md`
**Imported by:** property-based canonicalization tests

## Purpose

Generate the restricted JSON profile and its nearest invalid neighbors for fuzzing canonicalization
and frozen domain value logic.

## Public surface

- `strategy_json_values` — recursive valid JSON values with bounded depth and size.
- `strategy_invalid_json_bytes` — duplicate keys, invalid UTF-8, BOM/NUL, float, unsafe integer,
  and malformed framing cases.
- `strategy_unicode_edge_strings` — NFC/NFD and non-ASCII boundary strings.

## Behavior

The strategy module must generate:

- valid JSON primitives, arrays, and unique-key objects within the contractual depth/size budget;
- invalid byte-level inputs that fail before model validation;
- Unicode edge cases that prove canonicalization does not erase normalization distinctions;
- deterministic shrinking that preserves the named failure class.

## Errors and edge cases

- A strategy that emits unsupported Python types is wrong.
- A strategy that cannot shrink to a named boundary case is incomplete.

## Invariants

1. Valid and invalid paths are generated separately.
2. Strategies stay within the published budget.
3. Shrinks preserve the same contract class.

## Tests

- `tests/property/strategies/json_values.py`

## Open questions

None.
