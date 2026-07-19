# src/yoetz/ports/runtime.py — service-owned generation-fenced task routing

**Wave:** B/C | **ADRs:** ADR-001, ADR-003, ADR-004, ADR-008 | **Imports (spec-tree):**
`protocol/errors.md`, `protocol/ids.md`, `ports/ledger.md`, `ports/objects.md`,
`ports/importer.md`, `ports/diagnostics.md`, `version.md` | **Imported by:**
`application/service.md`, `application/start.md`, `adapters/runtime.md`

## Purpose

Defines the ready service's boundary for provisioning and routing exact task bundles. It owns one
service-lifetime generation-fenced runtime per open task and returns least-authority task-scoped
ports without exposing a path, SQLite connection, key/credential handle, recovery object, or
concrete adapter. CLI/MCP/UI never implement or receive this port.

## Public surface

- `class BundleRuntimePort(Protocol)` with async `provision_start`, `route`, `verify_start`,
  `release`, and `close`.
- `@dataclass(frozen=True, slots=True) class ServiceRuntimeContext` — exactly
  `service_instance_id`, positive `service_generation`, `vault_generation`, `catalog_generation`,
  `capabilities: frozenset[RuntimeCapability]`, immutable structural `version_manifest`, and an
  opaque `shutdown_token`; nonserializable and constant-redacted.
- `enum RouteAccess` — `structural_read`, `payload_read`, `write`, `import_review`, `maintenance`.
- `@dataclass(frozen=True, slots=True) class RouteCommand` — exactly `session_id`, optional
  `writer_id`, `access`, and `required_capabilities`; no client kind/path/fuzzy reference.
- `enum BundleProvisionMode` — `created`, `attached`.
- `@dataclass(frozen=True, slots=True) class BundleProvisionCommand` — exactly `mode`, `task_id`,
  `session_id`, `writer_id`, `lifecycle_event_id`, generated `bundle_relpath`, positive
  `route_generation`, `route_identity_digest`, `phase`, optional `response_object_id`, positive
  `owner_generation`, `lease_owner_id`, positive `lease_generation`, `lease_expires_at`, and exact
  `protocol_version`, `engine_version`, `projection_version`, `bundle_schema_version` identities.
- `@dataclass(frozen=True, slots=True) class OwnershipFence` — exactly `service_instance_id`,
  positive `service_generation`, positive monotonic `owner_generation`, and opaque `nonce`;
  constant-redacted.
- `@dataclass(frozen=True, slots=True) class TaskRuntime` — exactly `task_id`, `session_id`, optional
  `writer_id`, admitted `capabilities`, least-authority `ledger`, `objects`, and `importer` ports,
  `projection_version`, `engine_version`, `protocol_version`, `bundle_schema_version`, and current
  `fence`; no keys/paths.
- `enum StartMilestone` and frozen `StartMilestoneExpectation`/
  `StartCompletionEvidence` with the existing bundle-ready/lifecycle-committed/result-published
  semantics.

`StartMilestoneExpectation` carries exactly the milestone plus task/session/writer/lifecycle-event
identity, route generation/digest, and optional response-object/envelope-digest/result-digest
triple.
`StartCompletionEvidence` carries the same structural identity plus owner generation, optional
lifecycle frontier, optional response-object/envelope-digest/result-digest triple, and a canonical
evidence digest.

The removed per-client `RuntimeScopeKind`/`mcp_service`/one-shot CLI scope is not part of v0.1.
Client authorization is enforced by service control before application entry; this port sees only
the service's fixed authority ceiling and an admitted request.

## Behavior

### Service lifetime and exact routing

Construction requires a ready `ServiceRuntimeContext` whose vault/service generations match the
daemon. It opens the installation catalog but no task bundle. `route`:

1. validates exact session/writer/access/capability and current ready generations;
2. resolves the one catalog route, never cwd/path/title/workspace/fuzzy candidates;
3. verifies route digest, private no-follow local path, schema/recovery/object/projection versions;
4. asks the service vault for an opaque bundle-key handle only for payload/write access;
5. for write/import/maintenance, acquires/revalidates bundle generation CAS and creates exactly one
   service-owned writer queue/connection; all client requests coalesce onto it;
6. verifies writer membership and returns a fresh least-authority usage facade over the cached
   service runtime.

Read-only facades cannot dynamically reach mutators. A warm route rechecks service/vault/bundle
generations and capability every time. Generation loss or relock poisons the cache entry
immediately; it is never silently reacquired inside an admitted operation.

### Crash-safe start provisioning

`provision_start` accepts only a current catalog allocation copied into
`BundleProvisionCommand`. For create it validates/resumes the exact bootstrap marker, asks the
ready vault to load then create exactly one BMK record only on proven first absence, initializes
the exact bundle schema/writer, atomically publishes the route, acquires generation, and removes
the marker only after durable verification. It never creates a replacement key over partial
ciphertext. Attach opens only the allocated exact existing route and idempotently creates/verifies
the allocated writer membership.

Expected absence after route reservation is retryable. Foreign marker, route/session/key-slot,
event, or object identity is contradiction/quarantine. Keyring/vault lock, disk failure,
cancellation, or contention leaves the start pending.

### Milestone evidence

`verify_start` rereads current durable bundle, lifecycle event, and result object facts under the
same service/bundle generation, producing only structural `StartCompletionEvidence`. Catalog
completion compares that proof immediately while the service still owns the fence. No client,
cached result, or catalog phase can assert bundle facts.

### Cache, relock, and close

The service lazily retains at most eight idle task runtimes and four concurrent cold opens. Cache
saturation yields bounded `BUNDLE_BUSY`, never unbounded queues. `release` closes one logical usage
reference, not the shared runtime. Lifecycle relock/close stops admission, resolves admitted
shielded commits, closes importer/ledger/object/writer/read handles, clears vault handles, releases
bundle generations, and empties the cache. A crash relies on successor validation and generation
advance, not cleanup.

## Errors and edge cases

- Missing route → `SESSION_NOT_FOUND`; session/writer mismatch → `SESSION_CONFLICT`; bounded
  contention → `BUNDLE_BUSY`; unsafe/generation/vault state → `STORAGE_UNSAFE` with bounded reason;
  contradiction → `STORAGE_CORRUPT`; newer schema → `MIGRATION_REQUIRED`.
- Service/vault generation mismatch or locked/draining state rejects before route IO.
- A client disconnect releases only its application usage when dispatch completes; it never closes
  the shared runtime or writer.
- Missing bundle key for existing state never creates a new one or returns empty payload.
- Errors contain no path, cwd, title, raw ref, username, key locator, SQL, payload, or exception.

## Invariants

1. Only the ready per-user service owns this runtime and one writer per open bundle.
2. Every mutator checks service, vault, and bundle generations inside its durable section.
3. Every route is exact/catalog-derived; no client can select storage or receive a concrete handle.
4. New-bundle provisioning is idempotent and never replaces an existing key/state identity.
5. Relock invalidates all task runtimes before the service reports locked.
6. No write-capable task runtime exists until the full service/task safety gate passes.

## Tests

- `tests/unit/service/test_runtime_context.py` covers generation/capability/route validation.
- `tests/integration/service/test_multi_client_single_writer.py` proves concurrent clients share
  one bundle writer/key context.
- `tests/integration/storage/test_owner_generation.py` covers crash takeover and stale service
  rejection.
- `tests/subprocess/test_process_owner_fencing.py` races services, not CLI/MCP owners.
- `tests/integration/service/test_locked_ready_transitions.py` proves relock invalidation.

## Open questions

None.
