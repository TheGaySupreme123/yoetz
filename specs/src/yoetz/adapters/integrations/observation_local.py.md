# src/yoetz/adapters/integrations/observation_local.py — durable local observation state

**Wave:** D | **ADRs:** ADR-010, ADR-009 | **Imports (spec-tree):** `domain/observation.py.md`,
`config/paths.md` | **Imported by:** `cli/observe.py.md`, `cli/observe_hooks.py.md`,
`cli/setup.py.md`

## Purpose

Owner-private durable store under `state_dir()/observation/` for observation consent, session
bindings, generation-fenced cursors, structural envelope retention, pre/post pairing, and advice
suppression. Used by hooks and `yoetz observe` when service observation handlers are unavailable.

## Public surface

- `LocalObservationStore`, `LocalObservationConsent`
- `HOOK_MAPPING_VERSION`, `STREAM_MAPPING_VERSION`, `YOETZ_TOOL_NAMES`
- `workspace_commitment_for_path`, `session_commitment_from_codex_id`, `observation_dir`
- Stream helpers: `get_stream_cursor` / `set_stream_cursor`, `get_stream_partial` /
  `set_stream_partial`, `note_stream_reconcile` / `last_stream_reconcile_mono`
- `allocate_hook_ordinal` — durable per-session hook sequence when the host supplies no ordinal
- Pending outbox: `enqueue_outbox`, `list_pending_outbox`, `pending_outbox_count`,
  `acknowledge_outbox`, `note_coverage_gap`

## Behavior

Consent is recorded as a workspace commitment only (never a raw path). Ingest fails closed without
active consent. Pause/resume/revoke match `ObservationPort` semantics. Evidence is retained on
revoke. No transcript prose is stored. Stream partial-line bytes persist as opaque base64 under
session commitments (never a filesystem path).

`LocalObservationStore` is the machine-private consent, session-routing, cursor, and
**pending-outbox** coordinator. Hooks enqueue structural envelopes after local accept; outbox
entries are acknowledged only after the service coordinator reports accepted/duplicate (task-bundle
commit). Overflow records `outbox_overflow` and never silently drops coverage. `note_coverage_gap`
records safe rejected/service gap codes without payload prose.

`refresh_advice` rebuilds `AdviceSnapshot` from retained envelopes via deterministic
observation-advice policies (optional semantic add-on). `peek_advice_for_delivery` suppresses
duplicates by suppression identity.
## Errors and edge cases

Fail closed on consent, mapping, and validation errors; never leak secrets.

## Invariants

1. Owner-only directory and files (`0700` / `0600`).
2. Path-free commitments in all persisted state and logs.
3. Bounded envelope/dedup retention.

## Tests

`tests/unit/cli/test_observe_cli.py`, `tests/unit/cli/test_observe_hooks.py`.

## Open questions

None.
