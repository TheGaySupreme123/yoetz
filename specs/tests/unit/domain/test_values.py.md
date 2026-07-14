# tests/unit/domain/test_values.py — canonical domain value conversions

**Wave:** A/B | **ADRs:** ADR-002, ADR-003, ADR-005 | **Imports (spec-tree):**
`src/yoetz_core/domain/values.md`, `src/yoetz_core/protocol/canonical.md`,
`src/yoetz_core/protocol/models.md`
**Imported by:** the domain unit suite

## Purpose

Lock the frozen domain value helpers that turn parsed JSON and wire primitives into stable domain
values without allowing lossy coercion.

## Public surface

- `test_freeze_json_accepts_only_safe_json_shapes` — valid parsed JSON becomes frozen domain JSON.
- `test_timestamp_round_trip_is_utc_exact` — timestamp formatting and parsing stay exact.
- `test_wire_sequence_parsing_rejects_noncanonical_strings` — canonical decimal strings only.
- `test_subject_state_ref_validation_is_strict` — subject-state references remain bounded and exact.
- `test_subject_state_relation_full_matrix` — the complete absent/present tree-A/tree-B relation
  matrix, including the adversarial stale-after-edit vectors, follows the honesty rule.
- `test_generated_value_round_trips_are_idempotent` — Hypothesis covers wire sequences, UTC
  timestamps, and recursive safe JSON values across deterministic environment controls.

## Behavior

The suite proves:

- domain value freezing preserves JSON structure but rejects unsupported Python types;
- UTC timestamps round-trip with exact formatting;
- wire sequences and frontiers obey canonical integer-string rules;
- subject-state references cannot smuggle hidden state or unbounded text;
- `subject_state_relation` returns `different` only for two present unequal tree digests and the
  full relation matrix is locked against the worked state-A/state-B vector in
  `specs/src/yoetz_core/domain/values.md`;
- generated values prove `render_wire_sequence(parse_wire_sequence(x))`, timestamp
  string/datetime/string, and `freeze_json(freeze_json(x))` identities across hash seed, locale,
  timezone, Unicode, nesting, and integer boundaries.

## Errors and edge cases

- Any coercion from floats, bytes, or arbitrary objects fails.
- Any timestamp offset other than UTC fails.
- A subject-state relation that infers change from one missing digest fails.
- A property strategy that filters away a boundary or fails to shrink reproducibly is invalid.

## Invariants

1. Domain values stay JSON-compatible and frozen.
2. Canonical wire primitives remain canonical.
3. Helper functions are deterministic and I/O-free.
4. Subject-state change is claimed only from two present, unequal tree digests.
5. Accepted value round trips are idempotent across deterministic environment variants.

## Tests

- `tests/unit/domain/test_values.py`

## Open questions

None.
