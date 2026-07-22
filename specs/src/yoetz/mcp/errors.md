# src/yoetz/mcp/errors.py — structured MCP error envelopes and sanitization

**Wave:** D | **ADRs:** ADR-002, ADR-005 | **Imports (spec-tree):**
`protocol/errors.md`, `protocol/models.md`, `mcp/summaries.md`
**Imported by:** `mcp/server.md`, CLI envelope rendering, and error tests

## Purpose

This file converts public operation errors and validation failures into the exact MCP result shapes
Yoetz sends over stdio. It is the one place where user-safe structured errors are assembled.

## Public surface

| Name | Signature (natural language) |
|---|---|
| `build_public_error_result(...)` | build a structured error envelope with `ok: false` |
| `build_last_resort_internal_error_result()` | startup-built common fallback result shared by all tools |
| `safe_validation_locations(exc)` | reduce a validation error to allowlisted locations and bounded reason codes |
| `sanitize_unknown_tool_name(name)` | turn an invalid tool name into a safe public failure description |
| `tool_error_envelope(...)` | convenience constructor for tool-level public failures |

## Behavior

`build_public_error_result(...)` returns a structured tool result that carries the public error code,
correlation ID, retryability, and safe details. The object is suitable for MCP `structuredContent`
and pairs with a compact summary in `content`.

`build_last_resort_internal_error_result()` creates the request-independent fallback object used
when helper code fails after the request is already in the dispatch fence. It must not call any
helper that can throw and must be schema-valid for every public tool output schema.

`safe_validation_locations(exc)` extracts only allowlisted field paths and bounded reason codes from
structured validation errors. It must never echo the raw input value, the original exception text,
or documentation URLs. For `extra_forbidden` failures whose leaf segment is not allowlisted (for
example `("client", "id")` when a caller invents `client.id`), it projects the longest allowlisted
parent path (here `/client`) instead of dropping the location or allowlisting a generic `id`
segment that could leak unsafe paths.

`sanitize_unknown_tool_name(name)` maps a tool name that reached the valid `tools/call` method but
did not match a registered tool into a safe invalid-params description. It must not reveal the
full raw argument payload or echo the caller-controlled name.

`tool_error_envelope(...)` is a convenience wrapper for the common public error shape. It keeps the
tool result format consistent between success and failure cases. The MCP server uses it for
defense-in-depth when a bound (or unbound-then-bound) `PublicOperationError` escapes the ordinary
service client; without that path, application failures such as `EVENT_INVALID` would be unreachable
and collapse into `INTERNAL_ERROR`.

The concrete v0.1 mapping API stays transport-neutral: `build_public_error_result` accepts an exact
`PublicErrorCode`, bounded public message, retryability, bound correlation ID, optional request ID,
and allowlisted safe details; `tool_error_envelope` accepts a bound `PublicOperationError`. Both
return the common protocol failure mapping, which the server later places in `structuredContent`.
`safe_validation_locations` returns at most eight `{field, reason}` records, keeps only statically
allowlisted public field paths and bounded reason tokens, and never stringifies the exception.
When an `extra_forbidden` leaf is not allowlisted but an allowlisted parent prefix exists, the
projected `field` is that parent path with reason `extra_forbidden`.

## Errors and edge cases

- JSON-RPC error objects are not used for ordinary public operation failures on registered tools.
- The last-resort fallback must still validate even when helper code is broken.
- Unknown tool names are a sanitized JSON-RPC/protocol failure (`INVALID_PARAMS`), not a structured
  tool execution result, when they arrive via `tools/call`. The public message never echoes the
  caller-controlled name; stderr must not interpolate that name either.
- Validation summaries may name fields, not payloads. An `extra_forbidden` on an untrusted leaf under
  an allowlisted parent (such as invented `client.id`) must still surface the parent path
  (e.g. `/client`) so `safe_details` is usable; it must not echo the untrusted leaf name.

## Invariants

1. Public errors stay structured.
2. No raw user text is echoed in validation summaries.
3. Error envelopes remain compatible with `checked_call_result(...)`.
4. The last-resort fallback is always schema-valid.

## Tests

- `tests/conformance/surfaces/test_mcp_contract_matrix.py` — public error-envelope structure,
  allowlisted validation summaries, and side-effect-free unknown-tool behavior.

## Open questions

None.
