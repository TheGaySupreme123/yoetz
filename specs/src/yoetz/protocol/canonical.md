# src/yoetz/protocol/canonical.py — restricted-JCS canonicalizer and digest identities

**Wave:** B (vectors are Wave A artifacts) | **ADRs:** ADR-002 (owns this file), ADR-004
(commitment keys — explicitly *not* here) |
**Imports (spec-tree):** `protocol/errors.md` (`ProtocolValueError`) |
**Imported by:** `protocol/models.md`, `protocol/schemas.md`, `domain/events.md`,
`application/*`, `adapters/sqlite/repository.md`, `adapters/objects/*`

## Purpose

The consensus-critical byte identity of the protocol. Every digest, idempotency key, entry chain,
and golden vector depends on these bytes being identical across processes, platforms, hash seeds,
and releases. ADR-002 decides: RFC 8785 JCS restricted to the Yoetz value profile registered in
`specs/INTERFACES.md`, implemented as a Yoetz-owned canonicalizer — the profile forbids floats entirely, so
ECMAScript number rendering degenerates to base-10 integers in ±(2^53−1). `json.dumps(sort_keys=
True)` is forbidden anywhere in the digest path (it sorts by code point, not UTF-16 code units,
and does not enforce the profile).

## Public surface

| Name | Signature (natural language) |
|---|---|
| `JsonValue` | exact recursive type alias: `None | bool | int | str | list[JsonValue] | tuple[JsonValue, ...] | Mapping[str, JsonValue]` (re-exported for callers; no other `Sequence` and no float is a member) |
| `canonical_encode(value: JsonValue) -> bytes` | restricted-JCS UTF-8 bytes; raises `ProtocolValueError` |
| `canonical_digest(value: JsonValue) -> str` | `"sha256:" + sha256(canonical_encode(value)).hexdigest()` |
| `strict_json_parse(data: bytes | bytearray) -> JsonValue` | strict profile-enforcing JSON decode over an immutable input snapshot; raises `ProtocolValueError` |
| `ensure_canonical_value(value: JsonValue) -> None` | walk an already-parsed value (e.g. from the MCP SDK) and enforce the full profile without encoding |
| `ensure_canonical_set(values: list[str] | tuple[str, ...]) -> None` | enforce set-valued-field rules (ASCII, ascending unsigned byte order, no duplicates) |
| `canonical_integer_string(value: int) -> str` | render a nonnegative int ≤ 2^63−1 as `0|[1-9][0-9]*`; raises on range |
| `parse_canonical_integer_string(value: str, *, signed: bool = False) -> int` | strict inverse; raises `noncanonical_integer_string` |
| `request_digest(identity: JsonValue) -> str` | publication-request identity digest with ledger-assigned-field fence |
| `entry_digest(preimage: JsonValue) -> str` | accepted-entry digest of the schema-shaped digest-preimage view |
| `MAX_JSON_DEPTH` | `int = 64` — the one shared maximum container nesting bound registered in `specs/INTERFACES.md` |

## Behavior

### `canonical_encode(value)`

Recursive serializer over `JsonValue`; depth counter starts at 0 and any container at depth
`MAX_JSON_DEPTH` raises `nesting_too_deep`. `protocol/canonical.py` owns and exports this one
constant; `domain/values.py` imports it for `freeze_json` and MUST NOT define a mirror. Output is
the UTF-8 encoding of:

1. **Literals:** `None → null`, `True → true`, `False → false`. `bool` is checked before `int`
   (Python `bool` subclasses `int`).
2. **Integers:** must satisfy `-(2**53 - 1) <= v <= 2**53 - 1`, else `integer_out_of_safe_range`.
   Rendered `str(v)` (base-10, no leading zeros, `-` only for negatives; Python has no int −0).
   Any `float` (including `float` NaN/inf) raises `float_forbidden` — there is no float branch.
