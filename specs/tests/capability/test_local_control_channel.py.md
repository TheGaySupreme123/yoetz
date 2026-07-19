# tests/capability/test_local_control_channel.py — platform peer-credential capability probe

**Wave:** C/F | **ADRs:** ADR-008 | **Imports (spec-tree):** `src/yoetz/adapters/control/unix_socket.md` | **Imported by:** release evidence

## Purpose

Prove same-UID peer credential and owner-only Unix socket behavior on certified macOS/Linux.

## Public surface

Real disposable listener/client/foreign-UID-or-mocked-negative capability cases.

## Behavior

Verify getpeereid/SO_PEERCRED, modes, close-on-exec, stale socket safety, and both directions.

## Errors and edge cases

Unsupported API, wrong UID, symlink/non-socket, inherited descriptor.

## Invariants

1. Platform support requires positive peer identity evidence.
2. No TCP/token fallback.

## Tests

This file emits bounded structural capability evidence.

Run this capability file separately from the same-basename integration file until the Wave F/B9
runner selects pytest importlib mode or packages test directories; default pytest import mode
otherwise aliases the two modules during one collection.

## Open questions

None.
