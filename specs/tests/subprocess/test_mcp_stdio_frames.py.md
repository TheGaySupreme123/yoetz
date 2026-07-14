# tests/subprocess/test_mcp_stdio_frames.py — bounded LF-frame golden matrix

**Wave:** D/F | **ADRs:** ADR-005 | **Imports (spec-tree):** bounded stdio spec and frame/child helpers
| **Imported by:** transport and release gates

## Purpose

Prove exact input framing, caps, decoding, parse failures, recovery/termination, and EOF behavior for
the Yoetz-owned stdio adapter independently of MCP business logic.

## Public surface

Sixteen parameter families mirror the index matrix: blank/small/cap/cap+1, coalesced/split reads,
64-KiB read cap, invalid UTF-8/BOM/NUL/JSON/numbers, CRLF/extra LF, complete/partial EOF, oversized
unterminated, malformed-then-valid, unrecoverable ID, short writes/backpressure, broken pipe/fault.

## Behavior

Run each case in a fresh installed child with raw stimulus bytes and a minimal echo/parse endpoint.
Split valid frames at every byte boundary and test multiple frames per read. Assert which complete
messages reach the SDK, literal output/error frame bytes, exit, stderr class, and observed maximum
buffer/read size. Exactly 1 MiB excluding LF is accepted; one byte more is rejected before any
prefix is forwarded.

Malformed-frame continuation versus orderly termination follows the frozen golden vector. Complete
prior frames remain processed; partial/oversized input never becomes a message. EOF after complete
LF shuts down cleanly; partial EOF returns the bounded transport failure.

## Errors and edge cases

- Kernel read coalescing is not asserted; semantic chunks/frames are.
- Test diagnostics retain digests/offset class, never hostile bytes.
- Cap arithmetic excludes exactly the delimiter as specified; CR in CRLF is payload unless contract
  says otherwise.
- Timeout/deadlock is failing harness evidence.

## Invariants

1. Memory is bounded by frame/read/output caps.
2. Only a complete accepted LF frame reaches MCP.
3. No invalid prefix is forwarded or echoed.
4. Output frames have one LF and canonical protocol bytes.

## Tests

Run under both certified kernels and with synthetic OS short-read/write/EINTR schedules. Golden
vectors are public binary fixtures with digests.

## Open questions

None.
