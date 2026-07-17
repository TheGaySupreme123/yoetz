# schemas/events/accepted-event-1.0.0.schema.json — accepted event schema

**Wave:** A/B/C | **ADRs:** ADR-001, ADR-002, ADR-003, ADR-005, ADR-007 | **Imports (spec-tree):**
`src/yoetz/domain/events.md`, `src/yoetz/protocol/coverage.md`
**Imported by:** ledger parity, replay, and resource manifest tests

## Purpose

Describe the structural ledger envelope stored after acceptance.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/events/accepted-event-1.0.0.schema.json`.
- Owning model: `AcceptedEvent`.

## Behavior

Closed object with the structural envelope fields from the ledger contract, including:

- protocol/version identity;
- event/task/session/operation identity;
- author/writer/ledger chain fields;
- publication channel;
- coverage;
- payload reference and redaction state;
- artifact/evidence refs;
- entry digest.

The accepted event never embeds plaintext payload bytes. A decoded payload handle is an internal,
nonserializable domain convenience and is expressly absent from this public/persisted schema; a
field attempting to encode one is an extra property and fails closed.

## Errors and edge cases

- Missing ledger-chain identity fails.
- Plaintext payload embedding fails.

## Invariants

1. Accepted events are structural envelopes only.
2. No plaintext payload bytes are embedded.
3. Coverage is explicit.
4. Decoded payload handles never cross the schema boundary.

## Tests

- `tests/conformance/protocol/test_frozen_schemas.py`
- `tests/conformance/protocol/test_unknown_events.py`

## Open questions

None.
