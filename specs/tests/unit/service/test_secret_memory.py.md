# tests/unit/service/test_secret_memory.py — protected secret-memory contract unit suite

**Wave:** C | **ADRs:** ADR-004, ADR-008 | **Imports (spec-tree):** `src/yoetz/ports/secret_memory.md`, `adapters/keys/secret_memory.md` | **Imported by:** test runner

## Purpose

Verify all six purpose/consumer/bound/one-shot/redaction/overwrite semantics without overclaiming
zeroization.

## Public surface

Tests for capture/allocate/consume/close, fault/cancel, copy/pickle/repr, retained views, fork and
stale generation, plus exact `ProviderAttemptAuthBinding` and custom-transport callback behavior.

## Behavior

Instrument buffers to prove source and allocation overwrite attempts on every path and truthful
page-lock capability reporting. For provider credentials, prove one handle is bound to one
provider/model/endpoint profile/version/purpose/dispatch/final-body digest/deadline/generation,
exposes its view only to the injected custom transport during header injection/request start, and
is consumed before retry. SDK client/default headers see only the nonsecret sentinel.

## Errors and edge cases

Wrong purpose/consumer including initialize/unlock substitution, second consume, oversize, callback
retention/exception/cancel, close/fork, body/profile/deadline mismatch, generic/stock transport,
redirect, callback reuse, and relock during injection.

## Invariants

1. No raw immutable secret return.
2. Capability evidence never assumes platform behavior.
3. Provider credential bytes never become reusable SDK state; every physical attempt gets one exact
   scoped callback.

## Tests

This file is the executable owner.

## Open questions

None.
