# fixtures/adversarial/ADV-011-config-evidence-does-not-satisfy-transport.case.json — config evidence does not satisfy transport

**Wave:** A-D | **ADRs:** ADR-002, ADR-004 | **Imports (spec-tree):** protocol schemas, fixture case contract, work-integrity policy | **Imported by:** tests/conformance/honesty/test_adversarial_cases.py, tests/unit/kernel/test_verification_classes.py

## Purpose

Freeze the orthogonal verification-class gate: a resolved obligation that requires
`integration_transport` and `live_smoke` cannot be satisfied by linked `unit_config` evidence alone.

## Public surface

One canonical strict-JSON fixture case with `fixture_schema: "yoetz.fixture-case/1.0.0"`,
`fixture_version: "1.0.0"`, `fixture_id: "ADV-011"`, a publication-safe purpose, owning requirement
IDs, minimum component versions, deterministic controls, typed input variants, and typed expected
assertions.

## Behavior

The `trigger` variant publishes a resolved obligation with
`required_verification_classes: [integration_transport, live_smoke]` and linked evidence declaring
only `unit_config`. Expected finding kind is `verification_class_unsatisfied` with missing-fact codes
`unsatisfied_class_integration_transport` and `unsatisfied_class_live_smoke`. The
`closest_non_trigger` variant links evidence declaring both required classes and expects no such
finding. Classes never satisfy each other; producers that auto-stamp classes remain future work.

## Errors and edge cases

The loader rejects a wrong fixture ID/schema, undeclared field, float, duplicate key, invalid
base64/hex, member digest mismatch, unknown control token, unsorted set-valued field, or reference
outside this case. Rejection diagnostics identify the fixture and structural pointer but never echo
secret-shaped fixture values.

## Invariants

The file is canonical JSON, self-contained, offline, synthetic, immutable after release, and has one
unambiguous expected outcome per declared variant. It cannot strengthen coverage or assurance beyond
the owning protocol and policy specs.

## Tests

Consumed directly by `tests/conformance/honesty/test_adversarial_cases.py`; fixture manifest and
packaging tests additionally lock its exact bytes. Unit coverage lives in
`tests/unit/kernel/test_verification_classes.py`.

## Open questions

None.
