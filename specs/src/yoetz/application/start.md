# src/yoetz/application/start.py — crash-safe create/attach/resume orchestration

**Wave:** D | **ADRs:** ADR-001, ADR-002, ADR-003, ADR-004 | **Imports (spec-tree):**
`protocol/models.md`, `protocol/canonical.md`, `protocol/errors.md`, `domain/events.md`,
`domain/values.md`, `ports/start_catalog.md`, `ports/ledger.md`, `ports/objects.md`,
`ports/runtime.md`, `ports/clock.md`, `application/unit_of_work.md` | **Imported by:**
`application/service.md`

## Purpose

`start` creates a task/session or attaches a new writer to exactly one existing route. It bridges
pre-writer catalog idempotency and the task bundle without ever allocating replacement IDs after a
crash. This file owns the seven-step workflow specified below; catalog SQL, bundle paths, key
backend details, and transport rendering remain behind ports/composition.

## Public surface

- `async execute_start(app: Application, request: StartRequest) -> StartResult` — implementation
  behind `Application.start`.

`StartRequest`, `StartResult`, compact view types, and wire conversion are defined by
`protocol/models.py`; this file exports no second request/result schema.

## Behavior

### Request semantics

The request carries protocol/schema/request ID, `mode` (`create`, `attach`,
`create_or_attach`), optional consistency `session_id`, task title, both-or-neither stable
`workspace_ref`/`external_ref`, actor/client, and `requested_view=compact`. A mutable path, branch,
commit, or tree digest is not attachment identity. Cross-field rules are enforced before any
catalog reservation:

- `create` may be keyless, but later attach then requires the returned session ID;
- `attach` requires either the exact commitment pair or a session ID;
- a supplied session ID and commitment pair must resolve to the same route;
- raw title/references are user content and may enter only encrypted bundle objects/events;
- MCP actor assurance is clamped to `self_asserted`; no display name upgrades identity.

Construct a redacted one-shot `StartIdentityInput` from the title/workspace/external references and
call `start_catalog.commit_identity` before hashing the request. Build the canonical request digest
from those returned domain-separated commitments plus the nonsecret logical request fields; never
place the low-entropy raw values in an unkeyed digest. Then build `StartCommand` with the operation
ID, mode/session guard, identity input, exact commitments, and digest. The digest excludes transport
framing, generated route/path, random encryption, and later ledger-assigned fields. Raw identity
input crosses only this catalog boundary, is independently recommitted before routing, and is never
logged or persisted in the catalog.

### State machine

1. Call `start_catalog.reserve_or_resume(command)`.
   - `replayed`: decode and validate the stored terminal envelope and return the original success
     or raise its stored safe terminal error. Perform no bundle/object work.
   - `reserved`/`resumed`: retain the exact allocated task/session/writer/lifecycle-event IDs and
     lease. Never generate replacements.
2. Treat `allocation.phase` as a lower bound, not proof. Project the allocation's structural
   fields/lease, including the exact route generation/identity digest, into
   `BundleProvisionCommand` and call `app.runtime.provision_start`; it
   revalidates every durable fact and returns the task-scoped `TaskRuntime` before advancing:
   - for `route_action=created`, ensure the generated bundle route is private/empty or identically
     initialized, create bundle keys exactly once, initialize schema/meta/writer rows with the
     allocated IDs, and open under the current owner generation;
   - for `attached`, locate only the allocated route, validate bundle/task/session identity,
     schema/build/key/object/projection state, and add/validate the allocated writer stream;
   - a missing expected downstream artifact is recoverable at the phase that precedes it;
     contradictory identity/content is quarantinable.
3. Call `app.runtime.verify_start(..., milestone=bundle_ready)` and only after that evidence is
   returned CAS `route_reserved → bundle_ready`, replacing the current allocation with
   `start_catalog.advance_phase`'s return value.
4. Build the allocated lifecycle event:
   - created route: `session_opened` with encrypted title/raw refs, client identity/integration,
     and active profile;
   - attached route: `session_resumed` with client/profile and the exact current resumed frontier;
   - envelope author is `yoetz_engine`; the caller assertion is contextual payload metadata only.
   Canonicalize/finalize through `TaskRuntime.objects`, then append through
   `TaskRuntime.ledger` idempotently with the allocated event ID/writer and start request ID. An
   identical accepted event is resume; a different event under the allocation is contradiction.
   Re-verify the accepted event through `app.runtime.verify_start(...,
   milestone=lifecycle_committed)` and CAS `bundle_ready → lifecycle_committed` only after the
   append commit/evidence is verified, again retaining the returned allocation.
