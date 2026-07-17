# fixtures/adversarial/ADV-002-omitted-failed-test.case.json — omitted failed test adversarial case

**Wave:** A-B | **ADRs:** ADR-002 | **Imports (spec-tree):** protocol schemas, fixture case contract, owning policy specs | **Imported by:** tests/conformance/honesty/test_adversarial_cases.py, tests/unit/kernel/test_deterministic_checks.py

## Purpose

Freeze detection of an undisclosed failed required verification as synthetic, public, deterministic evidence before implementation.

## Public surface

One canonical strict-JSON fixture case with `fixture_schema: "yoetz.fixture-case/1.0.0"`, `fixture_version: "1.0.0"`, `fixture_id: "ADV-002"`, a publication-safe purpose, owning requirement IDs, minimum component versions, deterministic controls, typed input variants, and typed expected assertions.

## Behavior

The `input` section contains trigger, disclosed-partial, and revised-plan sequences with a fixed
failing result and bounded failure excerpt. The `expected` section freezes `failed_work_omitted`
only when the completion claim hides the failure, plus its exact `FindingBasis` trigger/missing fact
codes and refs. A separate orphan-result variant freezes `result_without_action` only when its
`action_ref` is absent or inconsistent; linking the exact action is the closest non-trigger. A
separate typed-limitation variant freezes `material_limitation_omitted` when the completion claim's
support refs omit the exact recorded `failure|partial|unknown` result, while linking that limiting
record is the remediation/non-trigger. The assisted semantic variant receives that basis/excerpt
and returns a direct-agent challenge requesting acknowledgement/action or a revised claim; the
disclosed variant produces no
spurious challenge. Every referenced identifier, timestamp, key, digest, nonce, provider response,
and fault point is explicit test data; a test may not replace it with current time, randomness,
network state, or host paths. Multi-variant cases evaluate each variant independently and declare
the relationship between their outcomes.

## Errors and edge cases

The loader rejects a wrong fixture ID/schema, undeclared field, float, duplicate key, invalid base64/hex, member digest mismatch, unknown control token, unsorted set-valued field, or reference outside this case. Rejection diagnostics identify the fixture and structural pointer but never echo secret-shaped fixture values.

## Invariants

The file is canonical JSON, self-contained, offline, synthetic, immutable after release, and has one unambiguous expected outcome per declared variant. It cannot strengthen coverage or assurance beyond the owning protocol and policy specs.

## Tests

Consumed directly by `tests/conformance/honesty/test_adversarial_cases.py` and `tests/unit/kernel/test_deterministic_checks.py`; fixture manifest and packaging tests additionally lock its exact bytes.

## Open questions

None.
