# src/yoetz/ports/check_sandbox.py — enforcing approved-check sandbox boundary

**Wave:** D | **ADRs:** ADR-009, ADR-010 | **Imports (spec-tree):** — |
**Imported by:** `adapters/check_sandbox.py.md`, `adapters/approved_checks.py.md`

## Purpose

Define `CheckSandboxPort` so approved checks can require an enforcing no-network process sandbox.
Unsupported environments return `sandbox_unavailable` and must never claim network denial from an
environment variable alone.

## Public surface

- `CheckSandboxStatus`, `CheckSandboxLaunch`, `CheckSandboxPort`

## Behavior

`prepare(argv, cwd, env, deny_network=True)` returns a launch plan. When ready and network is
denied, `network_isolated` is true only if the platform sandbox actually enforces isolation.

## Tests

`tests/unit/adapters/test_check_sandbox.py`