3. **Strings:** reject any code point in U+D800–U+DFFF with `lone_surrogate` (Python strings can
   carry them via `surrogatepass`). Reject U+0000 with `nul_byte_forbidden`. Then serialize per
   RFC 8785 §3.2.2.2: escape `"` as `\"`, `\` as `\\`; controls U+0001–U+001F as `\b \t \n \f \r`
   where defined, else `\u00xx` with lowercase hex; every other code point is emitted literally
   (UTF-8). No `\/`, no `\uXXXX` for non-controls, **no Unicode normalization** — NFC and NFD
   spellings stay distinct.
4. **Arrays** (`list`/`tuple`): `[` + `,`-joined recursive encodings in given order + `]`. Order
   is preserved exactly; canonicalization never sorts arrays (set-field sorting happens at
   validation time, before this function — see `ensure_canonical_set`).
5. **Objects** (`Mapping`): keys must be `str` (else `object_key_not_string`); validate each key
   as in step 3; sort entries by the **UTF-16 big-endian encoding of the key**
   (`key.encode("utf-16-be")` compared as unsigned bytes — this is JCS's UTF-16 code-unit order
   and differs from Python's default code-point sort for supplementary-plane keys); emit
   `{"k":v,…}` with no whitespace. Duplicate keys cannot occur in a `Mapping`; duplicates in
   wire input are rejected earlier by `strict_json_parse`.
6. Any other Python type raises `unsupported_json_type`.

### Worked vector (freeze this in `fixtures/canonical/`)

Input value (shown as ordinary JSON; key order irrelevant at input):

```json
{"ﬀ": "bmp", "𝌆": "astral", "protocol": "yoetz.event",
 "writer": {"sequence": "12", "previous_entry_digest": "genesis"},
 "n": 42, "refs": ["evt_0d9254c1-031a-4e99-8bfe-a65ae8a28df8"]}
