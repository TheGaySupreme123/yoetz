# src/yoetz/adapters/integrations/codex_lifecycle.py — Codex↔Yoetz structural mapping store

**Wave:** D | **ADRs:** ADR-010 | **Imports (spec-tree):** `config/paths.md`,
`protocol/ids.md`, `protocol/canonical.md` | **Imported by:**
`cli/hooks.py.md`, lifecycle/plugin unit tests

## Purpose

Own the private, allowlisted structural correlation record that maps one Codex session id to Yoetz
task/session/writer identities after a successful `start`, and provide single-flight locking for
resume/compaction re-grounding. This module stores no prompts, transcripts, paths, titles, or
secrets.

## Public surface

- `MAPPING_VERSION` — exactly `1`.
- `LifecycleMapping` — frozen allowlisted record with exactly the keys
  `mapping_version`, `codex_session_id`, `yoetz_task_id`, `yoetz_session_id`, `yoetz_writer_id`,
  `last_frontier`.
- `validate_codex_session_id`, `encode_frontier_token`, `parse_frontier_token`.
- `codex_lifecycle_dir`, `mapping_path`, `load_mapping`, `store_mapping`, `clear_mapping`,
  `mapping_from_start_ids`, `acquire_session_lock`.

## Behavior

Storage is one JSON file per Codex session under `state_dir()/codex-lifecycle/`, written atomically
(tmp+rename), mode `0600`, with a bounded read size. Reads fail closed: extra keys, wrong types,
unknown `mapping_version`, oversized files, or invalid IDs are treated as absent. Yoetz IDs are
validated with `IdKind` shapes (`tsk_`, `ses_`, `wri_`). `codex_session_id` is a bounded printable
ASCII opaque token without path separators. `last_frontier` is `null` or `{sequence}:{head_digest}`.

Fork/subagent identity rule: the mapping is keyed by the exact `codex_session_id` from the hook
payload. A forked or subagent Codex session never inherits the parent's writer; a new session id has
no mapping until its own successful `start`.

`acquire_session_lock` uses an `O_EXCL` lock file with a stale-lock timeout so duplicate concurrent
session-start handlers coalesce.

## Errors and edge cases

- Malformed or hostile mapping bytes never partially trust fields.
- Forbidden content (path separators, overlong tokens, non-ID Yoetz values) is rejected on write and
  treated as absent on read.
- Concurrent directory creation races are tolerated when the resulting directory is owner-private.

## Invariants

1. Exactly the six allowlisted keys; no prose, paths, cwd, credentials, or env values.
2. Atomic private writes; bounded reads.
3. Fail closed on unknown version or schema drift.
4. Fork/subagent sessions do not inherit parent writer mappings by session-id keying alone.

## Tests

- `tests/unit/adapters/test_codex_lifecycle.py`

## Open questions

Stop-hook auto-unlock and transcript-derived identity remain out of scope pending a separate ADR.
