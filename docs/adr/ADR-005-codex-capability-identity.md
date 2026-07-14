# ADR-005 — Codex capability, identity, and MCP transport edge cases

**Status:** Working decision for spec drafting (2026-07-13). Ratification requires the pinned
capability matrix run from an installed artifact.
**Owning public specs:** `specs/src/yoetz_core/adapters/mcp_stdio.md`,
`specs/src/yoetz_core/adapters/importers/codex_jsonl.md`, `specs/src/yoetz_core/mcp/`, the Codex
skill specs, and `specs/tests/capability/`.

## Decisions

1. **Supported Codex range:** target/maximum-tested `0.144.3`; minimum supported set by the
   capability run (candidate `0.139.0` as observed local fixture); anything newer than
   maximum-tested is "untested", not "supported". The release manifest records min/max/denied.
2. **Integration posture:** Codex is the MCP client; Yoetz is a local stdio server registered via
   `codex mcp add yoetz -- yoetz-core mcp serve`, default `required = false`. Skill installed
   explicitly to `.agents/skills/yoetz-core/` with preview/consent.
3. **MCP protocol/SDK:** protocol negotiated (latest published `2025-11-25`, never assumed); SDK
   pinned `mcp==1.28.1`, low-level `Server` surface, `validate_input=False`, direct
   `CallToolResult`, Yoetz-side jsonschema Draft 2020-12 output validation, nested constant
   fallback defined in the MCP error spec.
4. **Transport:** Yoetz-owned `bounded_stdio_server`: 1 MiB payload cap excluding
   LF, ≤64 KiB `os.read` chunks, strict UTF-8, BOM/NUL/duplicate-key rejection, sole stdout
   writer with partial-write loop, zero-capacity AnyIO streams for backpressure. Certified for
   macOS arm64 + glibc Linux x86_64 only; Windows needs a separate gate.
5. **Parse-error ID decision:** when a frame is malformed and no
   request ID is recoverable, Yoetz emits a **manually constructed JSON-RPC 2.0 error frame with
   `"id": null`** through the sole writer (bypassing the SDK's non-null-ID model), with the fixed
   transport error code. If the pinned Codex client is shown by transcript test to mishandle the
   null-ID frame, fallback is orderly transport termination. Never fabricate an ID.
6. **Actor identity:** actor identity is caller-asserted; the server assigns at most
   `self_asserted` for MCP callers in v0.1 (`harness_observed` only via a justified observation
   channel, none of which exist pre-hooks). No inference from display names or transcript
   fields.
7. **Startup budget:** measured cold-start target < 2 s on reference hardware; hard requirement is
   comfortable margin under Codex's 10 s MCP default; release binds to measured percentiles.

## Capability matrix (must pass from the installed artifact, per pinned version)

User & trusted-project MCP config; six tool calls (interactive + `codex exec`); optional-server
failure disclosure; required-server startup failure; parent + subagents attribution; resume/
reattach without duplicates; `--json` JSONL import with unknown-event quarantine; skill discovery
(explicit `$yoetz-core` and implicit); cancellation/timeout ambiguous-write retry; stdout purity
under all of the above.
