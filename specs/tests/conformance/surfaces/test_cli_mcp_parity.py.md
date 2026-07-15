# tests/conformance/surfaces/test_cli_mcp_parity.py — CLI and MCP surface parity

**Wave:** D | **ADRs:** ADR-003, ADR-005, ADR-007 | **Imports (spec-tree):**
`src/yoetz/cli/app.md`, `src/yoetz/mcp/server.md`
**Imported by:** conformance surface tests

## Purpose

Prove CLI JSON mode and MCP tool calls expose the same public contract for each operation.

## Public surface

- `test_cli_and_mcp_request_result_parity` — the structured contract is identical.
- `test_cli_and_mcp_error_shape_parity` — public errors match exactly.
- `test_human_summary_is_weaker_than_structured_output` — text is bounded.

## Behavior

The test drives the same request through CLI and MCP and asserts:

- structured results match;
- exit codes and `isError` states align with public errors;
- safe human summaries do not reveal more than the structured payload;
- wrapper-specific transport noise does not affect the oracle.
- ordinary operation parity includes projected semantic findings carrying a bounded reviewer
  challenge; MCP can respond or publish work but cannot create local-human setup, widening, waiver,
  credential, or per-request approval authority.

## Errors and edge cases

- A CLI-only or MCP-only behavior drift fails.

## Invariants

1. CLI and MCP share the same public contract.
2. Structured truth is identical.
3. Summaries remain weaker.
4. Operation parity does not expose CLI-only human-control authority through MCP.

## Tests

- `tests/conformance/surfaces/test_cli_mcp_parity.py`

## Open questions

None.
