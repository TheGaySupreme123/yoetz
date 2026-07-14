# src/yoetz_core/adapters/memory/start_catalog.py — reference StartCatalogPort state machine

**Wave:** B/C | **ADRs:** ADR-001, ADR-002, ADR-003, ADR-004 | **Imports (spec-tree):**
`protocol/canonical.md`, `protocol/errors.md`, `protocol/ids.md`, `ports/start_catalog.md`,
`ports/keys.md`, `ports/clock.md`, `ports/ids.md` | **Imported by:** start/runtime conformance and
property tests; injected `adapters/runtime.md` factories

## Purpose

`MemoryStartCatalogAdapter` is the executable reference model for the complete `StartCatalogPort`.
It lets conformance tests distinguish catalog semantics from SQLite mechanics while preserving the
same commitment, route-generation, idempotency, lease, phase, completion-evidence, quarantine,
and exact-route behavior as `SqliteStartCatalog`.

This is not a relaxed fake. Process-local lifetime is its only durability difference. It stores no
title/raw workspace/raw external reference, does not inspect a filesystem, and does not invent a
second product ledger.

## Public surface

- `class MemoryStartCatalogAdapter(StartCatalogPort)` implementing `commit_identity`,
  `resolve_route`, `reserve_or_resume`, `advance_phase`, `complete`, and `quarantine` with the
  exact registered signatures.
- `@dataclass(slots=True) class MemoryStartCatalogState` — injected process-local catalog state:
  route records, operation records, current catalog owner generation, and monotonic revision.
- `@dataclass(frozen=True, slots=True) class MemoryStartCatalogPolicy` — v0.1 reference policy;
  start lease is 60 seconds and all limits/reason enums equal the SQLite catalog contract.

Constructor contract:

`MemoryStartCatalogAdapter(*, installation_id, lookup: MacKeyHandle, state,
transaction_lock, clock: ClockPort, ids: IdPort, policy=MemoryStartCatalogPolicy())`.

`transaction_lock` is the one injected async catalog lock shared by every adapter instance over
the same state. `lookup` must be the ready vault's purpose/domain-bound
`MacKeyHandle(purpose=catalog_lookup)`. It remains opaque and redacted; no raw key or key-store
object is accepted or exposed.

## Behavior

### Reference storage and atomic sections

`MemoryStartCatalogState` owns frozen/copy-on-write internal records equivalent to the catalog
tables:

- routes keyed by `task_id`, with unique secondary maps for active session, generated bundle
  route, active route-identity digest, and optional scoped commitment pair;
- start operations keyed by `(installation_id, operation_id)`, with request/mode/route action,
  once-allocated IDs, exact route generation/digest, state/phase, lease, pinned response object,
  terminal envelope/digest, quarantine code, and safe timestamps;
- `owner_generation` and `revision` used to fence leases and detect accidental in-place mutation.

One logical transaction acquires `transaction_lock`, captures one `clock.now_utc()` value before
the lock, clones only affected records/indexes, validates every CHECK/unique/FK-equivalent
invariant, and swaps the new state plus `revision + 1` without awaiting. A fault before swap changes
nothing; response loss after swap is resolved by the stored row. Crypto, clock, ID generation, and
completion-evidence canonicalization occur outside the lock, with all derived values rechecked
inside it before commit.

The memory records contain only validated structural IDs, generated relative routes, keyed
commitments, safe digests/enums/counts, and canonical terminal bytes. Their `repr` is redacted.
Raw identity values exist only in the one-shot `StartIdentityInput` passed to `commit_identity` or
the re-verification step in `reserve_or_resume` and are never copied into state/fault output.

### Identity commitments

`commit_identity` validates byte-exact strings/JSON values and calls the injected lookup handle:

- title: `lookup.mac(START_TITLE_DOMAIN, JCS(title))`;
- workspace: `lookup.mac(WORKSPACE_REF_DOMAIN, JCS(workspace_ref))`;
- external task: `lookup.mac(EXTERNAL_REF_DOMAIN, JCS(external_ref))`.

All three constants are imported from `ports/start_catalog.py` and include the byte-exact trailing
`\x00`; this adapter never reconstructs a domain from a display string.

Workspace/external values and commitments are both present or both absent. Missing pairs yield
`None`; no MAC of JSON null is created. `reserve_or_resume` recomputes all commitments and compares
them in constant time before looking up idempotency or attachment state. A mismatch is
`INVALID_REQUEST` with a bounded reason and no persistence.

### Exact route identity and point lookup

For every route:

```text
route_identity_digest = sha256(canonical({
  "task_id": task_id,
  "bundle_relpath": "tasks/" + task_id,
  "route_generation": positive_integer
}))
```

`resolve_route(session_id)` validates the opaque session ID before acquiring the lock, performs one
unique secondary-index lookup, and returns `None` only for exact absence. For a hit it copies the
route row, releases the lock, then validates task/session, generated route, positive generation,
closed state, and stored digest before returning `TaskRoute`. The returned
`route_identity_digest` corresponds to SQLite's `active_route_identity_digest`.

It never returns commitments, quarantine reason, timestamps, aliases, or alternate routes; never
acquires a lease; and never scans by title/path/cwd. Missing secondary/primary agreement,
duplicate identity, malformed generated route, or digest contradiction is `STORAGE_CORRUPT`.

