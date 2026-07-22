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
evidence. Reconcile advances the stream cursor for a consented workspace.

## Tests

`tests/unit/cli/test_observe_cli.py`.
