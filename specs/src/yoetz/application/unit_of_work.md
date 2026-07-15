# src/yoetz/application/unit_of_work.py — durability ordering and cancellation/ambiguity discipline

**Wave:** D | **ADRs:** ADR-001, ADR-002, ADR-003, ADR-004 | **Imports (spec-tree):**
`protocol/canonical.md`, `protocol/errors.md`, `domain/values.md`, `ports/ledger.md`,
`ports/objects.md`, `ports/start_catalog.md` | **Imported by:** `application/start.md`,
`application/publish_work.md`, `application/check.md`, `application/respond.md`,
`application/receipt.md`, `application/import_review.md`

## Purpose

This module gives application use cases one disciplined shape for work that spans pure validation,
encrypted object publication, a short durable port commit, cancellation, and idempotent recovery.
It is not a generic database transaction or ORM unit of work. SQLite transaction ownership stays
inside adapters; application code sees immutable prepared values and port results only.

## Public surface

Application-internal, natural-language contracts:

- `PreparedMutation` — immutable operation identity, canonical logical request digest, finalized
  object/entry references, expected frontier, and the bounded port command ready to commit.
- `CommitResolution` — closed outcome `committed`, `not_committed`, `pending`, `quarantined`, or
  `unknown`, plus only a verified stored result/safe reason where applicable.
- `run_prepared_append(ledger, prepared) -> AppendResult` — execute the shielded task-bundle append
  discipline below.
- `resolve_ambiguous_operation(ledger, writer_id, operation_id, request_digest) -> CommitResolution`.
- `run_catalog_transition` / `resolve_ambiguous_start` — equivalent helpers that delegate to
  `StartCatalogPort` rather than pretending pre-writer start is a ledger append.

These names remain internal and need not enter `INTERFACES.md`. They contain no connection,
cursor, SQL callable, arbitrary callback, SDK exception, or filesystem path.

## Behavior

### Phase A — pure/bounded preparation outside a write transaction

1. Strictly validate protocol/cross-field/session/actor semantics and caps before work proportional
   to input.
2. Canonicalize the caller-owned logical request and compute its stable digest. User payload
   identity uses keyed commitments where ADR-002 requires them. Exclude encryption nonces,
   generated object IDs, filesystem routes, lease values, accepted timestamps, sequences, and
   other server-assigned/random outputs so re-encryption on retry cannot change identity.
3. Validate/decode known event payloads, preserve bounded unknown schemas, check references that
   must be structurally durable, run secret/privacy policy, hash/commit data, and construct
   immutable entry precursors. CPU-heavy validation/hashing is never moved into the commit merely
   for convenience.
4. Stage and finalize every encrypted object in dependency order: write temp bytes, fsync the file,
   atomic rename, then fsync the objects directory. Finalize referenced content objects before
   event payload objects that refer to them. Only durable finalized `ObjectRef`s enter
   `PreparedMutation`.
5. Expensive filesystem traversal, object encryption/fsync, projection rebuild, subprocess work,
   and provider/network calls occur outside the SQLite transaction. For check, external work
   occurs after its durable pending reservation and each result is persisted before a later short
   selection/finalization transition; this helper applies separately to each durable transition,
   not as one long transaction.

An optional bounded operation lookup may skip repeated object work when it finds a terminal same-
digest result. It is only a preflight optimization. The commit path always repeats idempotency,
frontier, writer-chain, owner-generation, and object-reference validation atomically.

### Phase B — one bounded shielded commit call

1. Check cancellation before submitting a writer job. Enter a cancellation shield only around
   the port submission/await that can start the short commit. The shield does not cover validation,
   object publication, provider calls, backoff, rendering, or transport response writing.
2. The adapter admits the job to its bounded FIFO writer queue. Saturation fails promptly through
   the reviewed retryable busy mapping; application code never waits on an unbounded queue and
   never opens a second writer to bypass it.
3. Inside the adapter's `BEGIN IMMEDIATE`-equivalent section, perform only bounded indexed work:
   verify current generation/nonce; repeat idempotency lookup; verify expected frontier,
   writer/predecessor and durable objects; allocate sequences; insert immutable rows/references;
   apply the bounded incremental reducer change set; store the canonical structural terminal
   result; and commit. Every intermediate exception rolls back where SQLite can prove rollback.
4. A cancellation of the awaiting coroutine never interrupts a job already admitted/running on the
   writer thread. The shield waits for its definite result. If outer cancellation became pending,
   do not emit a success acknowledgement after leaving the shield: re-raise cancellation and let
   the caller repeat the same operation ID. The durable row may already be complete.
5. A port success means SQLite reported commit and the terminal result is durable. Return it to the
   use case; only the transport layer later serializes/acknowledges. A failed/ambiguous commit is
   never converted to success by local memory.

“Shield the smallest commit section” means protecting the outcome-producing writer job through its
definite completion, not allowing caller cancellation to cancel an in-flight SQLite API call and
not shielding the entire user operation.

