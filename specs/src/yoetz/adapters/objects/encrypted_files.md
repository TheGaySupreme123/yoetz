# src/yoetz/adapters/objects/encrypted_files.py — encrypted object files and GC-safe publication

**Wave:** C | **ADRs:** ADR-003, ADR-004 | **Imports (spec-tree):**
`adapters/objects/envelope.md`, `ports/objects.md`, `adapters/sqlite/recovery.md`,
`config/paths.md`
**Imported by:** `application/*`, `adapters/sqlite/repository.md`, `adapters/sqlite/recovery.md`

## Purpose

This file implements the object-store port on top of the local filesystem. It is responsible for
safe staging, atomic publication, verified reads, and conservative orphan cleanup. It is not a
generic file manager.

The important design constraint is that publication must never create a partially trusted object.
An object is either still private in staging, or durably published and discoverable by reference.
There is no middle state that callers are allowed to treat as usable.

## Public surface

| Name | Signature (natural language) |
|---|---|
| `EncryptedFilesObjectStore` | filesystem-backed implementation of `ObjectStorePort` |
| `commitment_for(...)` | compute the non-publishing keyed logical commitment |
| `stage(...)` | write a temporary encrypted object file in a private staging location |
| `finalize(...)` | atomically rename the staged object into durable storage |
| `open_verified(...)` | stream a verified object’s plaintext bytes after envelope and key checks |
| `sweep_orphans(root_snapshot: ObjectRootSnapshot, now) -> int` | remove only generation-proven unrooted temp/finalized objects and return the count |

## Behavior

`commitment_for(...)` performs no filesystem write, ID allocation, or encryption and uses the
same domain/key as `stage(...)`. `stage(...)` writes a new temporary file under the bundle’s object root with owner-only
permissions. It encrypts the source payload using the envelope module and writes the full envelope
to a temp path in the destination filesystem. The method records enough metadata to complete the
publish later, but does not make the object visible yet.

Staging requirements:

- the temp file lives on the same filesystem as the final object path when atomic rename is relied
  on;
- the temp file name is non-secret and collision-resistant;
- the staging path is not reused across concurrent publications;
- the file descriptor is closed only after the data is flushed and durability has been requested.

`finalize(...)` performs the publish protocol:

1. flush Python buffers;
2. fsync the temp file;
3. atomically rename into the durable object path;
4. fsync the containing directory where the platform supports it;
5. return the durable object reference.

Finalization is idempotent only for the same opaque `StagedObject` handle: retry after an ambiguous
rename verifies the exact destination identity and returns that same `ObjectRef`, or fails cleanly.
Re-staging the same logical source is a new publication with a fresh object ID, DEK, payload nonce,
envelope bytes, and envelope digest; it may leave a safe orphan and must never be deduplicated by
plaintext. No retry silently publishes to an unrelated target.

`open_verified(...)` is read-side only. It resolves the object reference, recomputes the complete
frame `envelope_digest`, structurally parses the envelope, compares every authenticated header field
to the reference, unwraps/authenticates through the key backend, and only then yields plaintext in
bounded chunks. If the
object is missing, tampered, or unreadable with the current key classification, the read fails
closed.

Read-path requirements:

- the object reference is resolved before any decryption attempt;
- the full frame digest, task/kind/creation metadata, header AAD, GCM tag, length, and plaintext
  commitment are validated before any plaintext is yielded;
- chunking is bounded so a single object cannot exhaust memory;
- the caller gets a clean failure if the key is unavailable, wrapped, revoked, or classified as
  incompatible for that object.

`sweep_orphans(...)` removes only temp/finalized objects proven absent from one complete
`ObjectRootSnapshot`. That snapshot unions task-ledger inventory, importer rows, installation-catalog
privacy-audit roots, and active maintenance pins for the exact task/route. Before each deletion the
adapter revalidates bundle/route generation plus the privacy-root generation/digest; any change
aborts the sweep. A catalog-rooted `privacy_audit` object is live even though no task-ledger
inventory row exists. The method must respect the bundle’s safety window and never delete a file
that may still belong to an active or just-crashed publication.

Sweep requirements:

- only files in known staging locations are candidates;
- any object ID in any owning root source or pin is retained;
- age is a hint, not the only proof, when a crash marker is present;
- sweeps are conservative under clock skew and filesystem timestamp vagaries.

The v0.1 stale-orphan safety window is exactly 24 hours. Younger candidates are retained even when
otherwise unreferenced. Object files use ADR-003's one-level shard derived from the first two
lowercase hexadecimal UUID characters after `obj_`: `objects/<2-hex-prefix>/<object_id>`.

The object store never becomes a second source of truth. It stores encrypted blobs and object
metadata, while task SQLite, importer rows, and the privacy catalog own their exact durable
references. Ledger-only reachability is insufficient for collection.

The object path layout is derived from the object identity rather than from payload text. It may
not change the logical object reference or introduce a second v0.1 sharding layout.

## Errors and edge cases

- A locked or missing key fails closed; the object is not treated as empty.
- A finalize failure after fsync but before its owning ledger/importer/catalog reference can leave an orphan; that is acceptable
  and must be swept conservatively later.
- A rename failure or directory fsync failure is a publish failure, not a silent success.
- `open_verified(...)` never returns plaintext from an object that failed validation.
- A stale staging file must never be promoted if the destination identity no longer matches the
  source metadata recorded during `stage(...)`.
- Missing directory fsync support is a platform limitation, not a license to skip durability
  entirely.

## Invariants

1. Publish is temp → fsync → rename → dir fsync.
2. Object references become live only after their owning ledger/importer/privacy-catalog record
   commits; a finalized-but-unowned object remains an orphan.
3. Reads verify before trust.
4. Orphan cleanup never deletes active data.
5. Object plaintext never appears in logs, paths, or exception text by default.
6. The staging directory is not scanned broadly; only known bundle paths are touched.
7. Verified reads are side-effect free.
8. A privacy-catalog `ObjectRef` is an explicit live root; missing ledger inventory never makes it an
   orphan.
9. Root/generation drift aborts collection before deletion.

## Tests

- `tests/integration/objects/test_envelope_and_encrypted_files.py` — publish, rename, readback,
  corrupt-envelope rejection, and collision-safe staging-path derivation.
- `tests/integration/objects/test_redaction_and_gc.py` — orphan cleanup, age-window behavior, and
  revoked/redacted access denial, privacy-catalog roots, and generation races.
- `tests/integration/objects/test_key_backends.py` — missing/wrong/revoked-key read failures.
- `tests/conformance/adapters/test_object_store_port.py` — in-memory vs filesystem behavior parity.

## Open questions

None.

E-005 is the sole central storage-performance gate.
