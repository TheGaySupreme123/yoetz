# tests/unit/service/test_client.py — ordinary ServiceClient unit suite

**Wave:** C/D | **ADRs:** ADR-008 | **Imports (spec-tree):** `src/yoetz/service/client.md` | **Imported by:** test runner

## Purpose

Verify typed request/result conversion, the complete 25-token method registry,
reconnect/cancel behavior, and client-kind authority.

## Public surface

Fake-control-stream tests for all `ServiceClient` methods and close/fork states, including
`privacy_receipts_list` and `privacy_receipts_get` through the generic call path.

## Behavior

Assert request IDs survive, RPC IDs differ, results validate, and response loss never implies
outcome. Assert every convenience method serializes the identically named wire token; the receipt
methods use `privacy_receipts_list|privacy_receipts_get`, never the internal audit-port
`list_receipts|get_receipt` names. MCP lifecycle and all privacy methods fail admission.
Receipt-list parsing yields the exact bounded `PrivacyReceiptPage`; receipt-get parsing preserves
the closed `found(PrivacyReceiptView)|not_found` tag and never returns a nullable body.

## Errors and edge cases

Absent/locked/draining/wrong-peer/stale-generation/partial-response and replay paths.

## Invariants

1. No direct runtime/application fallback constructor exists.
2. Client types contain no secret handle.

## Tests

This file is the executable owner.

## Open questions

None.
