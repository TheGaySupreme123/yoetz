# tests/subprocess/test_service_unlock_boundary.py — TTY-only unlock boundary suite

**Wave:** C | **ADRs:** ADR-004, ADR-008 | **Imports (spec-tree):** CLI unlock/secret-ingress specs | **Imported by:** test runner

## Purpose

Prove initialization/unlock cannot come from argv/env/config/stdin/file/pipe/MCP/agent-like
non-TTY process.

## Public surface

PTY/process-group/signal/EOF/timeouts and forbidden-source subprocess cases.

## Behavior

Verify YZH1 service-minted challenge/preview, direct `/dev/tty` no-echo read, distinct purpose
preview, exact 16..1,024-byte strict-UTF-8/no-NUL-CR-LF/no-normalization rules, initialization and
portable-create two local matching entries/one send, unlock and portable-restore one entry/one send,
mismatch sends nothing, terminal restoration, buffer overwrite and structural output.

## Errors and edge cases

No controlling TTY, background group, redirected descriptors, Ctrl-C/TERM/EOF, oversized input, service generation change.
Wrong/crossed endpoint magic, invented binding, and OS-keyring retry accidentally opening YZS1 are
also fatal.

## Invariants

1. No stdin or password-FD fallback.
2. MCP registry cannot invoke helper.
3. Existing vault state cannot invoke first-install initialization.
4. Keyring retry uses one zero-secret YZH1 action and never a zero-length YZS1 frame.
5. Recovery create/restore prompt counts and operation bindings cannot be crossed.

## Tests

This file is the executable owner.

## Open questions

None.
