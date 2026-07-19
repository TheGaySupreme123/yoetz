# src/yoetz/ports/start_catalog.py — StartCatalogPort protocol for pre-writer start allocation

**Wave:** B | **ADRs:** ADR-001, ADR-002, ADR-003, ADR-004 (K_lookup commitments) |
**Imports (spec-tree):** `protocol/ids.md`, `protocol/errors.md`, `ports/keys.md`, `ports/objects.md`
(EncryptedResultRef references an ObjectRef), `ports/runtime.md` (StartCompletionEvidence) |
**Imported by:** `application/service.md`,
`application/start.md`, `adapters/sqlite/start_catalog.md`, `adapters/memory/start_catalog.md`

## Purpose

`start` is the one mutating operation with no writer yet, so its idempotency scope is
`(server_installation_id, operation_id)` in the structural installation catalog, not the task
bundle. `StartCatalogPort` abstracts that catalog: durable one-time allocation of
task/session/writer/lifecycle-event IDs, the crash-safe pending → complete/quarantined state
machine, keyed attachment commitments, and safe bundle routing. Without this port, retrying a
crashed `start` could allocate a second session for the same key, and the application layer would
depend on catalog SQL directly.

The catalog is structural routing/idempotency state only — never a second event store, and never
a home for task titles or other user plaintext.

## Public surface

- `class StartCatalogPort(Protocol)`:
  - `async def commit_identity(self, value: StartIdentityInput) -> StartIdentityCommitments`
  - `async def resolve_route(self, session_id: SessionId) -> TaskRoute | None`
  - `async def reserve_or_resume(self, request: StartCommand) -> StartAllocation`
  - `async def complete(self, allocation: StartAllocation, result: EncryptedResultRef,
    evidence: StartCompletionEvidence) -> None`
  - `async def quarantine(self, allocation: StartAllocation, reason: SafeReason) -> None`
  - `async def advance_phase(self, allocation: StartAllocation, phase: StartPhase,
    result: EncryptedResultRef | None = None) -> StartAllocation`
    (durable checkpoint between reserve and complete)
- `@dataclass(frozen=True, slots=True) class StartCommand`
- `@dataclass(frozen=True, slots=True) class StartIdentityInput` — exactly `task_title`, optional
  `workspace_ref`, and optional `external_ref`; constant-redacted and never logged or persisted
- `@dataclass(frozen=True, slots=True) class StartIdentityCommitments` — exactly
  `title_commitment`, optional `workspace_ref_commitment`, and optional
  `external_ref_commitment`; domain-separated keyed commitments only
- `@dataclass(frozen=True, slots=True) class TaskRoute` — bounded structural catalog route for
  exact runtime resolution
- `enum TaskRouteState` — `initializing`, `active`, `quarantined`
- `@dataclass(frozen=True, slots=True) class StartAllocation`
- `@dataclass(frozen=True, slots=True) class StartOperationLease` — start-catalog-specific
  generation lease; distinct from the check-operation `ports.ledger.OperationLease`.
- `@dataclass(frozen=True, slots=True) class EncryptedResultRef`
- `@dataclass(frozen=True, slots=True) class SafeReason` — `code: str` from the allowlisted
  `quarantine_code` enum; no free text, ever.
- `enum StartPhase` — `route_reserved`, `bundle_ready`, `lifecycle_committed`,
  `result_published`, `terminal` (the exact phases persisted by
  `specs/migrations/catalog/0001.sql.md`).
- `enum StartMode` — `create`, `attach`, `create_or_attach`.
- Byte-exact MAC domains owned by this port and imported by both adapters:
  `START_TITLE_DOMAIN = b"yoetz/start-title/v1\x00"`,
  `WORKSPACE_REF_DOMAIN = b"yoetz/workspace-ref/v1\x00"`, and
  `EXTERNAL_REF_DOMAIN = b"yoetz/external-task-ref/v1\x00"`.

## Behavior

### Types

`commit_identity` computes installation-keyed HMAC commitments for every low-entropy user field
without persistence. The application uses those values, never raw title/reference text, in the
unkeyed canonical request digest. `reserve_or_resume` independently recomputes and verifies the
commitments before applying idempotency/routing. Each concrete adapter receives only the ready
vault's opaque `MacKeyHandle(purpose=catalog_lookup)`; this port never accepts or returns
`K_lookup` bytes, a key locator, or a generic key store.

