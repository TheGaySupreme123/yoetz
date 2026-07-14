# schemas/common/public-error-1.0.0.schema.json — public error envelope schema

**Wave:** A/B | **ADRs:** ADR-002, ADR-005, ADR-007 | **Imports (spec-tree):**
`src/yoetz_core/protocol/errors.md`, `src/yoetz_core/cli/exits.md`, `src/yoetz_core/mcp/errors.md`
**Imported by:** operation-result schemas and fallback error paths

## Purpose

Describe the structured public error object shared by CLI and MCP results.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/core/0.1/common/public-error/1.0.0`.
- Owning model: `PublicOperationError`.

## Behavior

Closed object with required fields:

- `code` — one of the public error codes.
- `message` — bounded user-facing text.
- `retryable` — boolean.
- `correlation_id` — error correlation ID.
- `safe_details` — bounded object or array of primitive-safe diagnostics, optional.

The schema rejects raw stack traces, payload echoes, SQL, and secrets. It is the canonical public
failure shape and the source for CLI exit-code mapping.

## Errors and edge cases

- Unknown codes fail.
- Oversized details fail.
- Extra keys fail.

## Invariants

1. Public errors are bounded and structured.
2. Retryability is explicit.
3. Safe details never echo user payloads.

## Tests

- `tests/unit/protocol/test_errors.py`
- `tests/conformance/surfaces/test_mcp_contract_matrix.py`

## Open questions

None.
