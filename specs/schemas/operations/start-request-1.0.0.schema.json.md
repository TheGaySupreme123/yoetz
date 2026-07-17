# schemas/operations/start-request-1.0.0.schema.json — start request schema

**Wave:** D | **ADRs:** ADR-001, ADR-002, ADR-003, ADR-004 | **Imports (spec-tree):**
`src/yoetz/application/start.md`, `src/yoetz/protocol/models.md`
**Imported by:** CLI, MCP, and validation fixtures

## Purpose

Describe the public request shape for the `start` operation.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/operations/start-request-1.0.0.schema.json`.
- Owning model: `StartRequestModel`.

## Behavior

Closed request object with the shared envelope fields plus the start-specific payload:

- `schema_version`
- `request_id`
- `mode` (`create`, `attach`, `create_or_attach`)
- `session_id` optional for attach/replay flows
- `task_title`
- `external_ref` optional
- `workspace_ref` optional
- `actor`
- `client`
- `requested_view` fixed to the compact start-view contract

Both-or-neither rules for attachment refs and exact enum/pattern validation are enforced here. The
schema rejects mutable paths, branch names, and extra keys.

## Errors and edge cases

- Partial attachment identity fails.
- Wrong schema version fails.
- Unknown extras fail.

## Invariants

1. Start identity is explicit.
2. Attachment refs are coherent.
3. Closed shape is required.

## Tests

- `tests/unit/protocol/test_models_and_schemas.py`
- `tests/conformance/operations/test_start_contract.py`

## Open questions

None.
