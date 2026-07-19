# tests/unit/privacy/test_catalog_audit.py — privacy catalog parity tests

## Purpose

Verify provider-free memory/SQLite privacy persistence parity.

## Public surface

No runtime surface; pytest tests only.

## Behavior

Exercises policy generations, atomic objectless projection, replay, cursors, and object roots.

## Errors and edge cases

Tampered cursors and contradictory replay fail closed.

## Invariants

Objectless projection never stores a content object.

## Tests

This file is the test implementation.

## Open questions

None.
