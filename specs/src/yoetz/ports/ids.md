# src/yoetz/ports/ids.py — injectable identifier-generation boundary

**Wave:** B | **ADRs:** ADR-002 | **Imports (spec-tree):** `protocol/ids.md` | **Imported by:**
`application/service.md`, `application/start.md`, `application/publish_work.md`,
`application/check.md`, `application/respond.md`, `application/receipt.md`,
`application/import_review.md`, `adapters/memory/*`, `adapters/sqlite/*`,
`adapters/objects/*`

## Purpose

`protocol/ids.py` owns the spelling and cryptographic generation algorithm; `IdPort` makes calls
to that algorithm injectable. Production uses the OS-CSPRNG implementation, while tests supply a
finite scripted sequence so event IDs, findings, receipts, attempts, and operation results can be
asserted byte-for-byte. The port does not assign meaning, reserve IDs, or make retries idempotent.

## Public surface

- `class IdPort(Protocol)` with `def new(self, kind: IdKind) -> str` (INTERFACES §10).

There are no additional production exports. The runtime's stateless implementation delegates
directly to `protocol.ids.new_id`; deterministic test doubles implement this Protocol in test
support and are not shipped as runtime policy.

## Behavior

### Production implementation

1. Accept an `IdKind` and call `protocol.ids.new_id(kind)` exactly once.
2. Return the resulting typed-prefix lowercase canonical UUIDv4 unchanged.
3. Do not cache, persist, log, time-sort, retry, or inspect the returned UUID's random bits.
4. `IdKind.ACTOR` is not generated; the delegated function raises
   `ProtocolValueError("actor_id_not_generated")` because actors are caller asserted.
5. The implementation is safe for concurrent calls because every call obtains fresh bytes from
   the OS CSPRNG and owns no mutable counter.

### Allocation and retry rules

- Client-owned IDs (`request_id`, event/obligation/claim and any registry-approved payload IDs)
  arrive validated and are never replaced by the application.
- Server IDs are allocated at the first durable workflow point that owns them. `start` allocates
  task/session/writer/lifecycle-event IDs inside the catalog reservation and reuses them on resume.
  A check's durable resume state owns its finding/job/attempt allocation. A receipt retry returns
  the stored original receipt ID after commit.
- An ID generated before a transaction that never commits may be abandoned. Reusing it for a
  different logical object is forbidden, but a fresh retry may generate a new one when no durable
  operation row exists.
- Database uniqueness violations are not hidden by an in-process regeneration loop. They are
  treated as a collision/invariant failure and sanitized; silently selecting a new ID after part
  of a workflow was persisted would break replay and idempotency.

### Deterministic test doubles

A conforming scripted test double:

1. Is constructed with an ordered sequence of `(expected_kind, value)` pairs.
2. On `new(kind)`, requires the next expected kind, validates the supplied value with
   `validate_id`, consumes it once, and returns it.
3. Raises a test-only assertion on kind mismatch or exhaustion; it never fabricates a fallback.
4. Exposes remaining-count inspection only to the test, not through `IdPort`.

Tests may also use a recording wrapper around the production implementation to assert which kinds
were requested, but golden tests never depend on random concrete values.

## Errors and edge cases

- Invalid kind or actor generation is `ProtocolValueError`; at a public request boundary this is
  a software/configuration defect unless caused by caller-supplied data already rejected earlier.
- OS CSPRNG failure propagates to the application defect boundary and becomes sanitized
  `INTERNAL_ERROR`; it is never downgraded to a predictable PRNG.
- A repeated valid UUID from a faulty test source is accepted by the spelling validator but fails
  at the durable uniqueness/idempotency boundary. The port does not claim entropy assurance.
- IDs are not secrets or authorization tokens; knowing one never grants cross-session access.

## Invariants

1. Production generation is exactly the algorithm in `protocol/ids.py`; no module implements a
   second prefix table or UUID renderer.
2. IDs remain opaque and are never parsed for time, order, routing, or actor assurance.
3. Retry stability is owned by durable allocation/idempotency state, not an ID cache.
4. No fallback weakens the OS-CSPRNG requirement.
5. Deterministic tests can account for every generated ID and fail on an unexpected allocation.

## Tests

- `specs/tests/unit.md`: delegation per non-actor `IdKind`, actor rejection, no normalization,
  scripted kind matching/exhaustion.
- `specs/tests/property.md`: every production-generated value validates for the requested kind;
  random valid IDs do not influence ranking except where the registry explicitly uses ID bytes as
  the final deterministic tie-break.
- `specs/tests/conformance.md`: crash/retry allocations return the catalog/operation's original
  IDs and do not consume replacement scripted IDs after replay.

## Open questions

None.
