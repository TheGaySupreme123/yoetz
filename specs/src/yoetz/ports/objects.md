# src/yoetz/ports/objects.py — ObjectStorePort protocol for immutable encrypted objects

**Wave:** B | **ADRs:** ADR-003 (publication sequencing), ADR-004 (envelope, keys, size policy) |
**Imports (spec-tree):** `protocol/models.md` (size constants), `protocol/errors.md` |
**Imported by:** `application/publish_work.md`, `application/start.md`, `application/check.md`,
`application/receipt.md`, `application/import_review.md`, `adapters/objects/encrypted_files.md`,
`adapters/memory/objects.md`, `adapters/sqlite/repository.md` (resume/case objects)

## Purpose

Every user-controlled plaintext — event payloads, captured command output, evidence content,
semantic cases and raw provider responses, receipts, operation results containing user content —
lives in an immutable encrypted object, never in SQLite structural tables. `ObjectStorePort`
abstracts staging, durable finalization, and verified reading of those objects, so the
application layer can guarantee "no acknowledged event references a missing object" without
knowing about temp files, AEAD framing, or fsync. The two-step stage/finalize split makes the
crash story explicit: a crash after `finalize` but before ledger commit leaves only an orphan
object; a crash before `finalize` leaves only a temp file — neither is ever an acknowledged
dangling reference.

## Public surface

- `class ObjectStorePort(Protocol)`:
  - `async def commitment_for(self, data: bytes, kind: ObjectKind) -> str`
  - `async def stage(self, source: ObjectSource, metadata: ObjectMetadata) -> StagedObject`
  - `async def finalize(self, staged: StagedObject) -> ObjectRef`
  - `async def resolve_verified(self, object_id: str, envelope_digest: str) -> ObjectRef`
  - `def open_verified(self, ref: ObjectRef) -> AsyncIterator[bytes]`
  - `async def sweep_orphans(self, root_snapshot: ObjectRootSnapshot, now: datetime) -> int`
- `@dataclass(frozen=True, slots=True) class ObjectSource` — exactly one of
  `data: bytes | None` or `stream: AsyncIterator[bytes] | None`, plus
  `declared_size: int | None` (required for streams).
- `@dataclass(frozen=True, slots=True) class ObjectMetadata` — `kind: ObjectKind`,
  `media_type: str`, `task_id: str`, `created_at: datetime`.
- `@dataclass(frozen=True, slots=True) class StagedObject` — `object_id: str`,
  `plaintext_size: int`, `commitment: str`, `envelope_digest: str`,
  `encryption_format: Literal["yoetz-object/1"]`, `key_slot: str`, `metadata: ObjectMetadata`,
  plus `staging_handle`, an adapter-opaque temp-location token excluded from repr/comparison and
  never a caller-visible path.
- `@dataclass(frozen=True, slots=True) class ObjectRef` — same fields as `StagedObject` minus the
  staging handle; the durable identity an owning ledger/importer/catalog record may retain.
- `@dataclass(frozen=True, slots=True) class ObjectRootSnapshot` — service-internal GC/maintenance
  proof with exactly `task_id`, `route_identity_digest`, positive `route_generation`, positive
  `bundle_generation`, nonnegative `privacy_root_generation`, `ledger_roots_digest`,
  `importer_roots_digest`, `privacy_roots_digest`, `maintenance_pin_digest`, `captured_at`, and
  sorted unique `live_object_ids`; no path or content.
- `enum ObjectKind` — `event_payload`, `captured_content`, `semantic_case`, `semantic_response`,
  `operation_result`, `start_result`, `check_resume`, `deterministic_result`, `receipt`,
  `import_source`, `import_source_manifest`, `import_plan`, `import_report`, `import_stderr`,
  `import_quarantine`, `capability_evidence`, `privacy_audit`.
- `MAX_OBJECT_HEADER_BYTES = 16 * 1024`.
- `OBJECT_COMMITMENT_DOMAINS: Mapping[ObjectKind, bytes]` — the exact closed table below.

## Behavior

### `resolve_verified`

Resolve only a finalized durable object whose ID and full encrypted-envelope SHA-256 digest both
match the caller's catalog-pinned identity. Verify its envelope, authenticated header, plaintext
size, and keyed content commitment before returning the reconstructed `ObjectRef`. Absence,
staging-only state, digest mismatch, decryption failure, or metadata corruption fails closed. This
bounded START-resume resolver never searches by content, title, path, or partial digest.

### `commitment_for`

Validate the kind and object-size bound, then compute the same domain-separated
`hmac-sha256:<hex>` commitment that `stage` will record, without allocating an object ID, DEK,
nonce, temp file, or durable object. The application uses this bounded preflight to construct the
logical request digest and ask `LedgerPort.lookup_operation` before routine retry publication.
`stage` recomputes and verifies the commitment as the correctness boundary; the preflight is only
an optimization. A concurrent first execution may still leave a safe encrypted orphan.
`ObjectKind.import_stderr` is the one commitment-only domain: `commitment_for` accepts it, while
`stage` rejects it with `ProtocolValueError("commitment_only_object_kind")` so no raw stderr
object can be published in v0.1.

