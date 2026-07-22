# tests/capability/test_codex_six_tools.py — six-tool MCP dispatch and gated live Codex cell

**Wave:** D/F | **ADRs:** ADR-002, ADR-005, ADR-006 | **Imports (spec-tree):** capability evidence,
MCP descriptors/server, pinned MCP SDK | **Imported by:** Codex capability claim

## Purpose

Prove the installed MCP server advertises exactly six frozen tools and that all six registered
handlers can be reached through the real MCP stdio transport. Live interactive/exec Codex driving remains Gate 2/3 work
and fails closed when unauthorized or when no live driver exists.

## Public surface

Cases: MCP stdio tool inventory; MCP stdio six-tool dispatch through the common structured
`INVALID_REQUEST` boundary; live Codex cell gated by `YOETZ_LIVE_CODEX` with `live_driver_unavailable` when
authorized but unimplemented.

## Behavior

List and call all six tools via `mcp.client.stdio` / `ClientSession` against `uv run yoetz mcp
serve`. Do not call Application methods directly and do not describe direct application calls as
MCP/Codex coverage. This local cell does not claim service conduit behavior; that remains owned by
the dedicated integration suite. When live authorization is absent, record `UNSUPPORTED` with
`live_codex_not_authorized`. When authorized without a Gate 2/3 driver, record `UNSUPPORTED` with
`live_driver_unavailable` and skip rather than unconditionally failing.

## Errors and edge cases

- Direct Application.start/publish/check paths are not capability evidence for six MCP tools.
- A live pass requires a real Codex driver; placeholders must not claim pass.

## Invariants

1. Exactly six tools map one-to-one to six operations over MCP stdio.
2. Local MCP dispatch ≠ service conduit or Codex model activation.
3. Live cells fail closed when the driver is unavailable.

## Tests

Run the MCP stdio cases on every advertised platform artifact; live remains opt-in.

## Open questions

None.
