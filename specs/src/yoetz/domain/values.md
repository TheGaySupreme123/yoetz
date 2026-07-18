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
`yoetz.protocol.canonical` (`MAX_JSON_DEPTH`, `ensure_canonical_value`,
`canonical_integer_string`, and `parse_canonical_integer_string`). It defines no mirror of a
canonical depth, integer-string, or Unicode-validation rule.

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
- `frontier_from_json(value) -> Frontier` — strict inverse of `Frontier.as_wire()` over the one
  closed frontier object.
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

Input: the output of `protocol.canonical.strict_json_parse` or an already-frozen value. Scalar and
array inputs must be exact built-in JSON-profile types; subclasses are not extension points.
Mappings may be a parsed plain `dict`, an already-frozen `JsonObject`, or another actual
`Mapping`. Recursively, in this exact dispatch order:

1. `None` and exact `bool` pass through. `bool` is checked **before** `int` so booleans never enter
   the integer branch.
2. Exact `int` must satisfy `-(2**53 - 1) <= value <= 2**53 - 1`; otherwise
   `ProtocolValueError("integer_out_of_safe_range")`.
3. An actual `float` or real `float` subclass raises `ProtocolValueError("float_forbidden")`.
   Other numeric classes such as `Decimal`, `Fraction`, and `complex`, and an `int` subclass, are
   unsupported objects and reach `unsupported_json_type`; they are never coerced or mislabeled as
   wire floats.
4. Exact built-in `str` is validated through `ensure_canonical_value` and then passes through.
   NUL and lone-surrogate code points therefore raise the canonical owner's
   `nul_byte_forbidden` and `lone_surrogate` reasons. A `str` subclass is not accepted as a scalar.
5. An exact `JsonObject` passes through; its constructor has already applied this same recursive
   validation.
6. Exact built-in `list`/`tuple` recurse element-wise into a built-in `tuple` after enforcing the
   shared `MAX_JSON_DEPTH` container bound.
7. An actual `Mapping` requires every key to be an exact built-in `str`, validates each key through
   `ensure_canonical_value`, recurses values, and produces a `JsonObject`. A non-string or `str`
   subclass key raises `ProtocolValueError("object_key_not_string")` before its value is read.
8. Any other type, including scalar/container subclasses not admitted above, raises
   `ProtocolValueError("unsupported_json_type")`.

`JsonObject` is a small final class holding an internal `tuple[tuple[str, JsonValue], ...]` in
insertion order plus a frozen key index. Its constructor accepts either an actual `Mapping` or an
exact built-in `list`/`tuple` of exact two-item built-in `tuple` pairs. The pair form is the sole
duplicate-capable input; the mapping form preserves its iteration order but cannot represent a
duplicate key. It implements `Mapping[str, JsonValue]`, `__hash__` (over the sorted item tuple),
`__eq__` (order-insensitive), and rejects duplicate pair-form keys at construction with
`ProtocolValueError("duplicate_object_key")`. Direct construction applies the same exact-key,
canonical-string, recursive-freeze, and depth rules as `freeze_json`; it never exposes a mutable
view. In the pair form, duplicate detection precedes recursive validation of the duplicate's value,
so the stable duplicate-key reason wins without inspecting rejected content. A wrong outer or pair
container shape raises `unsupported_json_type`.

### ID newtypes and constructors

Each constructor calls `protocol.ids.validate_id(kind, value)` with the matching `IdKind`
(prefixes exactly per INTERFACES §1: `req_`, `tsk_`, `ses_`, `wri_`, `evt_`, `obl_`, `clm_`, `act_`,
`res_`, `evd_`, `fnd_`, `obj_`, `rcp_`) and returns the validated string wrapped in the
newtype. `ActionId`/`ResultId`/`EvidenceId` use the payload-level client prefixes `act_`, `res_`,
`evd_` exactly as registered in INTERFACES §1. After validation, the constructor snapshots the
spelling into an exact built-in `str` before applying the `NewType`; a valid `str` subclass may be
accepted by the ID owner, but no caller-defined subclass behavior enters the frozen domain.
`RequestId` validates the `req_` shape and is also the type used by the accepted envelope's
`operation_id`; there is no separate operation-specific nominal type. `actor_id` is the exception: it
is caller-asserted convention,
validated only against `^[A-Za-z0-9._:-]{1,128}$` — never against a UUID shape — and raises
the ID owner's `ProtocolValueError("actor_id_malformed")` on a shape mismatch (wrong non-string
type remains `id_wrong_type`). `actor_id` applies the same exact-built-in snapshot after
`validate_actor_id`. Constructors never lowercase, trim, or otherwise alter characters; the input
either already has the single canonical spelling or fails.

