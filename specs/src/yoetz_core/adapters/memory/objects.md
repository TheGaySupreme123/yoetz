# src/yoetz_core/adapters/memory/objects.py — in-memory object-store reference adapter

**Wave:** C | **ADRs:** ADR-003, ADR-004 | **Imports (spec-tree):** `ports/objects.md`,
`adapters/objects/envelope.md`
**Imported by:** conformance tests and object-publication fixtures

## Purpose

This file implements the object store port in memory so the conformance suite can exercise
publication sequencing, reads, and failure handling without depending on the filesystem or key
backend.

## Public surface

| Name | Signature (natural language) |
|---|---|
| `MemoryObjectStore` | in-memory implementation of `ObjectStorePort` |
| `commitment_for(...)` | compute the non-publishing keyed logical commitment |
| `stage(...)` | stage an object in memory with the same metadata rules as the durable store |
| `finalize(...)` | promote a staged object to a durable reference in the reference model |
| `open_verified(...)` | return the plaintext bytes for a verified object reference |
| `sweep_orphans(root_snapshot, now)` | apply the same owning-root, age-window, and generation fences as durable GC |

## Behavior

The memory object store mirrors the durable object-store protocol but keeps the data in process
memory. It exists so tests can isolate the protocol semantics from file-system durability.

`commitment_for(...)` produces the same domain-separated commitment as the durable adapter and
allocates/publishes nothing. `stage(...)` recomputes it independently.

`stage(...)` records a staged object plus the exact RFC 3394/AES-GCM envelope metadata, subject to
the same size, domain-commitment, fresh-identity/key/nonce, and header constraints as the durable
adapter. Logical-repeat publications may differ in generated identity/envelope digest; conformance
compares their stable plaintext commitment and contract shape unless randomness is injected.

`finalize(...)` returns a stable object reference and makes the staged object visible to reads.

`open_verified(...)` yields the verified object bytes only after the same envelope and metadata
checks the durable adapter performs.

`sweep_orphans(...)` treats task-ledger inventory, importer rows, catalog privacy roots, and active
maintenance pins as one authoritative `ObjectRootSnapshot`. It retains catalog-rooted
`privacy_audit` objects without requiring ledger inventory, honors the exact 24-hour safety window,
and aborts without deletion when route, bundle, privacy-root generation/digest, or another source
digest changes. It returns only the count of removed in-memory objects.

The in-memory store must not bypass the encryption/envelope rules just because it is not writing to
disk. The test corpus depends on it behaving like the filesystem-backed store with respect to
object identity, verification, and failure classification.

## Errors and edge cases

- Staging or finalization failures are modelled explicitly and must not disappear.
- The adapter does not persist across process exit.
- Verified-open failures must match the durable adapter’s public shape.
- A stale/incomplete root snapshot is rejected rather than treated as permission to collect.

## Invariants

1. The in-memory adapter is a protocol oracle, not a relaxed fast path.
2. Reads stay verified.
3. Staged objects become visible only when finalized.
4. Collection parity includes every owning root and the same generation fences as the durable store.

## Tests

- `tests/conformance/adapters/test_object_store_port.py` — memory vs filesystem parity for
  stage/finalize/read, generation-fenced collection, and failure cases.

## Open questions

None.
