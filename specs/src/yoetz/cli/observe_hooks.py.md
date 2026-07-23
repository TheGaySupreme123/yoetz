# src/yoetz/cli/observe_hooks.py — unified Codex hook observation ingress

**Wave:** D | **ADRs:** ADR-010, ADR-009 | **Imports (spec-tree):** `cli/hooks.py.md`,
`observation_local.py.md`, `codex_lifecycle.py.md` | **Imported by:** `cli/app.md`, compatibility
wrappers in `cli/hooks.py.md`

## Purpose

Single bounded observation ingress for Codex lifecycle events. Maps stdin JSON to structural
`ObservationEnvelope` values, never retains transcript prose, excludes Yoetz self-tools from advice
loops, fails soft (exit 0) on service/vault outage, pairs pre/post via correlation id, and
auto-attaches on consented SessionStart when feasible.

## Public surface

- `handle_observe`, `map_hook_payload_to_envelope`
- `SUPPORTED_HOOK_EVENTS`, `ADVICE_SAFE_EVENTS`

## Behavior

Always exit 0. On outage emit structural gap codes (`service_unavailable` / `vault_locked`) without
plaintext spool. Allocate a durable per-session hook sequence when Codex supplies no
`event_ordinal`. Source identity includes host tool/event ids and the ordinal so repeated identical
tool calls remain distinct. Cursor `last_source_commitment` uses keyed `hook_source_commitment`.

Pipeline order:

1. Local durable ingest into `LocalObservationStore`.
2. Enqueue pending outbox entry on local accept.
3. Selective session-stream reconciliation (sibling-owned locator).
4. Refresh deterministic advice from retained envelopes.
5. **SessionStart:** auto-start/attach first, persist lifecycle mapping, then drain the local
   outbox through service `ObservationIngestRequest`.
6. Non-SessionStart: when already mapped, call service ingest immediately; validate disposition —
   `accepted`/`duplicate` ack outbox; `rejected` records a safe local coverage gap.
7. Advice `additionalContext` only at safe events when a new suppression identity is available.

Service ingest sends redacted `ObservationIngestRequest` (Codex session id + envelope only).

## Errors and edge cases

Fail closed on consent, mapping, and validation errors; never leak secrets. Rejected service
dispositions become visible local gaps.

## Invariants

1. No plaintext transcript spool.
2. No seventh MCP tool.
3. Coverage-qualified advice only.
4. Outbox ack only after service accepted/duplicate (task-bundle commit).

## Tests

`tests/unit/cli/test_observe_hooks.py`.

## Open questions

None.