### Reserve or resume

`reserve_or_resume` mirrors the port decision table in one atomic state swap:

1. Existing operation with a different request digest is `IDEMPOTENCY_CONFLICT`.
2. Existing `complete`/`quarantined` row with the same digest returns `replayed` with its exact
   stored terminal envelope; no IDs or route facts are regenerated.
3. Existing pending row with current owner generation and future expiry is
   `OPERATION_PENDING`. Expired or stale-generation ownership is reclaimed by CAS:
   `lease_generation + 1`, current generation, new owner nonce/expiry; the recorded phase and
   allocation/route identities are preserved and returned as `resumed`.
4. For a new operation, resolve `create`/`attach`/`create_or_attach` against the exact scoped
   commitment pair and optional supplied active session. Agreement/conflict/absence rules match
   `ports/start_catalog.md`; no partial/fuzzy match or candidate disclosure exists.
5. A created route gets new task/session/writer/lifecycle IDs, generated
   `tasks/<task_id>`, generation `1`, canonical route digest, and state `initializing`. An attached
   route reuses task/path/generation/digest, allocates a fresh session/writer/lifecycle ID for this
   start operation, and leaves the old active session indexed until terminal completion.
6. Insert route plus `pending/route_reserved` operation (or only the attached operation), lease
   generation `1`, and all secondary indexes in one swap; return `reserved` reconstructed from
   committed state.

Proposed random IDs generated for a losing concurrent reservation are unused and never observable.
The winning stored allocation is the only returned/replayed identity.

### Phase checkpoint, completion, and quarantine

`advance_phase` captures the proposed result fields outside the lock and performs one CAS on
operation revision plus lease owner/generation/current catalog generation/expiry. It accepts only
the direct successor `route_reserved -> bundle_ready -> lifecycle_committed -> result_published`.
The result is absent on the first two transitions and required only for `result_published`, where
its exact object ID is pinned. Repeating the identical already-committed transition returns the
stored allocation; a different/skipped/regressed transition is a bounded contradiction. A lost
lease returns `OPERATION_PENDING`.

`complete` independently recomputes and validates `StartCompletionEvidence` before the lock. In
one swap it rechecks lease, phase `result_published`, route task/path/generation/digest, allocation,
response-object/result digest, evidence milestone/generation/digest, and exact current catalog
route. It then stores the structural terminal envelope/digest, clears lease fields, marks the
operation `complete/terminal`, changes a created route `initializing -> active`, and atomically
switches the route's `active_session_id` to the allocation's fresh session. Only the new active
session index is visible after the swap.

`quarantine` is one lease-fenced terminal swap. It accepts only an allowlisted `SafeReason.code`,
stores the stable safe quarantine envelope/digest, clears lease fields, and marks an initializing
created route quarantined. It is for verified contradiction/corruption, not normal validation,
key/backend unavailability, cancellation, or a live competing lease.

### Generation changes and adapter lifetime

Tests may advance `state.owner_generation` only through their ownership fixture. The next atomic
call treats every older operation lease as stale; an old holder cannot advance/complete/quarantine.
This adapter does not implement restore or mutate an existing route generation. Restore fixtures
install a new validated route record atomically through the maintenance reference model; the
catalog adapter thereafter resolves only that active generation/digest.

Discarding `MemoryStartCatalogState` loses all rows by design. No API pretends otherwise, and no
test uses process exit as evidence of durable behavior.

## Errors and edge cases

- Public classifications match SQLite: `INVALID_REQUEST`, `IDEMPOTENCY_CONFLICT`,
  `OPERATION_PENDING`, `SESSION_CONFLICT`, `SESSION_NOT_FOUND`, `BUNDLE_BUSY`, `STORAGE_UNSAFE`,
  and `STORAGE_CORRUPT` where applicable.
- Cancellation before the state swap changes nothing; after-swap response loss replays/resumes
  the committed row. Clock reversal cannot validate a stale generation.
- Exact same request/route transition is replay; same key with different digest/result/evidence is
  conflict or corruption, never overwrite.
- Failure diagnostics contain only validated IDs, phase/state, counts, and bounded reason codes.
  Commitment inputs, raw refs/title, key material, path/cwd, and exception text never appear.
- Missing/stale or wrong-purpose/domain handles fail before the state lock and produce no
  commitment or mutation.

## Invariants

1. Memory and SQLite adapters return byte-equivalent shared values under identical clocks, IDs,
   installation lookup handle, owner generations, and action sequences.
2. One `(installation_id, operation_id)` owns one allocation and terminal outcome forever.
3. Scoped attachment, active session, generated route, and active route digest indexes are unique
   and agree with their primary route record after every swap.
4. Route identity binds task/path/generation; session/state transitions do not alter it.
5. Phase and lease transitions are monotonic and generation-fenced; acknowledgement follows the
   terminal state swap.
6. The state is structural catalog data only and contains no user plaintext.

## Tests

- `specs/tests/conformance.md`: shared reference state-machine traces against memory and SQLite,
  including every mode/idempotency/lease/phase/completion/quarantine branch.
- `specs/tests/property.md`: generated races, stale generations, response loss, digest conflict,
  and route-index consistency after every action.
- `specs/tests/integration.md`: keyed-commitment parity/canaries and exact
  active/initializing/quarantined/absent route resolution.

## Open questions

None.
