# src/yoetz/adapters/check_sandbox.py — macOS/Linux CheckSandboxPort adapters

**Wave:** D | **ADRs:** ADR-009 | **Imports (spec-tree):** `ports/check_sandbox.py.md` |
**Imported by:** approved-check runner

## Purpose

Platform adapters for enforcing no-network check execution.

## Public surface

- `MacOSCheckSandbox` — `sandbox-exec` Seatbelt deny-network profile
- `LinuxCheckSandbox` — `bwrap --unshare-net` when available
- `UnsupportedCheckSandbox` — honest unavailable
- `default_check_sandbox()`

## Behavior

Never claim network isolation from env markers. Unavailable → `sandbox_unavailable`.

## Tests

`tests/unit/adapters/test_check_sandbox.py`
