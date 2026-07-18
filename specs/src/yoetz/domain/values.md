# src/yoetz/domain/values.py — frozen domain value types

**Wave:** B | **ADRs:** ADR-002, ADR-004 | **Imports (spec-tree):** `protocol/ids.md`,
`protocol/errors.md`, `protocol/coverage.md`, `protocol/canonical.md` | **Imported by:** `domain/events.md`,
`domain/findings.md`, `domain/receipts.md`, `kernel/projections.md`, `kernel/reducers.md`,
`kernel/deterministic_checks.md`, `kernel/ranking.md`, `kernel/receipt_builder.md`,
`application/*`, `adapters/*`

## Purpose

The immutable vocabulary the pure kernel computes with. Every value that crosses from a boundary
model (Pydantic, MCP, SQLite, provider SDK) into the domain is converted here into a frozen,
runtime-validated value with exactly one canonical spelling. Without this file the kernel would
compute over mutable SDK dicts, canonical bytes would depend on adapter serialization, and the
byte-identical determinism contract in ADR-002 and `specs/src/yoetz/protocol/canonical.md`
would be unenforceable.

This file contains **no Pydantic, MCP, SQLite, or provider imports**. It may import only the
standard library plus `yoetz.protocol.ids` (ID validation), `yoetz.protocol.errors`
(`ProtocolValueError`), `yoetz.protocol.coverage` (the `AuthorshipAssurance` enum), and
`yoetz.protocol.canonical` (`MAX_JSON_DEPTH` only).

## Public surface

- `JsonScalar` — type alias `str | int | bool | None`.
- `JsonValue` — type alias `JsonScalar | tuple[JsonValue, ...] | JsonObject`.
- `JsonObject` — immutable string-keyed mapping of `JsonValue` (frozen, hashable, iteration in
  insertion order; equality is order-insensitive key/value equality).
- `freeze_json(value) -> JsonValue` — recursive validating converter from parsed JSON to the
  frozen profile.
- ID newtypes: `RequestId`, `TaskId`, `SessionId`, `WriterId`, `EventId`, `ObligationId`, `ClaimId`,
  `ActionId`, `ResultId`, `EvidenceId`, `FindingId`, `ObjectId`, `ReceiptId`,
  `ActorId` — each a `NewType` over `str`.
- ID constructors: one lowercase-named function per newtype (`task_id(value) -> TaskId`,
  `event_id(value) -> EventId`, …, `actor_id(value) -> ActorId`) — validate then wrap.
- `ActorType` — enum: `human`, `harness`, `logical_agent`, `model_backed_worker`,
  `delegated_subagent`, `yoetz_engine`, `importer` (INTERFACES §6).
- `Actor` — frozen dataclass `(actor_id: ActorId, actor_type: ActorType,
  assurance: AuthorshipAssurance)`.
- `Timestamp` — frozen value wrapping the one canonical RFC 3339 UTC spelling.
  Constructors `timestamp_from_string(value) -> Timestamp`,
  `timestamp_from_datetime(dt) -> Timestamp`. Property `wire: str`. Total chronological order.
- `format_rfc3339_millis(dt) -> str`, `parse_rfc3339_millis(value) -> datetime`, and
  `add_utc_milliseconds(dt, milliseconds) -> datetime` — pure canonical time helpers used by the
  constructors and persisted lease calculations.
- `Frontier` — frozen dataclass `(sequence: int, head_digest: str)` with
  `Frontier.genesis()`, `as_wire() -> JsonObject`, and guarded sequence comparison.
- `SubjectStateRef` — frozen dataclass `(tree_digest: str | None, diff_digest: str | None,
  described_state: str | None)`.
- `SubjectStateRelation` — enum: `same`, `different`, `unknown`.
- `subject_state_relation(a: SubjectStateRef | None, b: SubjectStateRef | None)
  -> SubjectStateRelation`.
- `validate_sha256_digest(value: str) -> str` — accepts exactly `sha256:<64 lowercase hex>`.
- `validate_commitment(value: str) -> str` — accepts exactly `hmac-sha256:<64 lowercase hex>`.
- `parse_wire_sequence(value: str) -> int` / `render_wire_sequence(value: int) -> str` —
  canonical base-10 integer-string sequence conversion.
- `GENESIS_DIGEST` — the literal string constant `"genesis"` (INTERFACES §3).

All dataclasses are declared `@dataclass(frozen=True, slots=True)`.

## Behavior

### `freeze_json(value)`

Input: the output of `protocol.canonical.strict_json_parse` or an already-frozen value.
Recursively:

1. `None`, `bool`, `str` pass through. `bool` is checked **before** `int`
   (`isinstance(True, int)` is true in Python; booleans must remain booleans).
