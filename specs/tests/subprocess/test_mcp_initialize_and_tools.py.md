# tests/subprocess/test_mcp_initialize_and_tools.py — raw MCP lifecycle and six-tool contract

**Wave:** D/F | **ADRs:** ADR-005 | **Imports (spec-tree):** MCP server/error/schema specs,
frame/child helpers | **Imported by:** PR, capability, and release gates

## Purpose

Verify the installed stdio server's lifecycle, negotiation, tool inventory, schema identity, calls,
and error envelopes at the raw JSON-RPC boundary without relying on a friendly SDK client.

## Public surface

Cases cover initialize/initialized/tools-list, exactly six tool definitions, each successful call,
invalid lifecycle order, unknown method/tool, invalid params, application error, internal fence,
cancellation, shutdown/EOF, supported protocol and named fallback.

## Behavior

Send literal golden frames via the byte driver. Assert no application/tool output before successful
initialization; negotiated protocol/capabilities/server identity are exact. `tools/list` returns
exactly start/publish/check/respond/status/receipt in frozen order with schemas/annotations matching
reviewed resource digests.

Invoke a deterministic workflow and compare structured results, compact text summaries, `isError`,
IDs/frontiers/coverage, and ledger effects. Invalid JSON-RPC vs valid application errors use distinct
fixed numeric/application codes. Cancel at validation, application wait, and response delivery;
server remains synchronized for the next request.

## Errors and edge cases

- Duplicate request ID handling follows JSON-RPC/session policy and cannot duplicate ledger effect.
- Unknown params/fields fail strict Yoetz validation even if SDK would coerce them.
- Unsupported protocol fails negotiation without opening/mutating a bundle.
- All stdout frames parse; stderr is sanitized and content-free.

## Invariants

1. Exactly six public tools and reviewed schemas are exposed.
2. Lifecycle errors have no product side effects.
3. `isError` and structured envelopes agree.
4. Cancellation/internal errors do not desynchronize framing.

## Tests

Golden raw transcripts and installed resource schemas are independent oracles. Run strict-local with
network denied on every advertised platform and both supported negotiation cells.

## Open questions

None.
