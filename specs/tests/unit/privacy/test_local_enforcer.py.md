# tests/unit/privacy/test_local_enforcer.py — deterministic local privacy tests

## Purpose

Verify exact scan reuse, scope, F-018 audiences, and F-019 provenance.

## Public surface

No runtime surface; pytest tests only.

## Behavior

Exercises deterministic classification, minimization, and coordinator policy decisions.

## Errors and edge cases

Ambiguous provenance, cross-task content, sensitive data, and never-send findings deny widening.

## Invariants

Local human view remains scope/scan bound; agent widening requires trusted authorship.

## Tests

This file is the test implementation.

## Open questions

None.
