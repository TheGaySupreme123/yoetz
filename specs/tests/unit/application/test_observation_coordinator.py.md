# tests/unit/application/test_observation_coordinator.py

**Wave:** D | **Owns:** focused unit coverage for observation ingest request codecs,
materialization mapping, local outbox, and coordinator rejection without mapping.

## Cases

- `ObservationIngestRequest` JSON round-trip excludes task/writer IDs
- Pre/post/unpaired materialization shapes
- Local outbox enqueue/ack/overflow gap
- SQLite ingest idempotent duplicate
- Coordinator rejects `mapping_missing` without calling runtime route
- Control handlers route `ObservationIngestRequest` via `ingest_request`

## Open questions

None.
