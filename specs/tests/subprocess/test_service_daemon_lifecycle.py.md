# tests/subprocess/test_service_daemon_lifecycle.py — real daemon singleton/crash/lifecycle suite

**Wave:** C | **ADRs:** ADR-001, ADR-008 | **Imports (spec-tree):** daemon/lifecycle specs | **Imported by:** test runner

## Purpose

Verify foreground daemon, second-instance rejection, state transitions, signals, stale endpoint and crash takeover.

## Public surface

PTY/subprocess harness with isolated platform dirs, fault points, and structural status probes.

## Behavior

Race two services, kill every startup/drain phase, advance generations, test TERM/INT/KILL and manager foreground behavior.

## Errors and edge cases

Crash around lock/endpoint/catalog/vault/app publication; stale symlink/foreign socket; commit during stop.

## Invariants

1. One daemon publishes/opens authority.
2. Successor stale writes fail.

## Tests

This file is the executable owner.

## Open questions

None.