2. `int` must satisfy `-(2**53 - 1) <= value <= 2**53 - 1`; otherwise
   `ProtocolValueError("integer_out_of_safe_range")`.
3. `float` (and any other numeric type) raises `ProtocolValueError("float_forbidden")`.
4. `list`/`tuple` recurse element-wise into a `tuple`.
5. `dict`/`Mapping` requires every key to be `str`; recurses values and produces a `JsonObject`.
   Non-string key: `ProtocolValueError("object_key_not_string")`.
6. Any other type: `ProtocolValueError("unsupported_json_type")`.

`JsonObject` is a small final class holding an internal `tuple[tuple[str, JsonValue], ...]` in
insertion order plus a frozen key index. It implements `Mapping[str, JsonValue]`, `__hash__`
(over the sorted item tuple), `__eq__` (order-insensitive), and rejects duplicate keys at
construction with `ProtocolValueError("duplicate_object_key")`. It never exposes a mutable view.

### ID newtypes and constructors

Each constructor calls `protocol.ids.validate_id(kind, value)` with the matching `IdKind`
(prefixes exactly per INTERFACES §1: `req_`, `tsk_`, `ses_`, `wri_`, `evt_`, `obl_`, `clm_`, `act_`,
`res_`, `evd_`, `fnd_`, `obj_`, `rcp_`) and returns the validated string wrapped in the
newtype. `ActionId`/`ResultId`/`EvidenceId` use the payload-level client prefixes `act_`, `res_`,
`evd_` exactly as registered in INTERFACES §1.
`RequestId` validates the `req_` shape and is also the type used by the accepted envelope's
`operation_id`; there is no separate operation-specific nominal type. `actor_id` is the exception: it
is caller-asserted convention,
validated only against `^[A-Za-z0-9._:-]{1,128}$` — never against a UUID shape — and raises
the ID owner's `ProtocolValueError("actor_id_malformed")` on a shape mismatch (wrong non-string
type remains `id_wrong_type`). Constructors never lowercase, trim, or
otherwise rewrite input; the input either already has the single canonical spelling or fails.

### `Actor`

Pure attribution triple. `assurance` is **server-assigned** upstream (INTERFACES §6); this class
performs no assurance logic, only shape validation in `__post_init__`: `actor_id` re-validated as
above, `actor_type`/`assurance` must be enum members. Display names are payload content and are
deliberately absent from this type; `specs/src/yoetz/domain/events.md` owns payload display fields.

### `Timestamp`

Canonical wire form: RFC 3339 UTC, exactly three fractional digits, uppercase `Z`, uppercase `T`
(`2026-07-13T09:14:31.010Z`) — INTERFACES §3. `timestamp_from_string` validates with a strict
regex (`^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$`) plus a real calendar-validity check by
round-tripping through `datetime.strptime`; leap seconds and offsets other than `Z` are rejected
(`ProtocolValueError("invalid_timestamp")`). `timestamp_from_datetime` requires an actual
`datetime`, an aware instant whose UTC offset is exactly zero (otherwise
`ProtocolValueError("timestamp_not_utc")`), and `microsecond % 1000 == 0`. Sub-millisecond input
raises `ProtocolValueError("timestamp_submillisecond_precision")`; it is never rounded or
truncated. `format_rfc3339_millis` applies the identical checks. A fixed-offset `+00:00` tzinfo is
accepted because it denotes UTC, but the one rendered spelling remains `Z`.
Ordering is plain string comparison — valid because the format is fixed-width UTC, so
lexicographic order equals chronological order. `Timestamp` never establishes ledger or causal
order (binding invariant); it is metadata carried alongside events.

`add_utc_milliseconds` requires an exact-millisecond UTC `datetime` and an `int` milliseconds
argument (not `bool`), performs exact integer arithmetic, and preserves millisecond precision.
Calendar overflow raises `ProtocolValueError("timestamp_out_of_range")`; it is never clipped and
no platform `OverflowError` escapes this value boundary.

### `Frontier`

`sequence` is an `int` but not `bool` in `0..9_223_372_036_854_775_807`. When `sequence == 0`,
`head_digest` MUST be `GENESIS_DIGEST`; when
`sequence > 0`, `head_digest` MUST pass `validate_sha256_digest`. Violations raise
`ProtocolValueError("invalid_frontier")`. `Frontier.genesis()` returns `Frontier(0, "genesis")`.
`as_wire()` returns `JsonObject({"sequence": render_wire_sequence(sequence),
"head_digest": head_digest})`, the one closed frontier shape owned by
`schemas/common/frontier-1.0.0.schema.json` and used for every `subject_frontier` value.
Equality is structural over both fields. The four ordering operators compare `sequence`, but first
raise `ProtocolValueError("frontier_digest_mismatch")` when two frontiers have the same sequence
and different digests. A comparison with any non-`Frontier` returns `NotImplemented`. This is a
guarded partial order, not a total order: divergent equal-height histories are never silently
equal or ordered. The implementation must not use `@dataclass(order=True)`.

