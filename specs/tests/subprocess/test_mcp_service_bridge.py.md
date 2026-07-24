# tests/subprocess/test_mcp_service_bridge.py — MCP bridge/service lifecycle suite

**Wave:** C/D | **ADRs:** ADR-001, ADR-005, ADR-008 | **Imports (spec-tree):** MCP server/service client/daemon specs | **Imported by:** test runner

## Purpose

Verify MCP is a six-tool stdio bridge, not service/application/vault owner.

## Public surface

Real installed MCP/daemon transcript tests for absent/locked/ready/draining/restart/EOF.

## Behavior

Assert no service spawn/unlock tools, structured errors, reconnect/idempotent replay, stdout purity, daemon survives bridge exit.

Assert the two receipt-projection reasons keep their distinct public meaning at the bridge: blocked
is non-retryable `PRIVACY_AUTHORITY_REQUIRED` carrying `receipt_json_projection_blocked` and a
message naming the formats that do project; transient stays retryable `SERVICE_UNAVAILABLE`.
Neither reconnects, and the same client serves the following call, so a caller can switch receipt
format without a new connection.

## Errors and edge cases

Response loss, service crash/generation change, malformed MCP/control result, cancellation.
Escaped `PublicOperationError` (for example `EVENT_INVALID` with `unsorted_set_field`) maps through
`tool_error_envelope` and must not become `INTERNAL_ERROR`.

## Invariants

1. Bridge process has no trusted imports/keys/storage.
2. Closing MCP leaves service alive.

## Tests

This file is the executable owner.

## Open questions

None.