There is no inferred domain-name transform. `OBJECT_COMMITMENT_DOMAINS` is exactly:

| `ObjectKind` | Domain bytes |
|---|---|
| `event_payload` | `b"yoetz/object/event_payload/v1\x00"` |
| `captured_content` | `b"yoetz/object/captured_content/v1\x00"` |
| `semantic_case` | `b"yoetz/object/semantic_case/v1\x00"` |
| `semantic_response` | `b"yoetz/object/semantic_response/v1\x00"` |
| `operation_result` | `b"yoetz/object/operation_result/v1\x00"` |
| `start_result` | `b"yoetz/object/start_result/v1\x00"` |
| `check_resume` | `b"yoetz/object/check_resume/v1\x00"` |
| `deterministic_result` | `b"yoetz/object/deterministic_result/v1\x00"` |
| `receipt` | `b"yoetz/object/receipt/v1\x00"` |
| `import_source` | `b"yoetz/object/import_source/v1\x00"` |
| `import_source_manifest` | `b"yoetz/object/import_source_manifest/v1\x00"` |
| `import_plan` | `b"yoetz/object/import_plan/v1\x00"` |
| `import_report` | `b"yoetz/object/import_report/v1\x00"` |
| `import_stderr` | `b"yoetz/object/import_stderr/v1\x00"` |
| `import_quarantine` | `b"yoetz/object/import_quarantine/v1\x00"` |
| `capability_evidence` | `b"yoetz/object/capability_evidence/v1\x00"` |
| `privacy_audit` | `b"yoetz/object/privacy_audit/v1\x00"` |

For `data` exactly as supplied, commitment is `hmac-sha256:` plus lowercase hex of
`HMAC-SHA-256(K_commit, OBJECT_COMMITMENT_DOMAINS[kind] || data)`. This port performs no implicit
canonicalization; a JSON-owning caller supplies already-canonical UTF-8 bytes, while binary capture
bytes are committed unchanged.

### `stage`

1. Validate `metadata` (bounded `media_type` against the allowlisted
   `application/vnd.yoetz.*+json` and capture media types; valid `task_id`).
2. Enforce the plaintext size cap `MAX_OBJECT_PLAINTEXT_BYTES` (4 MiB, ADR-004 decision 7) *before*
   buffering completes: a bytes source is checked immediately; a stream is counted as it is
   consumed and aborted at cap-plus-one with `LIMIT_EXCEEDED`. Larger artifacts are not chunked
   or streamed in v0.1 — the caller records digest + metadata only, with
   `evidence_immutability = content_digest` coverage.
3. Generate `object_id` (`obj_` + UUIDv4 via `IdPort`) and establish its non-reuse in this bundle
   before encryption; an existing staging or durable identity is a collision and requires a new
   ID. Generate a fresh 256-bit payload DEK plus random 96-bit payload nonce from the OS CSPRNG.
   The DEK performs exactly one AES-256-GCM encryption. Wrap that exact 32-byte DEK through the
   bundle `K_wrap` handle using nonce-free AES-256-KW (RFC 3394), producing exactly 40 bytes.
4. Compute the keyed payload commitment over the unmodified plaintext with the exact table/formula
   above. After the final frame exists, compute `envelope_digest = sha256:<hex>` over every frame
   byte from `YZO1` through the final GCM tag. The digest is stored only in `StagedObject`/
   `ObjectRef` and structural inventory; it is not embedded in its own envelope.
5. Encrypt with AES-256-GCM into this exact `yoetz-object/1` framing:
   `b"YZO1" | 0x01 | u32be(header_length) | header_json | 12-byte payload_nonce |
   plaintext_size bytes ciphertext | 16-byte tag`. `header_length` is `1..16384`; the calculated
   total length must equal the observed frame and trailing bytes are invalid. `header_json` is JCS
   canonical UTF-8 and is the complete payload AEAD associated data. It has exactly these keys:
   `created_at`, `encryption_format` (`"yoetz-object/1"`), `key_slot`, `media_type`, `object_id`,
   `object_kind`, `payload_algorithm` (`"aes-256-gcm"`), `plaintext_size`, `task_id`,
   `wrap_algorithm` (`"aes-256-kw-rfc3394"`), and `wrapped_dek` (base64url without padding of the
   exact 40 wrapped bytes). Unknown/missing keys, noncanonical JSON, noncanonical UTC time, or
   algorithm/length mismatch fail. There is no wrap nonce or embedded checksum. Write the frame
   into a newly created owner-only temp file on the destination filesystem (the memory adapter
   holds the same frame in a staging map).
6. Return `StagedObject`. Nothing is durable or referenceable yet.

### `finalize`

1. Flush buffers, `fsync` the temp file, atomically rename into `objects/<2-hex-prefix>/`, then
   `fsync` the containing directory on supported platforms (ADR-003 item 6; memory adapter:
   atomically move staging → durable map).
2. Only after all of that succeeds, return `ObjectRef`. An owning ledger/importer/privacy-catalog
   row may reference the object only after `finalize` returns — this ordering is the durability
   contract, and the kill matrix enforces it.
