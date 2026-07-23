# src/yoetz/application/observation_coordinator.py — service observation pipeline

**Wave:** D | **ADRs:** ADR-005, ADR-009, ADR-010 | **Imports (spec-tree):**
`domain/observation.py.md`, `adapters/integrations/observation_local.py.md`,
`adapters/sqlite/observation.md`, `adapters/integrations/codex_lifecycle.md`,
`application/observation_materialize.md`, `ports/runtime.py.md` | **Imported by:** ready
composition, observation control handlers

## Purpose

Service-level coordinator that connects machine-private local outbox ingest to the mapped
task-bundle `SqliteObservationStore`, materializes supported observations into the task ledger, and
optionally refreshes deterministic advice at the committed frontier.

## Public surface

- `ObservationCoordinator(runtime, local, clock, ids, mapping_loader=load_mapping, …)`
- `ingest_request(ObservationIngestRequest) -> ObservationIngestResult`
- ObservationPort-shaped `status|pause|resume|revoke` (proxied to `LocalObservationStore`)
- `ObservationAdviceHook` — optional post-commit hook for the advice agent

## Behavior

`ingest_request` steps:

1. Validate Codex session id.
2. Recompute and compare session commitment against the envelope.
3. Load Codex lifecycle mapping (reject `mapping_missing` when absent).
4. Resolve mapped task runtime for write.
5. Validate project consent from `LocalObservationStore`.
6. Grant/bind consent into the task `SqliteObservationStore` and ingest.
7. Materialize supported envelopes into the task ledger (`hook_observed` coverage).
8. Run deterministic advice snapshot refresh (and optional `advice_hook`).
9. Return accepted/duplicate/rejected.

Idempotency: SQLite observation dedup + stable operation digests for ledger appends so hook retry,
outbox replay, and hook/stream duplication collapse safely.

## Errors and edge cases

- Missing/revoked/paused consent → rejected with consent gap codes.
- Unmapped session → rejected `mapping_missing` (local outbox retains pending entries).
- Vault locked / service unavailable → rejected with matching gap codes.

## Invariants

1. Hooks never choose task/writer/coverage; coordinator resolves from lifecycle mapping.
2. `MemoryObservationStore` is never used on this path.
3. Local outbox ack happens only after callers observe accepted/duplicate from this coordinator.
4. No seventh MCP tool.

## Tests

`tests/unit/application/test_observation_coordinator.py`

## Open questions

None.
