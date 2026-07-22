# tests/capability/test_codex_six_tools.py — six-tool MCP conduit and gated live Codex cell

**Wave:** D/F | **ADRs:** ADR-002, ADR-005, ADR-006 | **Imports (spec-tree):** capability evidence,
MCP descriptors/server, pinned MCP SDK | **Imported by:** Codex capability claim

## Purpose

Prove the installed MCP server advertises exactly six frozen tools and that all six can be driven
through the real MCP stdio transport. Live interactive/exec Codex driving remains Gate 2/3 work
and fails closed when unauthorized or when no live driver exists.

## Public surface

Cases: MCP stdio tool inventory; MCP stdio six-tool conduit (success or structured unavailable/
locked); live Codex cell gated by `YOETZ_LIVE_CODEX` with `live_driver_unavailable` when
authorized but unimplemented.

## Behavior

List and call all six tools via `mcp.client.stdio` / `ClientSession` against `uv run yoetz mcp
serve`. Do not call Application methods directly and do not describe direct application calls as
MCP/Codex coverage. Accept schema-valid success or `SERVICE_UNAVAILABLE` / `VAULT_LOCKED`
structured tool errors. When live authorization is absent, record `UNSUPPORTED` with
`live_codex_not_authorized`. When authorized without a Gate 2/3 driver, record `UNSUPPORTED` with
`live_driver_unavailable` and skip rather than unconditionally failing.

## Errors and edge cases

- Direct Application.start/publish/check paths are not capability evidence for six MCP tools.
- A live pass requires a real Codex driver; placeholders must not claim pass.

## Invariants

1. Exactly six tools map one-to-one to six operations over MCP stdio.
2. Local MCP conduit ≠ Codex model activation.
3. Live cells fail closed when the driver is unavailable.

## Tests

Run the MCP stdio cases on every advertised platform artifact; live remains opt-in.

## Open questions

None.
