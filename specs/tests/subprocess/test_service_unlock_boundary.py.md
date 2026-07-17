# tests/subprocess/test_service_unlock_boundary.py — TTY-only unlock boundary suite

**Wave:** C | **ADRs:** ADR-004, ADR-008 | **Imports (spec-tree):** CLI unlock/secret-ingress specs | **Imported by:** test runner

## Purpose

Prove initialization/unlock cannot come from argv/env/config/stdin/file/pipe/MCP/agent-like
non-TTY process.

## Public surface

PTY/process-group/signal/EOF/timeouts, idle-relock policy changes, and forbidden-source subprocess
cases.

## Behavior

Verify YZH1 service-minted challenge/preview, direct `/dev/tty` no-echo read, distinct purpose
preview, exact 16..1,024-byte strict-UTF-8/no-NUL-CR-LF/no-normalization rules, initialization and
portable-create two local matching entries/one send, unlock and portable-restore one entry/one send,
mismatch sends nothing, terminal restoration, buffer overwrite and structural output.
Exercise `service idle-relock` with exact targets `60`, `900`, `86400`, and `disabled`; reject
`59`, `86401`, `0`, `00`, `060`, `+60`, `-60`, `60.0`, whitespace/suffix variants, empty input,
and null/infinity aliases before any endpoint connection. Freeze the current/proposed/generation/
scope/restart preview, deny with no YZS1 frame, and approve through both measured OS presence and
passphrase-mode `security_reauthentication`. Applied output is exact previous/effective tagged
policy plus `scope=service_generation` and generation; denial is an exit-0 structural no-mutation
result. Restart restores 900 seconds, and explicit/session/suspend/monitor-loss locking remains
active after disable.

## Errors and edge cases

No controlling TTY, background group, redirected descriptors, Ctrl-C/TERM/EOF, oversized input, service generation change.
Wrong/crossed endpoint magic, invented binding, and OS-keyring retry accidentally opening YZS1 are
also fatal.
Idle-relock through `ServiceClient`, raw ordinary control, MCP, config/env, `--yes`, stdin, privacy/
provider reauthentication, stale preview, expiry, or connection-close inference is fatal and cannot
change the policy.

## Invariants

1. No stdin or password-FD fallback.
2. MCP registry cannot invoke helper.
3. Existing vault state cannot invoke first-install initialization.
4. Keyring retry uses one zero-secret YZH1 action and never a zero-length YZS1 frame.
5. Recovery create/restore prompt counts and operation bindings cannot be crossed.
6. Idle-relock target bytes are nonsecret but non-authoritative; only the exact current-generation
   YZH1 ceremony and matching vault-minted proof can apply them.

## Tests

This file is the executable owner.

## Open questions

None.
