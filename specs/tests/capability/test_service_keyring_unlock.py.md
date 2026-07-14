# tests/capability/test_service_keyring_unlock.py — real service vault/keyring capability probe

**Wave:** C/F | **ADRs:** ADR-004, ADR-008 | **Imports (spec-tree):** OS keyring/secret-memory/vault specs | **Imported by:** release evidence

## Purpose

Prove disposable OS-keyring root-key creation/load/lock/retry plus measured memory hardening on advertised platforms.

## Public surface

Capability evidence test with isolated disposable entries and cleanup.

## Behavior

Exercise backend identity, create-if-absent support, correlation-bound create/round trip, and the
exact same-artifact intersection with `test_user_presence` evidence. Pristine create is eligible
only when both cells pass. Also cover locked pristine re-probe, usable-keyring/nonpassing-presence
no-write results, locked existing-entry load-only retry, existing ready-local without presence,
missing/mismatched entry refusal, service availability, and page-lock/no-core-dump evidence; never
print a secret or correlation.

## Errors and edge cases

Prompt denied, backend missing/unverified, cleanup failure, no interactive runner.

## Invariants

1. Only positive real evidence supports a platform claim.
2. Existing missing key never auto-replaces.
3. Only a correlated exact entry+sentinel+mode supports an advertised ready result.
4. A passing keyring cell alone cannot authorize pristine initialization; the exact active
   user-presence cell for the same candidate artifact is mandatory.

## Tests

This file emits bounded structural capability evidence.

## Open questions

None.
