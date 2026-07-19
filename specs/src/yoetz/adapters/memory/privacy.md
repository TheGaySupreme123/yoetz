# src/yoetz/adapters/memory/privacy.py — in-memory privacy catalog reference

**Wave:** C–E | **ADRs:** ADR-008, ADR-009 | **Imports:** privacy domain/ports and canonical
catalog helpers | **Imported by:** provider-free conformance and application tests

## Purpose

Provide a process-local reference implementation of privacy policy and audit persistence whose
observable transitions match the SQLite catalog.

## Public surface

`MemoryPrivacyCatalogState`, `MemoryPrivacyPolicyStore`, and `MemoryPrivacyAudit`.

## Behavior

Policy generation/CAS, objectless projection, encrypted proposal roots, receipt queries, and
authenticated pagination follow the catalog contract.

## Errors and edge cases

Stale policy, contradictory replay, bad cursor, wrong route, or invalid transition fails closed.
Network authorization and dispatch remain unavailable until B8.

## Invariants

Structural state contains no key, credential, provider response, or plaintext proposal body.
Objectless projection never calls the object store.

## Tests

Memory/SQLite parity covers generations, replay, cursor tampering, roots, and empty projection.

## Open questions

None.