3. `finalize` is not idempotent across retries of the *logical* content: a retry re-stages and
   re-finalizes, possibly creating an orphan. Correctness never depends on content-addressed
   deduplication; per this port's collection rules, orphans are collected only after a 24 h safety window
   and never while referenced or pinned by a maintenance pin.

An `ObjectRef` becomes live through its owning durable subsystem. Event/operation objects use task
SQLite inventory, importer objects use importer rows, and `ObjectKind.privacy_audit` uses the
installation privacy catalog's generation-bound root set. A privacy object MUST NOT receive a fake
task-ledger inventory row solely for reachability. `ObjectRootSnapshot` is the union of all owning
roots plus active maintenance pins; collection is invalid unless the route/bundle/privacy
generations and every source digest remain unchanged through deletion.

### `open_verified`

1. Resolve `ref.object_id` inside the bundle only; symlinks, hard links, traversal, and paths
   outside the bundle are rejected with `STORAGE_CORRUPT`.
2. Hash the complete observed frame and compare `ref.envelope_digest`; structurally parse magic,
   version, big-endian header length, exact canonical header, nonce/ciphertext/tag lengths, and no
   trailing bytes. Verify every authenticated header field matches `ref`: object ID, task ID,
   object kind, key slot, plaintext size, media type, creation time, format, and algorithms. Then
   unwrap the exact 40-byte wrapped DEK via RFC 3394 `K_wrap`, decrypt-and-authenticate with the
   parsed header bytes as GCM AAD, verify decrypted length, and recompute the raw-byte commitment.
3. Yield plaintext in bounded chunks (≤ 64 KiB) only after authentication succeeds — v0.1 objects
   are ≤ 4 MiB, so the adapter may decrypt one-shot in memory and then chunk the output; it MUST
   NOT yield unauthenticated bytes.
4. Any mismatch — tag failure, truncation, wrong key slot, commitment mismatch, appended bytes —
   raises before the first chunk; a partially consumed iterator never silently switches objects.

## Errors and edge cases

- `LIMIT_EXCEEDED` — plaintext over 4 MiB, header over bounds, media type not allowlisted.
- `STORAGE_UNSAFE` — bundle path failed safety validation (repo/synced/network/world-readable),
  temp file creation denied, fsync unsupported where required.
- `STORAGE_CORRUPT` — frame-digest/header/tag/commitment/length verification failure on read; the adapter
  reports the object as `quarantined` and never returns partial plaintext.
- `KeyStoreError(key_locked | key_missing | unsupported_backend)` propagates from the key
  collaboration untranslated (the application maps it; a locked key is never treated as an empty
  or redacted payload).
- Disk-full or I/O error during stage/finalize raises without acknowledgement; the temp file is
  abandoned for later cleanup.
- A `redacted`/`missing` object state in its owning ledger/importer/catalog record is that owner's
  concern;
  `open_verified` on a deleted file raises `STORAGE_CORRUPT` with reason `object_missing` and the
  caller degrades coverage rather than fabricating content.
- No plaintext, filename fragment, or key material in any raised error.

## Invariants

1. An `ObjectRef` exists only for durably finalized bytes; `stage` alone durably publishes
   nothing.
2. Every object is encrypted with a fresh DEK used exactly once; stable `K_wrap` performs only
   nonce-free RFC 3394 wrapping; the header is authenticated as AAD; truncation is always
   detectable through exact framing, the full-envelope digest, and GCM authentication.
3. `object_id` is a random opaque locator revealing neither plaintext digest nor path.
4. Objects are immutable after finalize: no method mutates, appends to, or re-keys an existing
   object. Redaction/deletion is a separate maintenance path outside this port.
5. `open_verified` never yields a byte that failed authentication or commitment verification.
6. The durability sequencing (fsync file → rename → fsync dir → only then referenceable) is a
   conformance requirement on every adapter, including the in-memory reference adapter's
   simulated crash points.
7. Catalog-held privacy-audit refs are first-class live roots despite having no task-ledger
   inventory row; GC and maintenance cannot infer orphanhood from the ledger alone.

## Tests

- `specs/tests/integration.md`: known-answer vectors for RFC 3394 wrapping and exact
  `yoetz-object/1` u32-big-endian/JCS/base64url framing; bit flip in header/AAD/payload nonce/
  ciphertext/tag; truncation at every boundary; appended bytes; every authenticated ref-field
  substitution; wrong key/slot; fresh-DEK/one-encryption behavior under concurrency and restart;
  all 17 exact domain-plus-raw-byte commitment vectors.
- `specs/tests/subprocess.md`: kill matrix points 1–5 (before creation, partial ciphertext, after
  file fsync before rename, after rename before dir fsync, after publication before append);
  orphan-collection safety window and pin respect.
- `specs/tests/conformance.md`: memory vs encrypted-files adapters agree on refs, commitments,
  and verified reads for all fixtures.
- `specs/tests/integration.md`: root snapshots union ledger, importer, privacy-catalog, and pin roots;
  generation races abort collection and no catalog-rooted privacy object is swept.
- `specs/tests/integration.md`: canary plaintext never appears in temp paths, object filenames, or
  errors.

## Open questions

None.
