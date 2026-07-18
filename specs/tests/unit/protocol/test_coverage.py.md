# tests/unit/protocol/test_coverage.py — coverage lattice and weakest-merge rules

**Wave:** A/B | **ADRs:** ADR-002, ADR-004, ADR-006 | **Imports (spec-tree):**
`src/yoetz/protocol/coverage.py`
**Imported by:** the protocol and kernel unit suite

## Purpose

Lock the coverage dimensions, ordering, and merge behavior so weaker evidence always stays weaker
and no helper accidentally computes an average.

## Public surface

- `test_ordered_dimensions_sort_as_defined` — ordered enums compare in the registry order.
- `test_weakest_merge_is_componentwise` — merge picks the weaker ordered value per dimension.
- `test_known_gaps_union_is_sorted_unique` — gaps are deduplicated and sorted.
- `test_known_gaps_union_overflow_fails_closed` — two valid 64-gap values with a 65-or-more-member
  union raise `invalid_known_gap` in either argument order and never truncate.
- `test_six_channel_defaults_are_exact` — each publication channel maps to the frozen seven-field
  default, including its exact known-gap tuple.
- `test_coverage_json_round_trip_is_exact` — JSON codecs preserve the exact closed object shape.
- `test_coverage_json_rejects_noncanonical_shapes` — missing/extra keys, wrong container types,
  and wrong enum/token shapes fail with `invalid_coverage_value`.
- `test_coverage_constructor_rejects_noncanonical_sets` — empty/duplicate/descending publication
  channels and check types, bad check-type shapes, and invalid known-gap tokens use exact reasons.
- `test_no_averaging_or_strengthening_exists` — no helper strengthens coverage without proof.

## Behavior

The suite proves that:

- coverage values follow the exact registry ordering;
- ordered dimensions never strengthen during merge;
- unordered kinds are preserved, not normalized into a fake scalar;
- constructors validate rather than sort: publication channels and check types are nonempty,
  ASCII-sorted, duplicate-free tuples; known gaps are at most 64 sorted unique lower-snake ASCII
  tokens of at most 128 bytes;
- constructor validation follows exact stored-field order on mixed-invalid inputs, tuple containers
  and enum/token members use actual exact runtime types, and spoofed `__class__` or hostile
  subclasses cannot enter the frozen value;
- JSON codecs accept only exact coverage-shaped mappings, preserve the seven closed keys and the
  list-backed field spelling, and round-trip through schema-valid wire values without coercion;
- all six defaults match `COVERAGE_DEFAULTS_BY_CHANNEL` byte-for-byte: cooperative MCP and local
  CLI are self-asserted/published-only/metadata-only/current; Codex import is
  self-asserted/import-observed/metadata-only/partial with
  `import_source_range_not_universal`; hook-observed is
  harness-observed/hook-observed/metadata-only/current; engine-derived is
  service-authenticated/published-only/metadata-only/current; human import is
  self-asserted/import-observed/metadata-only/partial with
  `human_import_scope_not_universal`; every row has `none` check type before stronger evidence;
- channel defaults claim only channel facts and never authenticate a caller-asserted author;
- weakest merge takes each ordered minimum, unions set fields in ASCII order, and removes `none`
  when any real check type is present;
- a gap union of at most 64 members is exact, while a larger union raises
  `ProtocolValueError("invalid_known_gap")` symmetrically before constructing a result; the test
  covers a 64-member boundary union, a 65-member union, and two disjoint valid 64-member inputs.
- `coverage_for_channel` rejects every non-`PublicationChannel` input and `weakest` rejects a
  non-`Coverage` left or right input with `invalid_coverage_value`, before duck-typed field access.

## Errors and edge cases

- A merge that strengthens evidence fails the test.
- A gap union that preserves duplicates fails the test.
- A gap union that truncates, chooses argument-order-dependent survivors, or emits an invalid
  over-cap `Coverage` fails the test.
- A codec round-trip that loses key order, enum spelling, or tuple/list structure fails the test.
- Exact reasons are asserted for `empty_publication_channels`, `invalid_publication_channels`,
  `empty_check_types`, `invalid_check_types`, `invalid_known_gap`, and
  `invalid_coverage_value`; codec shape failures use `invalid_coverage_value` and propagate the
  constructor-owned duplicate/unsorted/invalid-gap reasons unchanged.

## Invariants

1. Coverage is a lattice of honesty, not a score.
2. Merge semantics are deterministic.
3. Gap metadata survives replay.

## Tests

- `tests/unit/protocol/test_coverage.py`

## Open questions

None.
