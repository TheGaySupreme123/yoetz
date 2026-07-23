# src/yoetz/cli/observe.py — observe CLI user controls

**Wave:** D | **ADRs:** ADR-010 | **Imports (spec-tree):** `observation_local.py.md`,
`codex_session_stream.py.md` | **Imported by:** `cli/app.md` (`yoetz observe …`)

## Purpose

Local-control CLI for observation consent and status: grant/pause/resume/revoke/status and
session-stream reconcile. Not MCP tools.

## Public surface

- `observe_status`, `grant_observation`, `pause_observation`, `resume_observation`,
  `revoke_observation`, `reconcile_session_stream`

## Behavior

Grant stores a private workspace commitment only and never logs the raw path. Revoke retains
evidence. Reconcile is a recovery/diagnostic command that advances the stream cursor for a
consented workspace (automatic reconcile is hook-driven via `CodexSessionStreamLocator`).

## Errors and edge cases

Fail closed on consent, mapping, and validation errors; never leak secrets.

## Invariants

1. No plaintext transcript spool.
2. No seventh MCP tool.
3. Coverage-qualified advice only.

## Tests

`tests/unit/cli/test_observe_cli.py`.

## Open questions

None.