```

Canonical bytes (174 bytes, one line, non-ASCII shown literally as UTF-8):

```text
{"n":42,"protocol":"yoetz.event","refs":["evt_0d9254c1-031a-4e99-8bfe-a65ae8a28df8"],"writer":{"previous_entry_digest":"genesis","sequence":"12"},"𝌆":"astral","ﬀ":"bmp"}
```

`canonical_digest` = `sha256:7333f5cf44f9f98835dcb08ce3f3665346394f7e21181ef0859806b2063f70c6`.

The load-bearing detail: `𝌆` is U+1D306 (UTF-16 surrogates `d834 df06`), `ﬀ` is U+FB00.
Code-point order puts `ﬀ` (0xFB00) before `𝌆` (0x1D306 > 0xFB00 is false — 0x1D306 = 119558 >
64256, so code-point order is `ﬀ` **first**? No: 0xFB00 = 64256 < 0x1D306 = 119558, so code-point
order is `ﬀ`, `𝌆`); UTF-16 order compares `d834…` < `fb00` and puts `𝌆` **before** `ﬀ`. A
code-point sorter produces different bytes and a different digest — this vector catches it.

Escape vector: `{"a": "line\nbreak", "b": "\u0001"}` canonicalizes to
`{"a":"line\nbreak","b":"\u0001"}` (32 bytes; `\n` two chars, `\u0001` six chars, lowercase hex),
digest `sha256:06efd62f5b85ba6f06b14beb4939be5733c4d75d2ea344d1dbeca87c9cb07912`.

### `strict_json_parse(data)`

The only permitted wire-JSON decoder in the digest path and at the CLI boundary.

1. `data` must be `bytes`/`bytearray` → else `input_not_bytes`. A `bytearray` is copied once to
   immutable `bytes` before any decode, inspection, or parse; every later step uses only that
   snapshot, so caller mutation cannot change the value being validated.
2. Decode the immutable bytes as UTF-8 with `errors="strict"` → failure raises `invalid_utf8`.
3. Reject a leading U+FEFF with `byte_order_mark_forbidden` (a BOM decodes cleanly, so this is an
   explicit check, not a decode failure).
4. Parse with a `json.JSONDecoder` configured so the profile is enforced *during* parse:
   - `object_pairs_hook` builds the dict and raises `duplicate_object_key` when a key repeats
     (byte-identical or not — any repeat);
   - `parse_float` raises `float_forbidden` (any literal containing `.`, `e`, or `E` routes
     here);
   - `parse_int` inspects the literal first: literal `-0` raises `float_forbidden` (negative
     zero; INTERFACES §4 groups it with floats); otherwise `int(literal)` then range-check
     ±(2^53−1) → `integer_out_of_safe_range`;
   - `parse_constant` (NaN/Infinity/-Infinity) raises `float_forbidden`.
   Syntax errors, trailing bytes, and empty input map `json.JSONDecodeError` →
   `malformed_json`. A `RecursionError` or depth beyond `MAX_JSON_DEPTH` (checked in the
   hooks via a decoder-local depth counter, or post-walk) → `nesting_too_deep`.
5. Post-walk every string (keys and values) rejecting surrogate code points (`lone_surrogate` —
   Python's decoder accepts `"\ud800"` escapes) and U+0000 (`nul_byte_forbidden`, covering the
   escaped `\u0000` form; a raw NUL byte anywhere in the input is rejected earlier with the
   same `nul_byte_forbidden` by an explicit pre-decode check on the immutable byte snapshot,
   before UTF-8 decoding or JSON syntax classification).
6. Return the value. Property: for any bytes `b` accepted by `strict_json_parse`,
   `canonical_encode(strict_json_parse(b))` succeeds, and if `b` was already canonical,
   round-trips byte-identically (idempotence: `canonical_encode(strict_json_parse(
   canonical_encode(v))) == canonical_encode(v)`).

### `ensure_canonical_value(value)`

Same checks as `canonical_encode` steps 1–6 (types, int range, surrogates, NUL, key types, depth)
without producing bytes. Used by `protocol/models.md` on payload `JsonValue` fields that arrive
pre-parsed by the MCP SDK (which tolerates floats and does not enforce the profile). Raises the
same reason codes.

### `ensure_canonical_set(values)`

For protocol-`0.1` set-valued fields (`causal_parents`, reference arrays, `known_gaps`), the outer
value must satisfy `isinstance(values, (list, tuple))`; every other outer type, including `str`,
`bytes`, another `Sequence`, or an iterator, raises `unsupported_json_type`. Every member must be
`str` and pure ASCII; a non-string or non-ASCII member raises `set_member_not_ascii`. Members must
be strictly ascending by unsigned byte comparison of their ASCII bytes — an equal neighbor raises
`duplicate_set_member`, a descending neighbor raises `unsorted_set_field`. Empty lists and tuples
pass. This validates; it never silently sorts (the client must send canonical order — normalization
at the boundary would create two accepted spellings of the same request bytes and break
idempotency-digest equality).

### Integer-string helpers

`canonical_integer_string(v)`: `int` (not bool), `0 <= v <= 9_223_372_036_854_775_807` (the
SQLite signed-int64 storage ceiling) → `str(v)`; else `integer_out_of_sqlite_range`.
`parse_canonical_integer_string(s, signed=False)`: full match `0|[1-9][0-9]*` (or
`0|-?[1-9][0-9]*` when `signed=True`, only where a schema explicitly permits signed values);
length ≤ 19 (20 with sign) before matching; result within int64 (nonneg) / signed-int64 bounds;
any failure → `noncanonical_integer_string`. `"01"`, `"+1"`, `"1 "`, `"-0"`, `""` all fail.

### Two byte identities (ADR-002 decision 4)

- **`request_digest(identity)`** — SHA-256 over the canonical bytes of the *publication request
  identity object*: the caller's logical headers plus deterministic **keyed payload commitments**
  (`hmac-sha256:<hex>` strings computed elsewhere with `K_commit` domain separation, ADR-004 §3;
  this module never sees key material). Before encoding, the helper walks the tree and raises
  `ledger_assigned_field_in_request_identity` if any of these key names appear at any depth:
  `ingestion_sequence`, `accepted_at`, `previous_entry_digest`, `object_id`, `ledger`. This is a
  deterministic fence guaranteeing retry re-encryption (new object IDs/nonces) and ledger
  assignment can never change logical identity. The exact identity field list is owned by
  `application/publish_work.md` (and `application/start.md` for the catalog); this helper owns
  only bytes + fence. Result string is stored as `request_digest` under
  `(writer_id, operation_id)` / `(installation_id, operation_id)`.
- **`entry_digest(preimage)`** — SHA-256 over the canonical bytes of the accepted structural
  `AcceptedEvent` **digest-preimage view** from `accepted_record_digest_preimage()` in
  `specs/src/yoetz/domain/events.md`, after the ledger assigns order and predecessors. The input
  must be a `Mapping`, must have `preimage["protocol"] == "yoetz.event"`, and its keys must be
  exactly `artifact_refs`, `author`, `causal_parents`, `coverage`, `event_id`, `evidence_refs`,
  `ledger`, `occurred_at`, `operation_id`, `payload_ref`, `protocol`, `protocol_version`,
  `publication_channel`, `redaction`, `schema`, `session_id`, `task_id`, and `writer`. Thus it has
  neither a top-level `entry_digest` nor a top-level decoded `payload`. A non-mapping input, wrong
  protocol token, missing top-level field, or extra top-level field raises
  `ProtocolValueError("not_an_accepted_envelope")` before canonical encoding. After that exact
  top-level gate, ordinary canonical-value validation owns types, integer bounds, strings, keys,
  and depth and propagates its own registered reason. This helper deliberately does **not** perform
  deep accepted-event schema or domain validation; `domain/events.py` and the frozen accepted-event
  schema own those checks before producing this preimage. The view contains every other field in
  the persisted accepted record, including `payload_ref`, and is exactly the full schema-shaped
  record with `entry_digest` removed. The full record (which includes `entry_digest` and excludes
  decoded `payload`) is what the accepted-event JSON Schema validates; that schema is not applied
  to the deliberately incomplete preimage. Empty predecessor is the literal string `"genesis"`,
  never `null`.

Both return `sha256:<64 lowercase hex>`. `event_id` (logical name), `payload.commitment` (keyed
payload identity), `entry_digest` (ledger-entry identity), and `operation_id` (retryable API
operation) remain four distinct identities; nothing in this file conflates them.

### CI second-language oracle contract (ADR-002 §1)

Every fixture under `fixtures/canonical/` carries input JSON, expected canonical bytes (or an
expected reason code), and expected digest. A CI-only job (never a runtime dependency) runs an
independent implementation — Node `canonicalize` or Rust `serde_jcs` — over every *positive*
fixture and must agree byte-for-byte; profile-rejection fixtures are asserted Python-side only
(full JCS accepts floats the profile rejects). The gate blocks release on any mismatch. Fixtures
include: all RFC 8785 vectors applicable to the profile, the verified negative-zero erratum,
UTF-16 ordering cases (Hebrew, emoji, combining marks, the `𝌆`/`ﬀ` inversion above), NFC/NFD
distinctness, and fuzz-discovered cases (each promoted to a frozen vector). Complete branch
coverage and independent-oracle parity are behavioral release obligations because this module is
consensus-critical. Their measurement harness, locked second-language tool, and CI wiring belong
to the named Wave F `pr-ci.yml`/`release.yml` workflow and repository-tooling owners; B0 does not
add an otherwise unused runtime or development dependency merely to measure them early.

## Errors and edge cases

Reason codes owned here (registered in `PROTOCOL_REASON_CODES`): `input_not_bytes`,
`invalid_utf8`, `byte_order_mark_forbidden`, `nul_byte_forbidden`, `malformed_json`,
`duplicate_object_key`, `float_forbidden`, `integer_out_of_safe_range`,
`integer_out_of_sqlite_range`, `noncanonical_integer_string`, `lone_surrogate`,
`nesting_too_deep`, `object_key_not_string`, `unsupported_json_type`, `set_member_not_ascii`,
`unsorted_set_field`, `duplicate_set_member`, `ledger_assigned_field_in_request_identity`,
`not_an_accepted_envelope`.

- No error message ever contains input content — reason codes only. Mapping to public
  `INVALID_REQUEST`/`EVENT_INVALID` happens in callers via `protocol/errors.md`.
- This module performs no I/O, no logging, reads no clock/env/locale, and holds no state; output
  is identical under any `PYTHONHASHSEED`, locale, timezone, and `-O` mode, as exercised by
  `specs/tests/conformance.md`.

## Invariants

1. Same value → same bytes → same digest on every supported platform and process; the frozen
   replay contract is owned by this file and ADR-002.
2. Accepted `canonical_entry` bytes stored in SQLite are re-verified on read; a serializer change
   can never rewrite released identity (ADR-002 consequences) — hence the frozen vectors.
3. Floats never appear in canonical protocol objects; large counters travel as canonical integer
   strings (INTERFACES §3).
4. No Unicode normalization, ever; strings are preserved exactly.
5. `request_digest` input excludes plaintext payloads, object IDs, nonces, and all
   ledger-assigned fields. Accepted entries have two explicit views: the full persisted/schema
   record includes `entry_digest` and excludes decoded `payload`; the digest preimage excludes
   both `entry_digest` and decoded `payload` and otherwise contains the same structural fields.
6. No key material, HMAC computation, or secret enters this module (ADR-004 boundary).

## Tests

- Root `fixtures/canonical/` golden vectors, loaded only through the manifest-bound
  `tests/fixture_loader.py` (positive, rejection, request-digest, entry-digest, integer-string,
  set-field) — permanent compatibility obligations once released. Tests never parse the Markdown
  fixture-spec shadows and never fall back to an installed mirror.
- `specs/tests/unit.md` → `tests/unit/protocol/test_canonical_vectors.py`: every vector; every
  reason code; idempotent round-trip; the two worked vectors above byte-exact.
- Inline unit vectors cover rejection paths that cannot be represented as JSON fixture values:
  `input_not_bytes`, `integer_out_of_sqlite_range`, `object_key_not_string`,
  `unsupported_json_type`, and `not_an_accepted_envelope` (including embedded `entry_digest` and
  embedded decoded `payload`).
- `specs/tests/property.md`: Hypothesis — encode/parse round-trip, permutation of object key
  insertion order never changes bytes, array order always preserved, `ensure_canonical_set`
  accepts exactly the sorted-unique ASCII permutation.
- Installed canonical-fixture mirror byte identity is owned by
  `tests/packaging/test_resource_byte_parity.py`; canonical unit/property tests own fixture
  semantics from the reviewed root corpus.
- Wave F CI oracle job (`pr-ci.yml`/`release.yml`): second-language byte parity on all positive
  vectors.
- Determinism matrix: the 12 exact subprocess cells formed by `PYTHONHASHSEED` `0`, `1`, and
  `4294967295`; `TZ="UTC"` and `TZ="Pacific/Honolulu"`; `LC_ALL="C"`; and normal versus `-O`
  interpreter mode.

## Open questions

None.
