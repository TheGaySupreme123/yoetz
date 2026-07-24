# tests/unit/mcp/test_receipt_projection_errors.py — receipt projection error mapping unit suite

**Wave:** C | **ADRs:** ADR-004, ADR-009 | **Imports (spec-tree):** `src/yoetz/mcp/server.md`,
`src/yoetz/mcp/errors.md`, `ports/control.md`, `src/yoetz/protocol/errors.md` | **Imported by:**
test runner

## Purpose

Freeze the split between the two receipt projection failures so an agent can tell "retry later"
apart from "this format cannot be projected". Before the split, both reached the agent as a single
retryable `SERVICE_UNAVAILABLE`, and a real agent retried the same blocked JSON request five times
without ever learning that `markdown` or `text` would succeed.

## Public surface

Direct assertions over `mcp/server._control_error_result` for the `receipt` operation, one test per
control reason.

## Behavior

`privacy_projection_blocked` maps to `PRIVACY_AUTHORITY_REQUIRED` with `retryable=false` and
`safe_details.reason_code == "receipt_json_projection_blocked"`, and its bounded message names an
alternative format. `privacy_projection_unavailable` maps to `SERVICE_UNAVAILABLE` with
`retryable=true` and its own `reason_code`, because that failure really is transient.

## Errors and edge cases

The blocked message must remain a fixed bounded string that names `markdown` or `text`; ADR-004
forbids carrying exception text or tracebacks into a public error.

## Invariants

1. The two reasons never collapse to the same public code or retryable flag.
2. Every projection error carries a registered `reason_code` in `safe_details`.

## Tests

This file is the executable owner. End-to-end agent-context projection of a JSON receipt under the
default policy is owned by `tests/conformance/operations/test_receipt_contract.py`; the default
allowlist itself is owned by `tests/unit/service/test_default_agent_context_allowlist.py`.

## Open questions

None.
