# ADR-005 — Codex capability, identity, and MCP transport edge cases

**Status:** Working decision for spec drafting (2026-07-13). Ratification requires the pinned
capability matrix run from an installed artifact.
**Implemented by:** `src/yoetz/adapters/mcp_stdio.py`,
`src/yoetz/adapters/importers/codex_jsonl.py`, `src/yoetz/mcp/`, the Codex
skill files under `skills/codex/`, and `tests/capability/`.

## Decisions

1. **Supported Codex range:** target/maximum-tested `0.144.5`; minimum supported set by the
   capability run (candidate `0.139.0` as observed local fixture); anything newer than
   maximum-tested is "untested", not "supported". The release manifest records min/max/denied.
2. **Integration posture:** Codex is the MCP client; Yoetz is a local stdio server registered via
   `codex mcp add yoetz -- yoetz mcp serve`, default `required = false`, only after
   `codex mcp get yoetz --json` confirms the global name is absent. A same-name entry is never
   overwritten unless a separately reviewed flow proves it is the exact Yoetz-owned entry. Skill
   installed explicitly to `.agents/skills/yoetz/` with preview/consent. Codex-readable
   `SKILL.md` frontmatter is limited to `name`, `description`, and optional
   `metadata.short-description`; Yoetz protocol/version compatibility remains in its private
   manifest and is not represented as Codex-readable frontmatter. MCP registration remains a
   separate previewed step, so v0.1 declares no `agents/openai.yaml` MCP dependency.
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
   `self_asserted` for ordinary MCP callers in v0.1. `harness_observed` authorship (and the
   `hook_observed` publication/artifact classes) require a justified observation channel. For
   first-party Codex that channel is the v0.1 `ObservationPort` path — hooks primary, selective
   session-stream reconciliation secondary — once the exact capability cell proves observation and
   project-level observation consent is active (ADR-010). A trigger-only hook still observes
   nothing and cannot raise authorship or artifact observation. No inference from display names or
   transcript fields.
7. **Startup budget:** measured cold-start target < 2 s on reference hardware; the release binds
   the acceptable margin to the default observed in every advertised Codex capability cell rather
   than assuming an invariant timeout.

## Capability matrix (must pass from the installed artifact, per pinned version)

User & trusted-project MCP config; same-name config preflight; six tool calls (interactive +
`codex exec`); optional-server failure disclosure; required-server startup failure per supported
Codex surface; duplicate skill-name discovery across loaded roots; parent + subagents attribution; resume/
reattach without duplicates; `--json` JSONL import with unknown-event quarantine; skill discovery
(explicit `$yoetz` and implicit); E-013 trigger and observation arms for exact passing profiles
(manual recovery / cooperative-only coverage for absent profiles); observation consent,
ingest/status/pause/resume/revoke, `hook_observed` only from real observation evidence, and
AdviceSnapshot via hooks plus ordinary `status`; cancellation/timeout ambiguous-write retry; stdout purity
under all of the above.
