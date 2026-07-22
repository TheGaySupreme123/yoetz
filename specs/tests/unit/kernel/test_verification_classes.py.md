# tests/unit/kernel/test_verification_classes.py — verification-class vocabulary and work-integrity gate

**Wave:** B | **ADRs:** ADR-002, ADR-004 | **Imports (spec-tree):**
`src/yoetz/domain/events.md`, `src/yoetz/kernel/policies/work_integrity.md`,
`src/yoetz/kernel/receipt_builder.md`, `src/yoetz/domain/receipts.md`
**Imported by:** the kernel unit suite

## Purpose

Lock the orthogonal `VerificationClass` vocabulary, optional obligation/evidence class fields,
the `verification_class_unsatisfied` deterministic rule, and receipt class-coverage disclosure.

## Public surface

- `test_verification_class_registry_is_exact_and_orthogonal` — closed six-class registry.
- `test_obligation_and_evidence_reject_unknown_duplicate_and_overlong_classes` — validation bounds.
- `test_optional_class_fields_round_trip_and_omit_when_empty` — encode/decode and legacy omit.
- `test_verification_class_unsatisfied_missing_one_all_and_legacy` — rule triggers and non-triggers.
- `test_receipt_discloses_satisfied_and_unsatisfied_verification_classes` — limitations disclosure.
- `test_receipt_obligation_class_fields_validate_bounds` — receipt obligation class consistency.

## Behavior

Assertions stay structural: exact class tokens, fact codes, and list membership. No prose
interpretation of filenames, commands, or evidence descriptions. Bounded producers remain out of
scope.

## Errors and edge cases

Unknown class tokens, unsorted/duplicate set fields, over-long lists, and satisfied classes outside
required sets are rejected.

## Invariants

Classes never satisfy each other; absent/empty required classes keep legacy class-free gating.

## Tests

This file is the owning unit suite for the Issue 5 verification-class surface.

## Open questions

None.
