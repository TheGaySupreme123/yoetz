# tests/subprocess/test_signals_and_cancellation.py — signal/cancellation phase semantics

**Wave:** C–F | **ADRs:** ADR-001, ADR-003, ADR-005 | **Imports (spec-tree):** application/unit-of-
work/MCP specs and child/fault helpers | **Imported by:** subprocess/nightly/release gates

## Purpose

Verify SIGINT, SIGTERM, MCP cancellation, and client EOF at every meaningful application phase,
including the cancellation-shielded commit, provider wait, output delivery, checkpoint, and shutdown.

## Public surface

Matrix dimensions: signal/cancel kind × idle/validation/object-stage/pre-transaction/in-commit/
provider-wait/post-commit-output/checkpoint/shutdown × CLI/MCP. Each case declares expected exit,
durable phase/frontier, retry result, and server-next-request behavior.

## Behavior

Synchronize on test-only phase markers, deliver one signal/cancel, then capture streams/exit and
reopen normally. Before commit, no partial event batch is visible and staged garbage is recoverable.
Inside the smallest shielded commit, cancellation waits for commit outcome. After commit/before
response, outcome is unknown to client but same request returns the one result. Provider wait
cancels/records only legal durable phase; late fake output cannot publish stale semantic findings.

During idle/shutdown, server stops accepting new work, drains/cancels according to policy, releases
owner generation safely, and leaves no child. MCP cancellation propagates its class rather than
`INTERNAL_ERROR`; CLI SIGINT exits 130 without traceback.

## Errors and edge cases

- Two signals follow the documented escalation path but cannot interrupt SQLite halfway through an
  acknowledged transaction.
- Signal availability differs by platform; advertised platforms run their exact supported matrix.
- Marker timeout is harness failure, not handled cancellation.
- User content/exception text never appears in diagnostics.

## Invariants

1. Cancellation records only valid durable operation states.
2. Commit ambiguity is resolved through idempotent retry.
3. No stale semantic result or second steering publication occurs.
4. Server remains synchronized or terminates cleanly.

## Tests

Each matrix cell asserts database chain/object inventory/projection and exact output. Repeat critical
pre/post-commit cells with CLI and raw MCP on macOS/Linux.

## Open questions

None.