5. Load/build the bounded **structural-only** compact projection at the lifecycle result frontier:
   task/current-plan refs, counts of open obligations and unresolved findings, freshness/coverage
   enums and closed gap codes, versions, and allocated IDs/frontiers. `StartResult` never echoes a
   title, description, finding summary/detail, evidence excerpt, path, external/workspace ref, or
   other user text; clients use bounded `status` pages for separately projected content.
6. Canonically encode the full structural start result, stage/finalize it as
   `ObjectKind.start_result`, and create `EncryptedResultRef` with the safe structural terminal
   envelope (IDs/digests/reason codes only). CAS `lifecycle_committed → result_published`, passing
   that ref so the same transaction stores its `response_object_id`, after the object is durable.
   Retain the returned allocation. On resume at `result_published`, reopen and validate its pinned
   object and rebuild the same structural ref; never re-encrypt a replacement.
7. Call `app.runtime.verify_start(..., milestone=result_published)` with the lifecycle acceptance
   and `EncryptedResultRef`, then pass the returned `StartCompletionEvidence` to
   `start_catalog.complete`. The catalog transaction compares allocation/result/evidence, rechecks
   its route/lease, activates a created route, stores the structural terminal envelope, clears the
   lease, and sets `complete/terminal`. Return only after this catalog commit. Always release the
   task-runtime usage reference in the operation's lifecycle cleanup; release never cancels an
   admitted commit.

On resume at any later phase, repeat the relevant verification and reuse the already durable
event/object. Never trust phase alone and never append a second lifecycle event.

### Failure classification

- Request/mode/identity conflicts before reservation are ordinary public errors and create no
  catalog row.
- Storage/key/provider-independent transient failures after reservation leave the operation
  pending for same-ID retry; they are not corruption and do not quarantine merely because work is
  incomplete.
- Only impossible ID/route/bundle/event/object disagreement, verified corruption, or unsafe durable
  ambiguity calls `start_catalog.quarantine` with an allowlisted `SafeReason` and stable terminal
  envelope.

## Errors and edge cases

- Exact mode mappings: existing route on `create` → `SESSION_CONFLICT`; no route on `attach` →
  `SESSION_NOT_FOUND`; inconsistent session/key → `SESSION_CONFLICT`.
- Same request ID/different logical request → `IDEMPOTENCY_CONFLICT`; live lease →
  `OPERATION_PENDING`; catalog/bundle contention → `BUNDLE_BUSY`.
- Unsupported path/build/key/schema maps through `STORAGE_UNSAFE`/`MIGRATION_REQUIRED`; integrity
  mismatch is `STORAGE_CORRUPT`. No raw task/ref/path/key value appears in errors.
- Cancellation after reservation leaves a resumable phase. Cancellation after completion but before
  delivery is resolved by repeating the same request ID and replaying the stored result.
- `start_catalog.complete` failure after result publication leaves a pinned unacknowledged result;
  retry reopens/reuses/verifies that exact object.

## Invariants

1. One installation/request ID maps forever to one allocation and terminal result.
2. The exact attachment pair creates once or returns the one protected route; no fuzzy matching or
   enumeration exists.
3. Catalog completion is impossible without a validated bundle, lifecycle event, and durable
   encrypted result object.
4. Raw title/workspace/task references never enter catalog structural rows, logs, or safe errors.
5. Reattach reports current recorded state and gaps; it never fabricates work from conversation
   resume.
6. Acknowledgement occurs only after the terminal catalog commit.
7. Start success is structural-only; adding content requires an explicit result-field registry and
   local-disclosure contract change.

## Tests

- `specs/tests/conformance.md`: create/attach/create_or_attach/keyless/session-guard matrix,
  replayed terminal result, identical IDs across memory/SQLite reference models.
- `specs/tests/subprocess.md`: kill at every seven-step boundary and response write; same-ID retry
  returns one route/event/result.
- `specs/tests/integration.md`: key locked/missing, schema mismatch, corrupt lifecycle/object,
  projection resume, two-process reservation race.
- `specs/tests/integration.md`: title/raw refs absent from catalog/WAL/logs/errors.

## Open questions

None.