### `SubjectStateRef` and `subject_state_relation`

At least one of the three fields must be non-`None`
(`ProtocolValueError("empty_subject_state")`). `tree_digest`/`diff_digest`, when present, must
pass `validate_sha256_digest`. `described_state` is a bounded free-text label (1–256 characters);
it exists so weak references remain expressible but it never participates in equality-based
freshness logic, request commitments, or deterministic state identity.

`subject_state_relation(a, b)` implements the three-valued comparison the freshness checks
depend on (kernel `deterministic_checks.md` K6):

- If either argument is `None` → `unknown`.
- If both have `tree_digest`: equal digests → `same`; unequal → `different`.
- Else if both have `diff_digest`: equal → `same`; unequal → `unknown` (two different diffs do
  not prove different trees).
- Else → `unknown`.

Worked example: a parity-suite result carries
`SubjectStateRef(tree_digest="sha256:…treeA…")`; the later `config.rs` edit publishes an action
with `tree_digest="sha256:…treeB…"`. `subject_state_relation(result_state, edit_state)` returns
`different`, which is the deterministic fact behind finding
`stale_evidence_for_changed_state`. Had either side published only `described_state="tree-A"`,
the relation would be `unknown` and no staleness finding could fire — the receipt instead shows
weak `evidence_immutability`.

### Sequence helpers

`parse_wire_sequence` accepts exactly the canonical pattern `0|[1-9][0-9]*` (leading zeros,
signs, whitespace all rejected with `ProtocolValueError("noncanonical_integer_string")`), bounds
the result to SQLite's signed 64-bit range `0..9_223_372_036_854_775_807`
(`ProtocolValueError("integer_out_of_sqlite_range")`), and returns `int`.
`render_wire_sequence` is its exact inverse and rejects `bool`, non-`int`, negatives, and values
above the same signed-64-bit ceiling. The round trip holds over the complete accepted domain.

## Errors and edge cases

- Every failure is `ProtocolValueError(reason_code)` with a bounded reason code from this file's
  fixed set: `duplicate_object_key`, `float_forbidden`, `integer_out_of_safe_range`,
  `integer_out_of_sqlite_range`, `object_key_not_string`, `unsupported_json_type`,
  `actor_id_malformed`, `invalid_timestamp`, `timestamp_not_utc`,
  `timestamp_submillisecond_precision`, `timestamp_out_of_range`, `invalid_frontier`,
  `frontier_digest_mismatch`, `empty_subject_state`, `invalid_digest`, `invalid_commitment`,
  `noncanonical_integer_string`, `nesting_too_deep`, plus the codes raised through
  `protocol.ids.validate_id`.
- Reason codes never embed caller input; no user text can leak through an exception message.
- `freeze_json` on deeply nested input is bounded by the frozen `MAX_JSON_DEPTH = 64` levels
  imported from `protocol/canonical.py` (this module defines no mirror) and raises the owner's
  `ProtocolValueError("nesting_too_deep")`, so hostile payloads cannot trigger recursion overflow.
- `Timestamp` equality across identical instants with different spellings cannot occur: only the
  canonical spelling constructs.

## Invariants

1. Every instance of every type in this file is deeply immutable and hashable.
2. One spelling per value: IDs, digests, commitments, timestamps, and sequences each have exactly
   one accepted textual form (ADR-002 decision 5).
3. No value here reads the clock, environment, filesystem, or RNG; construction is a pure
   function of its inputs.
4. Wall-clock values (`Timestamp`, `occurred_at`) never define order; only `Frontier.sequence`
   (ledger-assigned) does.
5. `subject_state_relation` never returns `different` without two present, unequal
   `tree_digest` values — the honesty rules forbid claiming state change on weaker evidence.
6. No Pydantic/SDK type appears in any signature or field.

## Tests

- `tests/unit/domain/test_values.py` — constructor accept/reject tables for every type; the
  bool-before-int trap; depth bound; JsonObject duplicate-key and hash/equality laws.
- `tests/unit/domain/test_values.py` — constructor tables, the full subject-state relation matrix,
  and Hypothesis coverage for `render_wire_sequence ∘ parse_wire_sequence`, timestamp round-trip,
  and `freeze_json` idempotence.
- Determinism controls required by ADR-002: identical bytes under varied `PYTHONHASHSEED`,
  locale, and timezone.

## Open questions

None.
