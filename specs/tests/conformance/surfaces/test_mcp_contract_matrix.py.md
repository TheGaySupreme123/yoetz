# tests/conformance/surfaces/test_mcp_contract_matrix.py — MCP contract matrix

**Wave:** D | **ADRs:** ADR-003, ADR-005, ADR-007 | **Imports (spec-tree):**
`src/yoetz_core/mcp/server.md`, `src/yoetz_core/mcp/errors.md`
**Imported by:** conformance surface tests

## Purpose

Prove MCP initialization, tool discovery, structured content, and fallback errors match the public
contract.

## Public surface

- `test_initialize_and_tools_list_contract` — version negotiation and tools list are exact.
- `test_tool_call_parity_and_isError_mapping` — tool calls map to the right public result/error.
- `test_fallback_error_object_is_admitted` — the last-resort error object is accepted.
- `test_public_error_and_validation_summaries_are_sanitized` — public error envelopes expose only
  allowlisted paths/reason codes and their text never exceeds structured content.
- `test_unknown_tool_is_a_tool_level_invalid_request` — unknown tools are distinct from malformed
  JSON-RPC and never enter application dispatch.

## Behavior

The test asserts:

- tools/list reports the six operation tools plus support paths;
- structured output and `isError` are exact;
- fallback error envelopes are admissible before stdin is accepted;
- validation and transport noise remain bounded;
- every public error branch has an admissible structured envelope and a deterministic sanitized
  summary containing only allowlisted field paths and bounded reason codes;
- unknown tool, unknown method, malformed request, and valid application error remain four distinct
  outcomes with no tool-side mutation for the first three.

## Errors and edge cases

- A fallback that is not admitted by the schema fails.
- A validation summary that echoes rejected values, secrets, paths outside the allowlist, traceback,
  or exception text fails.
- An unknown tool that reaches application dispatch or is reported as a transport parse failure
  fails.

## Invariants

1. MCP surface is frozen.
2. Fallback error shape is explicit.
3. Tool discovery is exact.
4. Error summaries are bounded projections of structured public errors.
5. Unknown-tool handling is side-effect free and protocol-distinct.

## Tests

- `tests/conformance/surfaces/test_mcp_contract_matrix.py`

## Open questions

None.
