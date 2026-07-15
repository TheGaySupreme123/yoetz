# tests/property/test_service_control_frames.py — bounded control-frame property suite

**Wave:** C | **ADRs:** ADR-008 | **Imports (spec-tree):** `src/yoetz/service/control_protocol.md` | **Imported by:** test runner

## Purpose

Fuzz canonical round trips and prove bounded allocation/rejection for arbitrary frame bytes.

## Public surface

Hypothesis strategies/properties for lengths, JSON structures, fragmentation and method envelopes.

## Behavior

Valid frames round-trip canonically; invalid frames terminate boundedly; no input causes allocation above cap or secret-method acceptance.

## Errors and edge cases

All split points, Unicode/duplicate/surrogate/number cases, cap boundaries and concatenated frames.

## Invariants

1. Parser memory is bounded.
2. Accepted values satisfy frozen schemas.

## Tests

This file is the executable owner.

## Open questions

None.
