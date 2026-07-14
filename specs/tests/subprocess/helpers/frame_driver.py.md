# tests/subprocess/helpers/frame_driver.py — byte-level MCP stdio stimulus and oracle

**Wave:** D/F | **ADRs:** ADR-005 | **Imports (spec-tree):**
`specs/tests/subprocess/helpers/child.py.md`, `specs/src/yoetz_core/adapters/mcp_stdio.md` |
**Imported by:** MCP framing, purity, backpressure, and cancellation tests

## Purpose

Drive LF-delimited JSON-RPC over raw descriptors with controlled fragmentation, delay, partial
writes, EOF, and slow reads. It tests transport behavior below SDK clients without copying the
production parser or masking malformed bytes.

## Public surface

- `FrameStimulus`: ordered byte chunks, delivery boundaries/delays, EOF and read policy.
- `FrameObservation`: raw output chunks/frames, stderr, exit, timing buckets, buffer watermark.
- `encode_valid_frame(value) -> bytes` for fixtures only; rejects noncanonical fixture values.
- `drive_frames(child, stimulus) -> FrameObservation`.
- `split_at_every_boundary(frame)`, `partial_write_schedule(...)`, `slow_reader_schedule(...)`.
- `parse_protocol_output_exact(bytes) -> tuple[JsonRpcFrame, ...]`.

## Behavior

Write caller-supplied bytes exactly, optionally one byte at a time, in fixed chunks, after readiness
delays, or with EOF before/after LF. Concurrently drain stderr; read stdout according to explicit
normal/slow/paused schedules. Capture OS write/read counts and bounded timing without assuming one
system call per frame.

The output parser splits only on LF, rejects partial trailing bytes, BOM/invalid UTF-8/duplicate JSON
keys/floats/unsafe integers, and validates JSON-RPC shape. It never repairs CRLF, ignores blank
bytes, or parses stderr. Expected malformed-input frames are literal golden bytes. Valid-frame
encoding is used only to make stimulus; output expectations remain independent fixtures.

Test-only descriptor shims may inject EINTR, one-byte writes, short writes, readiness delay, and
broken pipe around the child or test build. Injection identity is recorded and production config
must be unable to enable it.

## Errors and edge cases

- Driver deadlock, cap exceed, incomplete output, or schedule mismatch fails as harness error.
- Kernel coalescing/splitting is accepted; semantic frame boundaries and bytes are the oracle.
- Timing assertions use generous bounded intervals and event synchronization, not sleeps as proof.
- No decoded request/response content appears in diagnostics.

## Invariants

1. Stimulus bytes and delivery boundaries are reproducible.
2. No parser reads beyond LF or invents a missing delimiter.
3. Slow/partial I/O remains bounded and observable.
4. The oracle is independent of the production stdio implementation.

## Tests

Self-tests run a deterministic echo fixture under every split, short-write, EOF, slow-reader, and
invalid-byte schedule. Golden parser tests cover duplicate keys, invalid Unicode, unsafe numbers,
partial tail, and multiple frames.

## Open questions

None.