### `Actor`

Pure attribution triple. `assurance` is **server-assigned** upstream (INTERFACES §6); this class
performs no assurance logic, only shape validation in `__post_init__`: `actor_id` re-validated as
above, `actor_type` must be an exact domain `ActorType` member, and `assurance` must be an exact
`AuthorshipAssurance` member. Validation runs in that field order. A wrong actor enum raises
`ProtocolValueError("invalid_actor_type")`; a wrong assurance value raises the coverage owner's
`ProtocolValueError("invalid_coverage_value")`. Display names are payload content and are
deliberately absent from this type; `specs/src/yoetz/domain/events.md` owns payload display fields.

### `Timestamp`

Canonical wire form: RFC 3339 UTC, exactly three fractional digits, uppercase `Z`, uppercase `T`
(`2026-07-13T09:14:31.010Z`) — INTERFACES §3. `timestamp_from_string` validates with a strict
regex (`^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$`) plus a real calendar-validity check by
round-tripping through `datetime.strptime`; leap seconds and offsets other than `Z` are rejected
(`ProtocolValueError("invalid_timestamp")`). `timestamp_from_datetime` and
`format_rfc3339_millis` validate in this exact order: `type(dt) is datetime` or
`invalid_timestamp`; `tzinfo`/`utcoffset()` is present or `timestamp_timezone_missing`; the offset
equals zero or `timestamp_not_utc`; and `microsecond % 1000 == 0` or
`timestamp_submillisecond_precision`. No subclass, coercion, rounding, or truncation is accepted.
An accepted fixed-offset or named-zone zero-offset instant is normalized to `timezone.utc` before
rendering, so the one spelling remains `Z` and no later zone transition can affect arithmetic.
Ordering is plain string comparison — valid because the format is fixed-width UTC, so
lexicographic order equals chronological order. `Timestamp` never establishes ledger or causal
order (binding invariant); it is metadata carried alongside events.

`add_utc_milliseconds` is the sole duration-addition helper name. It first applies the exact
datetime validation above and normalizes the accepted zero-offset instant to `timezone.utc`. It
then requires `type(milliseconds) is int` and
`1 <= milliseconds <= 9_007_199_254_740_991`; `bool`, zero, negative, subclassed, and over-limit
values raise `ProtocolValueError("invalid_duration")`. It performs exact integer arithmetic and
preserves millisecond precision. `timedelta` construction or calendar addition overflow raises
`ProtocolValueError("timestamp_out_of_range")`; it is never clipped and no platform
`OverflowError` escapes this value boundary.

### `Frontier`

`sequence` is an `int` but not `bool` in `0..9_223_372_036_854_775_807`. When `sequence == 0`,
`head_digest` MUST be `GENESIS_DIGEST`; when
`sequence > 0`, `head_digest` MUST pass `validate_sha256_digest`. Violations raise
`ProtocolValueError("invalid_frontier")`. `Frontier.genesis()` returns `Frontier(0, "genesis")`.
`as_wire()` returns `JsonObject({"sequence": render_wire_sequence(sequence),
"head_digest": head_digest})`, the one closed frontier shape owned by
`schemas/common/frontier-1.0.0.schema.json` and used for every `subject_frontier` value.
`frontier_from_json` accepts an actual mapping with exactly the two exact built-in string keys
`sequence` and `head_digest`, in either insertion order, and no others. It validates shape first,
then parses `sequence` through `parse_wire_sequence`, then constructs `Frontier`. A malformed object,
wrong key set, wrong `head_digest` type, or genesis/digest mismatch raises `invalid_frontier`;
the sequence parser's `noncanonical_integer_string` reason propagates unchanged. `as_wire()` and
`frontier_from_json()` are exact inverses over every valid frontier.
Equality is structural over both fields. The four ordering operators compare `sequence`, but first
raise `ProtocolValueError("frontier_digest_mismatch")` when two frontiers have the same sequence
and different digests. A comparison with any non-`Frontier` returns `NotImplemented`. This is a
guarded partial order, not a total order: divergent equal-height histories are never silently
equal or ordered. The implementation must not use `@dataclass(order=True)`.

