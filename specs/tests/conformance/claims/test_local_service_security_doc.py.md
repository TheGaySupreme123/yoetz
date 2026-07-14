# tests/conformance/claims/test_local_service_security_doc.py — public local-service claim conformance

**Wave:** F | **ADRs:** ADR-008 | **Imports (spec-tree):**
`docs/protocol/local-service-security.md`, public claim map | **Imported by:** test runner

## Purpose

Keep the public security explanation complete and no stronger than executable contracts.

## Public surface

Document-section, terminology, claim-map, link, and forbidden-overclaim assertions.

## Behavior

Require states, clients, secret surfaces, same-UID/root/live-memory limits, relock, headless/native-
vault limitations, the pristine keyring-plus-presence no-write gate, existing-keyring ready-local
distinction, bounded setup reason, and test mappings.

## Errors and edge cases

Detect keyring-only automatic mode selection, keyring fallback, MCP unlock, deletion/zeroization,
service-manager-ready, or compromised-user protection claims.

## Invariants

1. Every promise maps to ADR/spec/test evidence.
2. No private docs or sample secrets.

## Tests

This file is the executable owner.

## Open questions

None.