The exact domains are the three registered byte constants above, including the trailing `\x00`
delimiter; each commitment is
`lookup.mac(domain_bytes, JCS(validated_string))` rendered as `hmac-sha256:<hex>`. The handle owns
the installation-scoped `K_lookup` HMAC-SHA-256 operation and rejects every other purpose/domain.
Values
are byte-exact after boundary validation—no case folding or Unicode normalization. Missing paired
references produce `None`, not a commitment to JSON null. Only workspace/external commitments may
be stored in the route table; the title commitment exists to build/verify `request_digest` and is
not a title lookup index.

`StartCommand` fields:

- `operation_id: str` — the caller's `request_id` (`req_` + UUIDv4), validated upstream.
- `request_digest: str` — `sha256:<hex>` of the canonical validated start request excluding
  transport-only fields and replacing raw identity fields with their keyed commitments, computed
  by the application.
- `mode: StartMode`.
- `identity_input: StartIdentityInput` — raw title and opaque workspace/external identities with a
  constant-redacted representation; workspace/external are both present or both absent.
- `identity_commitments: StartIdentityCommitments` — the exact title/workspace/external
  domain-separated commitments returned by `commit_identity`; absent refs have absent commitments.
  `reserve_or_resume` recomputes them from `identity_input` and constant-time compares before using
  `request_digest` or routing. Raw values never enter catalog storage, logs, or errors.
- `session_id: SessionId | None` — additional consistency guard; when supplied it MUST resolve to
  the same route as the commitment pair.

`StartAllocation` fields:

- `outcome: Literal["reserved", "resumed", "replayed"]` — `reserved`: new pending row created;
  `resumed`: an existing pending row was reclaimed under a fenced lease and its recorded phase is
  the lower bound to resume from; `replayed`: the operation is already terminal for the identical
  digest and `replayed_result` carries the stored envelope.
- `route_action: Literal["created", "attached"]`.
- `task_id: str`, `session_id: SessionId`, `writer_id: str`, `lifecycle_event_id: str` — all four
  allocated exactly once at first reservation; a retry never invents replacements.
- `bundle_relpath: str` — generated from the validated `task_id`, never accepted from a request.
- `route_generation: int` and `route_identity_digest: str` — the exact active structural route
  generation/identity selected at reservation. A created route starts at generation `1`; attach
  preserves the existing pair. They are immutable for this start operation even if a later
  maintenance switch makes the allocation stale.
- `phase: StartPhase` — the durably recorded phase (a lower bound after a crash).
- `response_object_id: str | None` — absent before `result_published`; the exact pinned result
  object locator at `result_published`/terminal replay.
- `lease: StartOperationLease` — positive `owner_generation: int`, `lease_owner_id: str`,
  `lease_generation: int`, `lease_expires_at: datetime`. Absent (`None`) when
  `outcome == "replayed"`.
- `replayed_result: bytes | None` — the stored `terminal_result_canonical` envelope (structural
  IDs/digests/reason codes only) when terminal; the application deserializes it into the public
  `StartResult`/error without recomputation.

`EncryptedResultRef` fields: `response_object_id: str` (the finalized encrypted start-result
object), `result_canonical: bytes` (the safe structural terminal envelope to store in the
catalog), `result_digest: str`.

`TaskRoute` contains only `task_id`, active `session_id`, generated `bundle_relpath`, positive
`route_generation`, `state: TaskRouteState`, and `route_identity_digest`. The digest is the stored
`active_route_identity_digest` and MUST equal SHA-256 over the canonical structural map
`{"task_id": task_id, "bundle_relpath": bundle_relpath, "route_generation": route_generation}`.
Session and state are deliberately outside the digest: attach changes the active session and
activation changes state without changing the physical route identity. It contains no attachment
commitments, raw refs/title/path, timestamps, or quarantine details.

`StartCompletionEvidence` is produced only by `BundleRuntimePort.verify_start` after independently
re-reading the exact bundle/lifecycle/result state. It is structural and safe, but is valid only
for its named current bundle generation.

### `reserve_or_resume`

