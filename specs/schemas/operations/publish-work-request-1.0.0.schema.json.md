# schemas/operations/publish-work-request-1.0.0.schema.json — publish-work request schema

**Wave:** D | **ADRs:** ADR-001, ADR-002, ADR-003, ADR-004 | **Imports (spec-tree):**
`src/yoetz_core/application/publish_work.md`, `src/yoetz_core/domain/events.md`
**Imported by:** CLI, MCP, and validation fixtures

## Purpose

Describe the request shape for atomically publishing a batch of event drafts.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/core/0.1/operations/publish-work-request/1.0.0`.
- Owning model: `PublishWorkRequestModel`.

## Behavior

Closed request object with:

- shared envelope fields (`schema_version`, `request_id`, `actor`, `client`);
- `expected_frontier`;
- bounded ordered array of `event_drafts`;
- request-identity fields required by the application contract.

The schema enforces exact batch bounds, canonical event ordering, and request identity. It does not
accept unknown fields or implied side channels.

## Errors and edge cases

- Too many events fail.
- Canonical-equivalent but differently ordered batch fields fail where order is contractually
  significant.
- Extra keys fail.

## Invariants

1. The batch is bounded.
2. Identity is explicit.
3. Events remain ordered.

## Tests

- `tests/unit/protocol/test_models_and_schemas.py`
- `tests/conformance/operations/test_publish_work_contract.py`

## Open questions

None.
