# src/yoetz/ports/observation.py — local-control live Codex observation boundary

**Wave:** D | **ADRs:** ADR-005, ADR-009, ADR-010 | **Imports (spec-tree):** `protocol/errors.md`,
`protocol/coverage.md`, `domain/observation.py.md`, `ports/objects.md`, `ports/ledger.md`,
`ports/importer.md` | **Imported by:** observation application/CLI composition, Codex hook and
session-stream adapters, status projection, capability tests

## Purpose

`ObservationPort` is the trusted-service boundary for first-party Codex **live** observation. It
ingests bounded observation envelopes, tracks cursors and consent, exposes observation status and
advice frontiers, and supports pause/resume/revoke. It is local control only: the five control
methods are CLI/UI support surfaces, never MCP tools. The public MCP surface remains exactly six
tools; advice reaches agents through nonblocking hooks and ordinary `status` / findings / coverage.

This port is distinct from `ImporterPort`. Batch `codex exec --json` import may reuse mapping-version
and gap-code vocabulary; it does not share consent, cursor, dedup, or advice state and never earns
`hook_observed`.

## Public surface

- `class ObservationPort(Protocol)` with async methods:
  - `ingest(envelope: ObservationEnvelope) -> ObservationIngestResult`
  - `status(query: ObservationStatusQuery) -> ObservationStatus`
  - `pause(command: ObservationControlCommand) -> ObservationStatus`
  - `resume(command: ObservationControlCommand) -> ObservationStatus`
  - `revoke(command: ObservationRevokeCommand) -> ObservationStatus`
- Shared closed types owned with `domain/observation.py`: `ObservationSource`,
  `ObservationEnvelope`, `ObservationContentChunk`, `ObservationCursor`, `ObservationStatus`, `AdviceSnapshot`,
  `ObservationIngestResult`, `ObservationStatusQuery`, `ObservationControlCommand`,
  `ObservationRevokeCommand`, and closed gap/reason enums as registered in `INTERFACES.md`.

Corresponding ordinary-control methods (CLI/UI only; denied to `mcp_bridge`) are exactly
`observation_ingest`, `observation_status`, `observation_pause`, `observation_resume`, and
`observation_revoke`.

## Behavior

### Consent and lifecycle

Observation requires one project-level confirmation recorded as a private workspace commitment
(never a raw filesystem path in logs, status, diagnostics, or receipts). Consent is independent of
egress consent. Without active consent, `ingest`/`resume` fail closed and create no durable
observation evidence. `revoke` stops new ingestion and retains already-kept evidence; it does not
delete encrypted objects or ledger history.

`pause` keeps consent but stops new ingest until `resume`. Status lifecycle is exactly
`active|degraded|stale|stopped`.

### Dual sources

`ObservationSource` is exactly `codex_hook | codex_session_stream`. Hooks are the primary
low-latency path. Session-stream reconciliation is selective and secondary: it fills gaps against
the cursor without replacing hook ingest or inventing events the stream does not contain.

### Ingest

`ingest` validates the envelope and bounded ephemeral content chunks, normalizes logical identity
before materialization, advances the generation-fenced cursor only after durable outbox insertion,
and stores sensitive bounded evidence only as authenticated encrypted objects. Plaintext state holds
allowlisted structure and object commitments/relations. Equivalent dual-source calls share one
operation; the second source may strengthen coverage/content references without duplicate ledger
events. Unknown visible events retain opaque structure, encrypted content, and an explicit gap.

`hook_observed` (and `harness_observed` authorship where justified) requires real observation
evidence under an active consented observation arm. Trigger-only hooks, consent markers, empty
status, or degraded/stopped sources alone do not raise coverage.

### Status and advice

`status` returns `ObservationStatus`: lifecycle, source coverage, last observation, lag, gaps,
unsupported events, and the current `AdviceSnapshot` frontier identity. `AdviceSnapshot` carries
ranked findings, exact evidence basis, confidence/coverage, recommended next action, freshness, and
suppression identity. The same snapshot surfaces through nonblocking hooks and ordinary public
`status`; secret-like command output never appears in either surface.

### Durability and migration

Existing v0.1 ledger/object/import data remains readable without rewrite. Migration `0003` adds
encrypted content/workspace-binding references, logical identity, exact-digest trust, verification
jobs/results, and advice history/delivery state. Vault or service outage never creates plaintext
content retry state; structural replay retains `content_capture_unavailable`.

## Errors and edge cases

- Missing/revoked consent, wrong generation, stale cursor, or capability mismatch fails before
  mutation with a bounded reason.
- Oversized payloads, secret-like command output in structural fields, or never-send content fail
  closed; no plaintext spool is written.
- Concurrent ingest is generation-fenced; identical envelopes are idempotent.
- MCP callers cannot invoke observation control methods even with a schema-valid body.

## Invariants

1. No seventh MCP tool exists for observation.
2. Hooks are primary; session-stream reconciliation is secondary and selective.
3. Observation consent ≠ egress consent; revoke stops ingest and keeps retained evidence.
4. Plaintext state is allowlisted structure + commitments only.
5. `hook_observed` requires real observation evidence.
6. `ImporterPort` remains a separate batch-import surface.
7. Repository bytes propose no check authority; exact digest trust is local and encrypted.

## Tests

- Unit/property: consent, cursor fencing, dedup, gap codes, unknown-event opacity.
- Integration: pause/resume/revoke, outage fail-closed, advice via status.
- Capability: E-013 installed-artifact observation arm for exact Codex cells.

## Open questions

E-013 remains the empirical gate for exact event/payload/privacy evidence per capability cell.