### `SubjectStateRef` and `subject_state_relation`

At least one of the three fields must be non-`None`
(`ProtocolValueError("empty_subject_state")`). `tree_digest`/`diff_digest`, when present, must
pass `validate_sha256_digest`. `described_state`, when present, must be an exact built-in `str` of
1–256 Unicode code points; a wrong type (including a subclass) or length raises
`ProtocolValueError("invalid_subject_state")`, while canonical validation of NUL/lone-surrogate
content propagates `nul_byte_forbidden`/`lone_surrogate`. Validation order is the all-absent check,
`tree_digest`, `diff_digest`, then `described_state`. The label exists so weak references remain
expressible but it never participates in equality-based freshness logic, request commitments, or
deterministic state identity.

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

`parse_wire_sequence` is a thin unsigned wrapper around
`protocol.canonical.parse_canonical_integer_string`. It accepts exactly the canonical pattern
`0|[1-9][0-9]*` in `0..9_223_372_036_854_775_807`; wrong types, leading zeros, signs, whitespace,
and over-range values all retain the canonical owner's
`ProtocolValueError("noncanonical_integer_string")` reason. `render_wire_sequence` is a thin alias
or wrapper of `protocol.canonical.canonical_integer_string`; it rejects `bool`, non-`int`,
negatives, and values above the same ceiling with `integer_out_of_sqlite_range`. The round trip
holds over the complete accepted domain.

### Fixed validation order

Where more than one defect is present, validation is deterministic: `freeze_json` follows its
eight-branch dispatch and checks mapping keys before values; ID constructors delegate all shape
checks before snapshotting; `Actor` checks actor ID, actor type, then assurance; datetime helpers
check exact type, timezone presence, zero offset, millisecond precision, then duration and calendar
range; `Frontier` checks sequence type/range before the genesis/digest rule;
`frontier_from_json` checks object/key shape, sequence parsing, then `Frontier`; and
`SubjectStateRef` checks all-absent, tree digest, diff digest, then described state. Tests use
multi-defect values to lock these precedence rules.

## Errors and edge cases

- Every failure is `ProtocolValueError(reason_code)` with a bounded reason code from this file's
  fixed set: `duplicate_object_key`, `float_forbidden`, `integer_out_of_safe_range`,
  `integer_out_of_sqlite_range`, `object_key_not_string`, `unsupported_json_type`,
  `nul_byte_forbidden`, `lone_surrogate`, `actor_id_malformed`, `invalid_actor_type`,
  `invalid_coverage_value`, `invalid_timestamp`, `timestamp_timezone_missing`, `timestamp_not_utc`,
  `timestamp_submillisecond_precision`, `timestamp_out_of_range`, `invalid_frontier`,
  `frontier_digest_mismatch`, `empty_subject_state`, `invalid_subject_state`, `invalid_digest`,
  `invalid_commitment`, `invalid_duration`, `noncanonical_integer_string`, `nesting_too_deep`,
  plus the codes raised through
  `protocol.ids.validate_id`.
- Reason codes never embed caller input; no user text can leak through an exception message.
- `freeze_json` on deeply nested input is bounded by the frozen `MAX_JSON_DEPTH = 64` levels
  imported from `protocol/canonical.py` (this module defines no mirror) and raises the owner's
  `ProtocolValueError("nesting_too_deep")`, so hostile payloads cannot trigger recursion overflow.
- `Timestamp` equality across identical instants with different spellings cannot occur: only the
  canonical spelling constructs.
- An accepted zero-offset named timezone is converted to `timezone.utc` before duration arithmetic;
  crossing a transition in the original zone therefore cannot change the requested UTC duration.

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
  bool-before-int trap; exact built-in and hostile-subclass gates; canonical string/key checks;
  depth bound; JsonObject duplicate-key and hash/equality laws.
- `tests/unit/domain/test_values.py` — constructor tables, the full subject-state relation matrix,
  frontier codec round trips, exact validation precedence, bounded UTC duration arithmetic including
  a zero-offset-zone transition vector, and Hypothesis coverage for
  `render_wire_sequence ∘ parse_wire_sequence`, timestamp round-trip, and `freeze_json`
  idempotence.
- Determinism controls required by ADR-002: identical bytes under varied `PYTHONHASHSEED`,
  locale, and timezone.

## Open questions

None.
