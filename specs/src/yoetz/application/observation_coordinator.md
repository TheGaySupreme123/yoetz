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
6. Encrypt/redact content chunks and persist only repository-owned object metadata/relations.
7. Grant/bind consent into the task `SqliteObservationStore` and ingest.
8. Normalize and materialize supported envelopes into the task ledger (`hook_observed` coverage);
   persist/merge the logical-identity source claim.
9. Capture the completed-action subject state, enqueue/drain generation-fenced verification only
   when changed, and encrypt redacted output.
10. Build advice from the durable context, persist history, materialize deterministic advice as
    ordinary findings, and call the optional safe hook.
11. Return accepted/duplicate/rejected.

Idempotency covers content manifests, observation rows, stable ledger operations, logical identity,
verification cache, and advice history. Duplicate ingest retries incomplete downstream work.
Equivalent hook/stream calls share one normalized operation and only strengthen source coverage.

## Errors and edge cases

- Missing/revoked/paused consent → rejected with consent gap codes.
- Unmapped session → rejected `mapping_missing` (local outbox retains pending entries).
- Vault locked / service unavailable → rejected with matching gap codes.
- Missing encrypted locator or untrusted/changed policy records explicit gaps and executes nothing.
- Revocation immediately closes local capture and best-effort deactivates bundle locator/trust rows.

## Invariants

1. Hooks never choose task/writer/coverage; coordinator resolves from lifecycle mapping.
2. `MemoryObservationStore` is never used on this path.
3. Local outbox ack happens only after callers observe accepted/duplicate from this coordinator.
4. No seventh MCP tool.
5. The coordinator uses repositories, never private SQLite connections/SQL.

## Tests

`tests/unit/application/test_observation_coordinator.py`

## Open questions

None.