Implements steps 1–3 of the crash-safe start workflow in
`specs/src/yoetz/application/start.md` in one catalog `BEGIN IMMEDIATE`-equivalent atomic section:

1. Look up `(installation_id, operation_id)`. The installation ID is engine state generated and
   stored by Yoetz itself, never accepted from the caller.
2. Existing row, different `request_digest` → raise
   `PublicOperationError(IDEMPOTENCY_CONFLICT)`.
3. Existing terminal row, same digest → return `outcome="replayed"` with the stored envelope
   (`complete` rows return the success envelope; `quarantined` rows return the stable safe
   quarantine envelope — the application converts it to the corresponding public error).
4. Existing `pending` row, same digest: lease with current catalog owner generation AND unexpired
   expiry → raise `PublicOperationError(OPERATION_PENDING)` (another owner is live). Expired
   expiry OR stale owner generation → fenced compare-and-swap reclaim: increment
   `lease_generation` under the current catalog owner generation, set new owner/expiry, return
   `outcome="resumed"` at the recorded phase. Wall-clock expiry never revives a stale generation.
5. No row: resolve the mode against the unique scoped attachment index —
   - `create`: an existing route for the commitment pair → raise
     `PublicOperationError(SESSION_CONFLICT)`. A keyless `create` (no refs) is allowed; later
     attachment then requires the returned session ID.
   - `attach`: no exact match by commitment pair or supplied session ID → raise
     `PublicOperationError(SESSION_NOT_FOUND)`.
   - `create_or_attach`: attach on the one exact match, otherwise create. Never fuzzy matching,
     never enumerable candidate lists.
   - Supplied `session_id` and commitment pair resolving to different routes → raise
     `PublicOperationError(SESSION_CONFLICT)`.
   Then allocate `task_id`/`session_id`/`writer_id`/`lifecycle_event_id` once (for `attached`,
   reuse the route's task, allocate a fresh session/writer/lifecycle event, and preserve the
   route generation/identity),
   insert (or leave) the `task_routes` row (`initializing` for created routes), insert the
   `pending/route_reserved` operation row with lease fields, commit, and return
   `outcome="reserved"`.

### `resolve_route`

Bounded, read-only point lookup by already validated `session_id`. It returns the one exact
`task_routes` row as `TaskRoute`, including its positive route generation and stored active route
digest, plus `initializing`/`quarantined` state so the runtime can distinguish retryable start
progress from unsafe state, or `None` when absent. It recomputes the route digest from
`task_id`/`bundle_relpath`/`route_generation`, constant-time compares the stored value, and never
returns workspace/external commitments.
It performs no lease acquisition, state transition, directory access, fallback, candidate
enumeration, or lookup by title/cwd/path. More than one physical match, malformed IDs/route, or a
digest/canonicalization contradiction is `STORAGE_CORRUPT` even if SQLite constraints should have
made it impossible.

### `advance_phase`

Single short atomic CAS: verify the allocation's lease is still valid (owner generation current,
lease owner/generation match, unexpired), verify the requested phase is the direct successor of
the recorded phase in the fixed order `route_reserved → bundle_ready → lifecycle_committed →
result_published`, update `phase` and `updated_at`, and return the refreshed allocation. `result`
must be absent for the first two transitions and is required for `lifecycle_committed →
result_published`; that CAS stores its `response_object_id` in the catalog row. The safe terminal
bytes/digest remain null until `complete`, as required by the pending-row CHECK. A resume at
`result_published` returns the pinned locator and must reopen/revalidate that exact task object; it
never rebuilds or substitutes a newly encrypted result. Phase
regression, skipping, or a lost lease raises `PublicOperationError(OPERATION_PENDING)` (another
owner) or `INTERNAL_ERROR` with a bounded code for contradiction. A recorded phase is a lower
bound: the application MUST revalidate durable state (bundle exists, lifecycle event present,
object durable) before advancing past it, as required by this port's crash/resume contract.

### `complete`

