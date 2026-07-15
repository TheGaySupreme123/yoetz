# tests/unit/service/test_confidential_client.py — client-safe confidential state machine tests

**Wave:** D | **ADRs:** ADR-004, ADR-008, ADR-009 | **Imports (spec-tree):**
`src/yoetz/service/confidential_client.md`, confidential golden frames | **Imported by:** unit
suite and client import-boundary gate

## Purpose

Prove the trusted helper client follows exact YZH1/YZS1 sequencing and cannot import, construct, or
invoke server authority.

## Public surface

Tests for open/session/action/phase/result/error/close, package-private session-token opacity,
one-send mutable secret handling, connector calls, cancellation, and transitive imports.

## Behavior

Use scripted byte streams and peer identities. Assert one live ceremony, exact service/ceremony/
step correlation, token creation only after server-opened, secret send only for the matching phase,
zero YZS1 response bytes, source overwrite on every exit, and bounded structural return only.

## Errors and edge cases

Wrong peer, stale/crossed binding, duplicate send, wrong purpose, partial write, response byte,
missing close, replay, and cancellation close the session. No raw frame/socket-path/standalone-
secret constructor or automatic retry may exist.

## Invariants

1. Transitive imports exclude daemon, human-control/ingress servers, unlock, vault, application,
   keys, and providers.
2. The client never mints a challenge, authority proof, or reusable token.
3. Mutable secret input is overwritten best effort on success and failure.

## Tests

This file is the executable owner; packaging tests separately inspect installed import graphs.

## Open questions

None.