### Phase C — post-commit result and acknowledgement

1. Validate/decode the port's structural result as server output. A construction/validation
   failure here is an internal defect, never `INVALID_REQUEST`.
2. Reopen referenced user-visible objects only when the operation-specific result requires it
   (for example receipt replay), verify their digests, and render outside the transaction.
3. Return success only after the durable commit result and result validation. MCP/CLI response
   serialization, partial stdout writes, broken pipe, or client timeout occurs after the product
   commit boundary and cannot roll it back; same-ID retry/status is the recovery story.
4. Checkpointing is asynchronous/bounded maintenance. A failed PASSIVE checkpoint does not change
   a committed acknowledgement because FULL synchronous WAL commit is the durability boundary.

### Filesystem/database non-atomicity

Yoetz does not claim a distributed transaction across object files and SQLite. The safe ordering is
object first, ledger reference second. Failure/crash before the DB commit can leave an unreferenced
encrypted object; delayed generation/pin-aware GC removes it after the safety window. The forbidden
opposite order would create an acknowledged dangling reference. No eager compensation deletes an
object that another retried/committed operation may reference.

### Ambiguous outcome resolution

After connection loss, process kill, cancellation, timeout, or response loss, resolve by durable
identity; never infer outcome from the exception or elapsed time:

| Durable observation for `(writer_id, operation_id)` | Resolution |
|---|---|
| no row, after the writer job/process is definitively stopped and recovery completed | `not_committed`; an ordinary retry may prepare again |
| `complete` + same request digest | `committed`; return/reconstruct the stored original result |
| `complete` + different digest | raise `IDEMPOTENCY_CONFLICT` |
| `pending` + same digest, current generation and unexpired lease | `pending`; return `OPERATION_PENDING` |
| `pending` + same digest, expired or stale generation | only the registered check/import state machine may fenced-CAS reclaim and resume |
| `pending` for a single-commit kind | contradictory state; quarantine, never guess |
| `quarantined` | stable terminal safe reason; operator repair is explicit |
| storage cannot be verified/read | `unknown` plus mapped storage failure; never say “not committed” |

`lookup_operation` itself does not renew/reclaim. Start uses the catalog's installation/request
scope and `reserve_or_resume`; it must not query the task ledger before writer allocation. A new
verified connection/startup recovery is required after SQLite reports an ambiguous connection/
commit failure before absence can be trusted.

### Reads

Read-only status/replay does not use the commit shield or create an operation row. Adapters use
bounded indexed pages/read-only connections, release read transactions between pages, bind stable
snapshot positions/frontiers, and never materialize the entire ledger in a hot path. A projection
rebuild is separate fenced maintenance, not hidden inside one long status read.

## Errors and edge cases

- Validation/canonicalization/object failure before submission has no ledger effect; finalized
  objects may be orphans. Map only registered safe error codes/reasons and never exception text.
- Queue/busy timeout → retryable `BUNDLE_BUSY` under the public mapping. Lost generation →
  `STORAGE_UNSAFE`; canonical/reference corruption → `STORAGE_CORRUPT`; disk/full/sync/commit
  failures never acknowledge.
- Cancellation before the shield has no commit; during the shield may commit and is resolved by
  ID; after commit but before response definitely may have committed and is replayed by ID.
- A caller changing logical fields while reusing the operation ID is a conflict even when object
  ciphertext/IDs differ. Identical logical retry is not a conflict when fresh orphan objects were
  created before the terminal preflight/commit replay was discovered.
- An unexpected exception after commit must not overwrite/delete the durable terminal result or
  issue a compensating event. It is sanitized; retry exposes the original result.

## Invariants

1. No network, object fsync/encryption, unbounded scan, or user-controlled rendering occurs inside
   a SQLite write transaction.
2. Every acknowledged ledger reference points to an already durable verified object.
3. Preflight improves efficiency only; the final atomic section owns correctness.
4. Cancellation/timeout never proves rollback, and commit ambiguity is resolved from durable state.
5. Application code never receives or controls a database connection/transaction.
6. All writer jobs are bounded, serialized, generation-fenced, and success follows commit.

## Tests

- `specs/tests/unit.md`: logical digest inclusion/exclusion, prepared-value immutability, outcome
  resolution table, result-validation-as-defect, cancellation timing model.
- `specs/tests/conformance.md`: preflight races, same/different digest replay, pending/quarantine,
  reference and memory/SQLite result parity.
- `specs/tests/subprocess.md`: every object/transaction/commit/response kill point, signals during
  queued/running writer jobs, reopen/retry proves no duplicate/partial/missing acknowledged effect.
- `specs/tests/integration.md`: queue saturation, busy/full/readonly/fsync/connection-loss faults,
  owner-generation takeover, orphan-GC safety window and pin protection.
- `specs/tests/property.md`: generated cancellation/fault/retry interleavings against a reference
  state machine; every acknowledgement has one terminal durable effect.

## Open questions

None.

E-004 is the sole central writer-queue calibration gate.