Implements the final start commit in one catalog transaction: verify the lease and route; compare
every allocation/result ID/digest against `StartCompletionEvidence`; require its milestone to be
`result_published`, its ownership generation to match the still-held runtime fence that
`BundleRuntimePort.verify_start` validated immediately before entering this transaction, and its
evidence digest to recompute from the supplied structural fields; verify response object and
`result_digest` consistency; store
`terminal_result_canonical`/`terminal_result_digest`/`response_object_id`; clear all lease
fields; flip the route from `initializing` to `active` (created routes); set
`complete/terminal` with `terminal_at`. Only after this commit may the application acknowledge.
Catalog completion without a validated bundle is forbidden.

### `quarantine`

One catalog transaction: verify lease ownership; store the stable safe terminal envelope and the
allowlisted `reason.code` as `quarantine_code`; clear lease fields; set `quarantined/terminal`.
Used only for corruption, invariant violation, or irreconcilable durable-state ambiguity — never
for ordinary validation failures (those are raised before reservation) and never for provider or
key-backend unavailability.

## Errors and edge cases

- Expected failures: `IDEMPOTENCY_CONFLICT`, `OPERATION_PENDING`, `SESSION_CONFLICT`,
  `SESSION_NOT_FOUND`, `BUNDLE_BUSY` (catalog contention past busy timeout), `STORAGE_UNSAFE`,
  `STORAGE_CORRUPT`, `MIGRATION_REQUIRED` — all as `PublicOperationError`.
- `resolve_route` returns `None`, rather than raising, only for exact absence; busy/storage/schema
  failures retain their classifications and never masquerade as absence.
- Crash between `reserve_or_resume` and `complete`: the pending row plus recorded phase makes the
  retry resume, never reallocate. Expected absence of downstream state at `route_reserved` is
  recoverable; a `result_published` row pins the exact response object for reuse; contradiction
  (e.g. a bundle exists with a different session) → `quarantine`.
- Two processes racing the same `operation_id`: exactly one holds a valid lease; the loser gets
  `OPERATION_PENDING`.
- `quarantine_code` and `SafeReason.code` are bounded reviewed enums; exception strings never
  enter the catalog.
- No raw `workspace_ref`/`external_ref`, task title, or any user plaintext in catalog rows,
  errors, or logs (plaintext-canary tested).
- A stale, missing, bundle-scoped, log-correlation, or privacy-audit handle fails before catalog
  lookup/mutation; there is no fallback commitment.

## Invariants

1. One `(installation_id, operation_id)` maps to at most one allocation forever; retries resume
   or replay it, never duplicate it.
2. The commitment pair is unique per installation (partial unique index); `create_or_attach`
   creates exactly once or returns the one protected route.
3. IDs are allocated exactly once, before any bundle side effect, inside the reservation
   transaction.
4. Phase advances monotonically through the fixed order; `terminal` is reached only via
   `complete` or `quarantine`.
5. Start operation keys/routes are retained for the supported installation-data lifetime; v0.1
   never silently expires or reassigns them.
6. The catalog and task bundles share the generation-fenced single-authoritative-process rule
   (ADR-001); a stale generation invalidates all catalog leases immediately.
7. Runtime routing has one structural catalog read boundary; it never reaches into catalog SQL or
   derives a route from ambient filesystem state.
8. `route_identity_digest` binds task, generated bundle route, and route generation exactly; a
   session/state transition does not mutate it, while a restore route switch must advance the
   generation and digest together.
9. The sole installation `K_lookup` crosses the catalog boundary only as a purpose/domain-bound
   `MacKeyHandle`; raw key bytes never do.

## Tests

- `specs/tests/conformance.md`: independent reference-model state machine for the catalog
  (reserve/resume/complete/quarantine sequences), run against memory and SQLite adapters.
- `specs/tests/subprocess.md`: kill matrix over every phase boundary; same-key retry after each
  kill returns the same IDs; two-process race with exactly one winner.
- `specs/tests/integration.md`: canary workspace/external refs and titles never appear in
  `catalog.sqlite3`, WAL, logs, or errors.
- `specs/tests/integration.md`: wrong-purpose/domain and stale handles fail with bounded reasons,
  no commitment output, and no catalog mutation.
- Known-answer vectors encode the trailing `\x00` domain byte explicitly and fail if an adapter
  hashes the display string without it.
- `specs/tests/conformance.md`: exact active/initializing/quarantined/absent `resolve_route` parity,
  stable route digest, and no commitment disclosure.

## Open questions

None.
