# tests/property/strategies/operations.py — generated operation and request strategies

**Wave:** B/C/D | **ADRs:** ADR-003, ADR-005, ADR-006, ADR-007 | **Imports (spec-tree):**
`src/yoetz_core/protocol/models.md`, `src/yoetz_core/application/*.md`
**Imported by:** property-based operation tests

## Purpose

Generate request-shaped values for the six public operations and their near-miss variants.

## Public surface

- `strategy_start_requests` — valid start requests and near misses.
- `strategy_publish_work_requests` — valid batch requests with exact identities.
- `strategy_check_requests` — frozen-case requests with mode variations.
- `strategy_respond_requests` — response requests with waivers and expiries.
- `strategy_status_requests` — read-only projection queries.
- `strategy_receipt_requests` — frozen-frontier receipt queries.

## Behavior

The strategy module must keep request identity fields, explicit frontiers, and bounded limits
coherent. Invalid variants should mutate one contract rule at a time so shrink behavior remains
explainable.

## Errors and edge cases

- A request strategy that depends on the application implementation is too coupled.
- A request strategy that loses the request identity contract fails the suite’s purpose.

## Invariants

1. Requests are operation-specific and bounded.
2. One mutation should explain one failure.
3. Request strategies stay independent of the SUT.

## Tests

- `tests/property/strategies/operations.py`

## Open questions

None.
