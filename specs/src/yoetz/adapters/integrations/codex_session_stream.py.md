# src/yoetz/adapters/integrations/codex_session_stream.py — incremental session-stream observer

**Wave:** D | **ADRs:** ADR-010 | **Imports (spec-tree):** `importers/codex_jsonl.md`,
`domain/observation.py.md`, `observation_local.py.md` | **Imported by:** `cli/observe.py.md`,
`cli/observe_hooks.py.md`

## Purpose

Selective secondary observation source: locate a Codex session JSONL under one selected
owner-private Codex home, advance a generation-fenced cursor, emit structural
`ObservationEnvelope` values with `source=codex_session_stream`, and handle partial lines,
truncation, rotation, and restart without inventing success.

## Public surface

- `CodexSessionStreamLocator`, `resolve_codex_home`
- `SessionStreamReader`, `SessionStreamAdvance`
- `reconcile_session_stream`, `should_trigger_stream_reconcile`, `PERIODIC_RECONCILE_SECONDS`
- `default_stream_profile`, `envelope_from_stream_record`, `structural_from_stream_record`

## Behavior

Locator resolution order: (1) hook-provided path validated beneath `{codex_home}/sessions` with no
symlinks, owner-safe files, `.jsonl` suffix, and session-id membership; or (2) exact session-id
match under the sessions root. Ambiguous matches, unsafe ownership, outside-home paths, and
unsupported formats are rejected. Resolved paths are local-only and must never be persisted or
disclosed.

`SessionStreamReader` requires observation key material and labels line commitments with keyed
`hmac-sha256` via `stream_line_commitment`. Partial-line bytes and cursors persist through
`LocalObservationStore`. Automatic reconcile triggers after material hooks, compaction/resume,
stop/session end, and periodically while a mapped session is active; manual
`yoetz observe reconcile` remains recovery/diagnostic.

Generic future records contribute only validated stable structural facts; unknown semantics always
add a coverage gap. Host tool/event ids enter source identity when present.

## Errors and edge cases

Fail closed on consent, mapping, and validation errors; never leak secrets or paths.

## Invariants

1. No plaintext transcript spool.
2. No seventh MCP tool.
3. Coverage-qualified advice only.
4. Values labeled `hmac-sha256` are keyed HMACs.

## Tests

`tests/unit/adapters/test_codex_session_stream.py`.

## Open questions

None.
