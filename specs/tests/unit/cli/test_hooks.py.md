# tests/unit/cli/test_hooks.py — lifecycle hook handler unit tests

**Wave:** D | **ADRs:** ADR-010 | **Imports (spec-tree):** `src/yoetz/cli/hooks.py.md`,
`src/yoetz/adapters/integrations/codex_lifecycle.py.md` | **Imported by:** CLI unit suite

## Purpose

Cover user-prompt-submit intake cue delivery without service contact; post-tool-use success/failure/
non-start/idempotent/malformed paths; session-start inactive/active/unavailable/clear/startup
matrix; and that active re-ground calls only status.

## Public surface

Direct handler invocation with BytesIO stdout and injected state/connect seams.

## Behavior

Fake service clients assert `status` only. Unreachable connect yields unavailable text. Malformed
JSON exits without traceback and creates no mapping.

## Errors and edge cases

Degraded exit 0 paths are required.

## Invariants

1. No invented task ids when mapping is absent.
2. No ledger mutation on re-ground.

## Tests

This file.

## Open questions

None.
