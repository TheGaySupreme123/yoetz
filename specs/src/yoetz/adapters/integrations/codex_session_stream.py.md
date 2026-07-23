# src/yoetz/adapters/integrations/codex_session_stream.py — incremental session-stream observer

**Wave:** D | **ADRs:** ADR-010 | **Imports (spec-tree):** `importers/codex_jsonl.md`,
`domain/observation.py.md`, `observation_local.py.md` | **Imported by:** `cli/observe.py.md`

## Purpose

Selective secondary observation source: advance a generation-fenced JSONL cursor over a Codex
session file, emit structural `ObservationEnvelope` values with `source=codex_session_stream`, and
handle partial lines, truncation, rotation, and restart without inventing success.

## Public surface

- `SessionStreamReader`, `SessionStreamAdvance`
- `default_stream_profile`, `envelope_from_stream_record`, `structural_from_stream_record`

## Behavior

Reuses `parse_codex_jsonl_from_offset` / mapping vocabulary from the batch importer. Unknown future
fields become opaque gaps. Dedup against hook copies is by source identity + cursor via the
observation store.

## Errors and edge cases

Fail closed on consent, mapping, and validation errors; never leak secrets.

## Invariants

1. No plaintext transcript spool.
2. No seventh MCP tool.
3. Coverage-qualified advice only.

## Tests

`tests/unit/adapters/test_codex_session_stream.py`.

## Open questions

None.
