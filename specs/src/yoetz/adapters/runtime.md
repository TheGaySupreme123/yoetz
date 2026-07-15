# src/yoetz/adapters/runtime.py — ready-service local bundle runtime

**Wave:** C/D | **ADRs:** ADR-001, ADR-003, ADR-004, ADR-008 | **Imports (spec-tree):**
`ports/runtime.md`, `ports/start_catalog.md`, `ports/diagnostics.md`, `service/vault.md`,
`config/paths.md`, SQLite/object/importer adapters, `version.md` | **Imported by:**
`service/daemon.md`, runtime integration/conformance tests

## Purpose

Implements `BundleRuntimePort` inside the ready persistent service. It joins exact catalog routes
to private task bundles, performs every path/schema/recovery/vault/generation gate, and coalesces
all CLI/MCP/UI calls onto one service-owned writer runtime per task.

## Public surface

- `class LocalBundleRuntime(BundleRuntimePort)` with the exact port methods.
- `@dataclass(frozen=True, slots=True) class RuntimeAdapterFactories` — service-internal verified
  constructors for object, ledger, importer, recovery, and read views; key operations are supplied
  only through `VaultService`, never a client factory.
- `@dataclass(frozen=True, slots=True) class RuntimeCachePolicy` — defaults
  `max_idle_tasks=8`, `max_opening_tasks=4`.
- `async open_local_bundle_runtime(context: ServiceRuntimeContext, catalog, vault, factories,
  diagnostics, versions, cache_policy) -> LocalBundleRuntime`.

Private cache/single-flight/ownership types are nonserializable and constant-redacted.

## Behavior

Construction validates current ready service/vault/catalog generations, freezes the service's
capability ceiling, and opens no task. Cold routes are single-flight by exact task ID. The opener:

1. resolves/recomputes exact catalog route identity and private no-follow path;
2. performs read-only schema/version/recovery/object/projection inspection;
3. obtains an opaque bundle-key handle from the ready vault only when required;
4. acquires generation CAS before any writer connection;
5. constructs exactly one writer thread, encrypted object store, ledger repository, and importer
   carrying the same fence;
6. verifies writer/session membership and generations immediately before atomic cache publication.

Followers see one complete entry or one sanitized failure. Warm routes recheck ready/vault/bundle
generations and least authority. Reads may share a write entry through a read-only facade; writes
never upgrade a read-only facade in place. Cache eviction closes only zero-usage/noncommitting idle
entries. No CLI/MCP process owns or closes an entry.

New-bundle provisioning preserves the exact resumable staging/bootstrap algorithm, but BMK
load/create occurs through `VaultService`. Key absence allows creation only for a proven fresh
allocation; existing/partial ciphertext with a missing/mismatched record fails closed. Attach
never creates a key/schema. Start milestone verification rereads authenticated event/object facts
under the current service/bundle fence.

Relock poisons all entries, stops admission, cancels noncommitting work, resolves shielded commits,
closes importer/ledger/object/writer/read handles, invalidates vault handles, releases generations,
and clears cache. If the lifecycle lock deadline cannot prove all secret consumers closed, daemon
termination replaces a false locked claim.

## Errors and edge cases

- Service/vault not ready or stale generation rejects before filesystem/catalog route IO.
- Missing route/session mismatch/contention/version/storage errors preserve port mappings.
- Failure after generation acquisition closes partial adapters but does not decrement generation;
  successor acquires a newer one.
- A cached path/key decision is never permanent authority.
- Safe diagnostics contain IDs/reasons/versions only, never path/ref/key locator/payload/exception.

## Invariants

1. One service owns at most one writer entry per task; all client requests share it.
2. Every task route is catalog-derived and byte-exact; no cwd/directory scan/fuzzy fallback.
3. No task facade survives vault relock or service-generation change.
4. Cache behavior changes resource use only, never authority/routing/recovery semantics.
5. Start creation never overwrites foreign directory/key/writer/event/object state.

## Tests

- `tests/integration/service/test_multi_client_single_writer.py` covers shared routing/writers.
- `tests/integration/service/test_locked_ready_transitions.py` covers relock poisoning/cleanup.
- `tests/integration/storage/test_start_catalog_state_machine.py` covers crash-safe create/attach.
- `tests/subprocess/test_process_owner_fencing.py` races two daemon generations and stale writes.
- `tests/packaging/test_platform_and_sqlite_gate.py` covers certified installed profiles.

## Open questions

None.
