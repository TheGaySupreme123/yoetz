# src/yoetz/cli/hooks.py — Codex lifecycle hook command handlers

**Wave:** D | **ADRs:** ADR-010 | **Imports (spec-tree):** `codex_lifecycle.py.md`,
`service/client.py.md`, `protocol/models.py.md`, packaged `guidance/agent-instructions.md` |
**Imported by:** `cli/app.md` (`yoetz hooks …`)

## Purpose

Implement the three Codex hook commands that inject the intake cue, correlate a successful Yoetz
`start` to the Codex session, and re-ground via read-only `status` after resume/compaction—always
failing truthfully and narrowly without blocking ordinary work.

## Public surface

- `handle_user_prompt_submit` — stdin hook payload → stdout additionalContext with intake cue;
  also routes structural facts through `handle_observe` when consent is active.
- `handle_post_tool_use` — successful `start` / `mcp__yoetz__start` → write mapping; routes
  structural observe for all PostToolUse payloads.
- `handle_session_start` — `startup`/other sources may observe; `clear` removes mapping;
  `resume`/`compact` re-ground; absent mapping falls back to inactive context unless observation
  auto-attach produced context.
- `handle_observe` — compatibility export to the unified observe ingress.
- Helpers: `read_hook_payload`, `intake_cue_text`, `INACTIVE_CONTEXT`, `YOETZ_START_TOOL_NAMES`.

## Behavior

All handlers: bounded stdin (256 KiB), strict JSON, unknown host fields ignored, allowlisted mapping
fields strict, exit `0` with degraded output on error (prefer not to fail the host turn), at most one
bounded sanitized stderr line, no secrets in output.

`user-prompt-submit` never touches the service; cue bytes come from packaged
`guidance/agent-instructions.md` (first 512 UTF-8 bytes / paragraph boundary).

`post-tool-use` writes a mapping iff the tool is Yoetz start and structured result has `ok=true` with
validated IDs. Failed/absent/malformed start creates no mapping. Duplicate events rewrite
idempotently.

`session-start` degraded matrix:

| Condition | Context |
| --- | --- |
| absent mapping | inactive — no invented task |
| mapping + status success | active — task id, frontier, “call status before further material work” |
| mapping + unreachable service | unavailable — no live receipt promised |
| mapping + vault locked | locked — no live receipt promised |
| hook crash / invalid JSON | degraded inactive/empty; no false success |

Uses `connect_service` (not on-demand spawn) for least side effect. Never creates a ledger event
merely for reading status. Single-flight lock coalesces duplicate concurrent resume/compact events.
`clear` removes the mapping file.

## Errors and edge cases

- Hook payload shape is host-owned; only structural fields we need are read.
- Stop hook is explicitly out of scope pending a separate ADR.
- No auto-unlock, no transcript parsing, no support/profile claims.

## Invariants

1. Prefer exit 0 with truthful degraded context over host-blocking failure.
2. No ledger mutation from status re-grounding.
3. Fork/subagent writers remain distinct via exact session-id mapping keys.

## Tests

- `tests/unit/cli/test_hooks.py`
- `tests/subprocess/test_hooks_cli.py`

## Open questions

None for this slice; Stop policy remains design-gated.
