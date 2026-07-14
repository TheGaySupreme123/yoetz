# tests/subprocess/test_privacy_human_control.py — foreground privacy decision boundary suite

**Wave:** C/E | **ADRs:** ADR-008, ADR-009 | **Imports (spec-tree):** CLI/service human-control specs | **Imported by:** test runner

## Purpose

Prove sensitive previews/decisions stay on foreground TTY/confidential channel and never MCP/stdout/logs.

## Public surface

PTY/background/redirection/pipe/JSON/MCP/boolean/signal/timeout and reauth cases.

## Behavior

Open YZH1 and render exact previews to TTY. For policy widening, approve only with a bound measured
presence attestation or established-passphrase YZS1 reauthentication. For a
`confirm_every_request` case already within durable policy, approve/deny directly on TTY with no
reauthentication frame and prove no durable policy change. Scan forbidden surfaces and verify only
structural results.

## Errors and edge cases

No TTY, stale digest, edit, interruption, relock, attempted `--yes`/stdin/MCP decision.

## Invariants

1. Boolean confirmation is not human authority.
2. Helper never receives reusable proof.
3. A pending ID alone cannot reach YZS1 or authorize a decision.
4. TTY case consent cannot widen policy, and strong policy proof is not required for an exact
   within-policy confirm-every-request decision.

## Tests

This file is the executable owner.

## Open questions

None.
