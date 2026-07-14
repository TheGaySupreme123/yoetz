# tests/unit/service/test_control_protocol.py — frozen service-control protocol unit suite

**Wave:** C | **ADRs:** ADR-008 | **Imports (spec-tree):** `src/yoetz_core/service/control_protocol.md`, `ports/control.md` | **Imported by:** test runner

## Purpose

Freeze ordinary local-control framing, handshake, method registry, schema binding, and secret-field impossibility.

## Public surface

Parametrized tests for every frame/method/state/client kind, canonical golden bytes, and malformed input.

## Behavior

Cover the 6,291,456-byte absolute frame guard, 1,048,576-byte ordinary guard, bounded 4 MiB import
exception, partial/extra bytes, strict JSON, hello negotiation, one-way cancellation/original-call
correlation, closed schema branches, method limits, and fixed errors.

## Errors and edge cases

Exercise zero/ordinary-cap/import-cap/absolute-cap plus one, stale generation mapping through
`service_generation_changed` to public `SERVICE_UNAVAILABLE`, duplicate RPC ID, unknown method,
`privacy_projection_unavailable` to retryable public `SERVICE_UNAVAILABLE`,
cross-paired/invalid nested result, and every forbidden secret-like field.

## Invariants

1. Golden bytes and five schemas agree.
2. No unlock/credential/key/passphrase method or field is representable.

## Tests

This file is the executable owner; no network/keyring dependency.

## Open questions

None.
