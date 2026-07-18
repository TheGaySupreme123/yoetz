# tests/unit/domain/test_values.py — canonical domain value conversions

**Wave:** A/B | **ADRs:** ADR-002, ADR-003, ADR-005, ADR-011 | **Imports (spec-tree):**
`src/yoetz/domain/values.md`, `src/yoetz/protocol/canonical.md`,
`src/yoetz/protocol/models.md`
**Imported by:** the domain unit suite

## Purpose

Lock the frozen domain value helpers that turn parsed JSON and wire primitives into stable domain
values without allowing lossy coercion.

## Public surface

- `test_freeze_json_accepts_only_safe_json_shapes` — valid parsed JSON becomes frozen domain JSON.
- `test_freeze_json_rejects_noncanonical_strings_and_subclasses` — exact built-in scalar/key rules,
  NUL/lone-surrogate reasons, and hostile subclasses are fail closed.
- `test_id_constructors_snapshot_valid_string_subclasses` — validation preserves characters while
  the domain value becomes an exact built-in string.
- `test_actor_validation_is_nominal_and_ordered` — actor ID, domain actor type, and server-assigned
  assurance use their exact types, reasons, and precedence.
- `test_timestamp_round_trip_is_utc_exact` — timestamp formatting and parsing stay exact.
- `test_timestamp_and_duration_validation_order_is_frozen` — exact datetime type, timezone,
  precision, duration range, and overflow reasons have one precedence.
- `test_utc_duration_arithmetic_ignores_named_zone_transitions` — an accepted zero-offset named
  zone is normalized before arithmetic, including a synthetic DST-transition vector.
- `test_wire_sequence_parsing_rejects_noncanonical_strings` — canonical decimal strings only.
- `test_frontier_json_codec_is_closed_and_invertible` — the two-key wire object is the sole
  frontier representation and round-trips exactly.
- `test_subject_state_ref_validation_is_strict` — subject-state references remain bounded and exact.
- `test_subject_state_relation_full_matrix` — the complete absent/present tree-A/tree-B relation
  matrix, including the adversarial stale-after-edit vectors, follows the honesty rule.
- `test_subject_state_capture_result_validation` — only a complete ADR-011 capture may carry
  comparable digests; partial/unsupported/changing results carry none.
- `test_generated_value_round_trips_are_idempotent` — Hypothesis covers wire sequences, UTC
  timestamps, and recursive safe JSON values across deterministic environment controls.

## Behavior

The suite proves:

- domain value freezing preserves JSON structure but accepts only exact built-in scalars/arrays,
  exact built-in object keys, or actual mappings; validates every string/key for NUL and lone
  surrogates; maps actual float classes to `float_forbidden`; and maps Decimal, Fraction, complex,
  integer subclasses, and other unsupported objects to `unsupported_json_type` without coercion;
- direct `JsonObject` construction accepts only an actual mapping or an exact list/tuple of exact
  two-item tuple pairs; the pair form proves duplicate detection wins before the rejected value is
  inspected, while malformed pair containers fail with `unsupported_json_type`;
- valid ID string subclasses are validated by the ID owner and then snapshotted into exact built-in
  `str` domain values without changing characters;
- `Actor` rejects a foreign/raw actor token with `invalid_actor_type`, rejects a foreign/raw
  assurance with `invalid_coverage_value`, and checks actor ID before both;
- UTC timestamps round-trip with exact formatting; datetime subclasses, naive/nonzero-offset
  samples, and sub-millisecond samples have distinct fixed reasons and no value is truncated;
- `add_utc_milliseconds` accepts only exact positive safe-integer durations, maps construction or
  calendar overflow to `timestamp_out_of_range`, and normalizes a zero-offset named zone before
  arithmetic so crossing its later transition still adds the exact UTC duration;
- wire sequences delegate to the canonical helpers: parse over-range remains
  `noncanonical_integer_string`, while render over-range is `integer_out_of_sqlite_range`;
- frontiers obey canonical integer-string/genesis/digest rules, reject every missing/extra/wrong
  wire field, preserve the parser reason for a bad sequence, and satisfy
  `frontier_from_json(frontier.as_wire()) == frontier`;
- subject-state references require exact built-in bounded canonical described text and cannot
  smuggle hidden state, NUL/lone-surrogate content, or an unbounded label;
- `subject_state_relation` returns `different` only for two present unequal tree digests and the
  full relation matrix is locked against the worked state-A/state-B vector in
  `specs/src/yoetz/domain/values.md`;
- multi-defect examples lock the owner spec's exact validation order rather than accepting whichever
  branch happens to fail first;
- generated values prove `render_wire_sequence(parse_wire_sequence(x))`, timestamp
  string/datetime/string, and `freeze_json(freeze_json(x))` identities across hash seed, locale,
  timezone, Unicode, nesting, and integer boundaries.

## Errors and edge cases

- Any coercion from floats, bytes, scalar/container subclasses, or arbitrary objects fails; a
  `str` subclass is accepted only by the ID owner and is snapshotted before entering the domain.
- NUL and lone-surrogate strings/keys retain the canonical owner's exact reasons.
- Any timestamp offset other than UTC fails, as do naive, subclassed, and sub-millisecond inputs;
  no timestamp helper repairs or truncates them.
- Duration `bool`, zero, negative, subclassed, and greater-than-safe-integer values fail with
  `invalid_duration`; calendar/timedelta overflow fails with `timestamp_out_of_range`.
- Frontier shape/genesis/digest defects fail with `invalid_frontier`, while a present malformed or
  over-range sequence retains `noncanonical_integer_string`.
- Subject described-state wrong type/length fails with `invalid_subject_state`; NUL and surrogate
  failures retain their canonical reasons.
- A subject-state relation that infers change from one missing digest fails.
- A property strategy that filters away a boundary or fails to shrink reproducibly is invalid.

## Invariants

1. Domain values stay JSON-compatible and frozen.
2. Canonical wire primitives remain canonical.
3. Helper functions are deterministic and I/O-free.
4. Subject-state change is claimed only from two present, unequal tree digests.
5. Accepted value round trips are idempotent across deterministic environment variants.
6. Failure precedence is stable for multi-defect values and never depends on hash order, locale,
   timezone, or subclass callbacks.

## Tests

- `tests/unit/domain/test_values.py`

## Open questions

None.
