# tests/subprocess/test_mcp_backpressure_and_partial_write.py — slow-client and short-write safety

**Wave:** D/F | **ADRs:** ADR-005 | **Imports (spec-tree):** stdio spec and frame/child helpers |
**Imported by:** subprocess/nightly transport gates

## Purpose

Prove the sole stdout writer handles partial writes, EINTR, readiness delay, paused readers, broken
pipes, and cancellation without truncating/interleaving frames or accumulating unbounded output.

## Public surface

Parameterized write schedules: one-byte, alternating short lengths, EINTR before/during, delayed
readiness, reader paused then resumed, reader never reads, broken pipe at byte offsets, concurrent
completed requests, and shutdown/cancellation while blocked.

## Behavior

Inject descriptor behavior through test-only shims. Drive known responses with unique structural
IDs, capture system-call schedule and raw stdout. The writer must loop until a whole `frame + LF` is
written, serialize concurrent responses at frame boundaries, and apply zero-/bounded-capacity
backpressure upstream. Peak queued bytes/RSS remain within configured caps.

When reader resumes, output equals complete golden frames in permitted response order. When it never
reads or breaks, the server reaches the bounded delivery failure/shutdown path; already committed
operation remains retry-resolvable, and no partial frame is treated as acknowledged.

## Errors and edge cases

- OS may return fewer bytes without error; any dropped/duplicated segment fails.
- EINTR is retried unless cancellation/shutdown owns the transition.
- Timing uses readiness/marker synchronization, not sleep-only assertions.
- Production artifact cannot enable I/O shims.

## Invariants

1. At most one task writes stdout and frames never interleave.
2. Output buffering is bounded under a non-reading client.
3. Delivery failure cannot alter durable operation outcome.
4. Every successful response ends in exactly one LF.

## Tests

Run small/cap-sized results, simultaneous calls, committed-before-broken-pipe retry, and release-hook
denial on macOS/Linux installed artifacts.

## Open questions

None.
