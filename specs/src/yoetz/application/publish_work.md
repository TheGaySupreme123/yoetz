# src/yoetz/application/publish_work.py — prepare and atomically publish typed event batches

**Wave:** D | **ADRs:** ADR-002, ADR-003, ADR-004, ADR-005 | **Imports (spec-tree):**
`protocol/models.md`, `protocol/canonical.md`, `protocol/coverage.md`, `protocol/errors.md`,
`domain/events.md`, `domain/values.md`, `ports/ledger.md`, `ports/objects.md`,
`ports/clock.md`, `application/unit_of_work.md` | **Imported by:** `application/service.md`,
`application/import_review.md`

## Purpose

`publish_work` converts one strict public batch into finalized encrypted payload objects and one
atomic ledger append. It is the sole cooperative/imported event-ingress use case. This file owns
cross-event validation, actor/channel coverage constraints, logical request identity, and
stage-before-append ordering; the ledger port owns sequences, chains, projections, durable
idempotency, and the final commit.

## Public surface

- `async execute_publish_work(app: Application, request: PublishWorkRequest) -> PublishWorkResult`
  — implementation behind `Application.publish_work`.
- `prepare_publication(request, *, channel, app) -> PreparedPublication` — internal, pure except
  for object staging/finalization performed by the execute function.

`PreparedPublication` is application-internal and not a wire/registry type.

## Behavior

### Validation and normalization

1. Require 1..`MAX_EVENTS_PER_BATCH` (100) drafts and canonical validated request size no greater
   than `MAX_CANONICAL_REQUEST_BYTES` (1 MiB). Enforce bounds before work proportional to input.
2. Resolve the validated `session_id`/`writer_id` to one active task runtime. The writer must belong
   to that session/task and be active; no cross-bundle object/reference is accepted.
3. Derive publication channel from `client.integration` (`cooperative_mcp`, `local_cli`, or
   `codex_jsonl_import`) rather than trusting a caller-supplied coverage value. Normalize the actor
   assertion and clamp assurance to what the channel proves; MCP is `self_asserted`, importer at
   most `harness_observed` where the recorded source justifies it.
4. Preserve input event order. Require event IDs unique within the batch; causal parents and
   reference sets sorted/unique. A parent may be an earlier accepted event or an earlier event in
   this same ordered batch, never a future/cross-task event.
5. For a known schema/version, decode the exact payload dataclass, enforce all family/state/ref
   mirror rules, and reject unknown fields. For an unknown bounded schema/version, preserve frozen
   canonical payload bytes and set `projection_status="unknown_unprojected"`; do not interpret it.
6. Server-side state-sensitive validation (plan version, resolution refs, finding/event existence)
   uses the projection/frontier seen by the final append. `expected_frontier=None` is allowed only
   for intentionally append-only semantics; a state-sensitive batch requires it.

### Payload objects and request identity

For every draft, in input order:

1. Canonically encode the decoded payload only (not its transport envelope).
2. Call `objects.commitment_for(bytes, ObjectKind.event_payload)`. This allocates and publishes
   nothing. After all commitments exist, compute the logical request digest described below and
   perform a bounded `ledger.lookup_operation(writer_id, request_id)` preflight: identical terminal
   state returns the original result; a different digest conflicts; a live pending state follows
   the shared pending rule.
3. Only for a genuinely new/racing request, build `ObjectMetadata(kind=event_payload, media_type,
   task_id, created_at)` and call `objects.stage(ObjectSource(data=bytes), metadata)`, then
   `objects.finalize`. No ledger transaction is held. `stage` recomputes the commitment. A failure
   aborts the whole request; already finalized objects are safe delayed orphans and no event is
   acknowledged.
4. Build `AppendEntry` from the original stable draft, normalized author, finalized `ObjectRef`,
   returned keyed commitment/media type/size, channel-derived `Coverage`, and projected/unknown
   status. Object IDs, nonces, and ciphertext digests do not define logical retry identity.

After all commitments exist, build publication-request identity bytes from protocol/schema,
request/session/writer IDs, expected frontier, normalized actor/client logical fields, ordered event
draft headers, and each keyed payload commitment plus logical reference sets. Exclude transport
framing, plaintext, random object IDs/nonces, accepted times/sequences/predecessors, and result
fields. `request_digest = canonical_digest(identity)`.

### Atomic append and result

Construct `AppendCommand(operation_kind=publish_work, operation_id=request_id,
request_digest, expected_frontier, entries)` and call `ledger.append_batch` exactly once through
the shielded commit helper. The port rechecks idempotency and frontier, assigns consecutive writer
and ingestion sequences, builds accepted envelopes/digests, applies reducers, stores the stable
result, and commits atomically.

Map `AppendResult` to `PublishWorkResult` without recomputing assignments: `accepted` summaries,
subject/result frontiers, `accepted|replayed` outcome, bounded warning codes, coverage, and versions.
Every unknown event adds `unknown_event_schema_preserved` and a known coverage gap. Same-ID replay
returns the original assigned sequences/digests even if retry created different orphan objects.

## Errors and edge cases

- Invalid known payload/ref/state → `EVENT_INVALID`; request/batch/size bound →
  `INVALID_REQUEST`/`LIMIT_EXCEEDED`; duplicate/reused event IDs are never silently dropped.
- Stale required frontier → `FRONTIER_CONFLICT`; same request ID with different logical identity →
  `IDEMPOTENCY_CONFLICT`; writer/session mismatch → `SESSION_CONFLICT` or `SESSION_NOT_FOUND`.
- Object key/path/I/O failures occur before append and map through their typed storage/key failures;
  no partial batch is accepted.
- Cancellation before append leaves only temps/orphans. During/after the shielded append, the
  durable operation row decides; the client retries the identical request ID.
- A captured object ID referenced inside a payload must already be durable in the same task; the
  ledger transaction verifies inventory. `open_verified` is not used merely to prove existence.

## Invariants

1. All events in the request commit together or none do.
2. No accepted event references an unfinalized/missing payload object.
3. Retry identity depends on logical values and keyed commitments, never encryption randomness or
   ledger-assigned metadata.
4. Unknown schemas survive byte-for-byte but never affect known projections/checks except as a
   coverage gap.
5. Caller assertions never strengthen channel-derived actor or evidence coverage.
6. This use case performs no network work and holds no transaction during encoding/encryption/fsync.

## Tests

- `specs/tests/unit.md`: known/unknown routing, state-sensitive frontier requirement, ref mirrors,
  channel/assurance clamping, request-identity exclusions.
- `specs/tests/conformance.md`: 1/100/101 boundaries, atomic invalid member, same-ID replay,
  different-digest conflict, unknown preservation, memory/SQLite byte parity.
- `specs/tests/subprocess.md`: kill/object/commit/response points; same-ID retry; no partial batch.
- `specs/tests/integration.md`: payload/user text absent from DB/WAL/logs/errors and structural result.

## Open questions

None.
