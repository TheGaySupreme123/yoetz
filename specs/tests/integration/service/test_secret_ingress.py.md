# tests/integration/service/test_secret_ingress.py — confidential purpose/binding integration

**Wave:** C | **ADRs:** ADR-004, ADR-008 | **Imports (spec-tree):** secret-ingress/unlock/vault specs | **Imported by:** test runner

## Purpose

Verify binary one-shot ingress for initialization, unlock, recovery, provider credential and
reauthentication.

## Public surface

Same-UID socket/challenge/secret-memory/vault integration matrix.

## Behavior

Exercise all six exact purpose/state/target/policy bindings, initialize/unlock and provider-reauth/
credential non-substitution,
portable recovery request+confirmed-plan binding, 60-second expiry, generation, rate limit,
consume/overwrite and structural results.
For every secret purpose, exercise its exact minimum/maximum and byte policy. Provider credentials
cover generic 0/1/8,192/8,193 and NUL/CR/LF rejection plus selected-profile validator rejection
before storage; the OpenAI profile's stricter vectors are owned by its adapter tests.

## Errors and edge cases

Wrong purpose/state/peer/challenge/generation, existing/ambiguous vault initialization, stale
recovery plan, zero/oversize/partial/extra, disconnect/cancel/session lock.

## Invariants

1. Secret never touches ordinary control/application.
2. One challenge accepts at most one bounded secret.

## Tests

This file is the executable owner.

## Open questions

None.
