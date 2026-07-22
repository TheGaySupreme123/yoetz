# src/yoetz/adapters/mcp_stdio.py — Yoetz-owned bounded stdio MCP transport

**Wave:** D | **ADRs:** ADR-005 (transport, parse-error ID), ADR-007 (anyio pin, platforms) |
**Imports (spec-tree):** `protocol/canonical.md` (strict JSON parse rules), `protocol/errors.md`
(internal reason codes), `observability/logging.md` | **Imported by:** `mcp/server.md`,
`specs/tests/subprocess.md`

## Purpose

The official MCP Python SDK v1 default stdio helper reads complete unbounded lines and decodes
malformed UTF-8 with replacement characters. Neither behavior meets the ingress contract in
`specs/src/yoetz/mcp/server.md` and ADR-005. This file is the sole owner of the process's stdin and
stdout while `yoetz mcp serve` runs. It enforces the frame-size cap before buffering an
attacker-controlled frame, guarantees strict UTF-8/duplicate-key rejection before any SDK parsing,
and guarantees that stdout carries only well-formed, size-checked MCP protocol frames. Without this
file, a hostile or buggy client could exhaust memory with one giant line, smuggle replacement-decoded
bytes into request parsing, or interleave non-protocol noise into stdout.

## Public surface

- `MAX_JSON_FRAME_BYTES: int = 1_048_576` — the inbound and outbound payload cap in bytes, counting
  JSON payload bytes only and excluding the single terminating LF (`specs/INTERFACES.md`).
- `bounded_stdio_server(max_json_bytes: int = MAX_JSON_FRAME_BYTES, *,
  drain_pending_responses: bool = False)` — async context manager that
  yields the pair `(read_stream, write_stream)` consumed by `Server.run` in `mcp/server.py`
  (`specs/INTERFACES.md`). `read_stream` delivers validated SDK `SessionMessage` values to the server;
  `write_stream` accepts SDK `SessionMessage` values from the server and serializes them to stdout.
  A nondefault cap must be at least the byte length of the largest fixed transport-error frame, so
  the adapter never violates its own outbound cap while reporting a bounded inbound failure.
- `TransportFailure` — internal exception type (never crosses to the SDK streams) with a bounded
  reason code; terminates the context manager body. It is deliberately adapter-private under the
  registry ownership rule.

No other module may read stdin or write stdout while the context manager is open.

## Behavior

### Structure

`bounded_stdio_server` is implemented with AnyIO task groups. On entry it:

1. Duplicates and takes ownership of the raw stdin and stdout file descriptors (`os.dup` of fds 0
   and 1), sets them non-blocking, and rebinds Python-level `sys.stdout` to stderr-backed or null
   sink so accidental `print` calls cannot reach protocol stdout (a debug-build assertion; the
   binding contract is still "nothing else writes fd 1").
2. Creates two pairs of AnyIO memory object streams with **capacity zero** carrying the SDK
   `SessionMessage` values consumed by `specs/src/yoetz/mcp/server.md`: inbound
   `reader task → Server.run` and outbound
   `Server.run → writer task`. Zero capacity means a stalled client or stalled server produces
   backpressure, never an unbounded in-process queue.
3. Starts the reader task and the writer task in one task group, yields
   `(read_stream, write_stream)` to the caller, and on exit cancels the tasks, closes the streams,
   and closes the duplicated descriptors.

The SDK `SessionMessage` type is an upgrade-sensitive seam: it is used only here and is covered by
the exact `mcp==1.28.1` pin and the subprocess golden matrix. If wiring this adapter to
`Server.run` would require an unreviewed private SDK API, ADR-005 blocks the release; the ingress
contract is never weakened instead.

### Reader task

The reader owns a single `bytearray` buffer and loops:

1. **Wait for readiness.** Await descriptor read readiness (AnyIO
   `wait_readable`/`wait_socket_readable` equivalent for the platform; on the two certified POSIX
   targets this is the fd readiness primitive). No read is issued while the descriptor is not
   readable.
2. **Bounded chunk read.** Call `os.read(fd, chunk)` with a fixed chunk size that is never larger
   than 64 KiB. The chunk size is additionally clamped so that
   `len(buffer) + chunk <= max_json_bytes + 1`: the reader deliberately reads at most one byte past
   the cap, which is exactly enough to prove the frame is oversized without ever buffering more
   than `max_json_bytes + 1` payload bytes. It never drains the remainder of an oversized frame.
3. **Frame scan.** Scan newly arrived bytes for LF (`0x0A`). Bytes before the LF complete the
   pending frame; the LF itself is the delimiter and is not counted against the cap. A valid frame
   contains exactly one LF — the delimiter; an embedded raw LF inside a JSON string is impossible
   in valid JSON (it must be escaped), so any LF terminates a frame.
4. **Cap check (cap-plus-one behavior).** If the buffered payload bytes for the pending frame ever
   exceed `max_json_bytes` — i.e., byte `max_json_bytes + 1` arrives without a preceding LF — the
   reader MUST NOT drain the rest of the frame and MUST NOT forward any prefix of it to
   `Server.run`. It requests one fixed transport error emission through the sole writer (see
   "Transport error frames"), then closes the transport and exits, because a stream that was
   truncated mid-frame cannot be resynchronized without draining unbounded input.
5. **Frame validation.** A completed frame (payload bytes between delimiters) is validated in this
   fixed order, all before any SDK type is constructed:
   1. empty frame (zero payload bytes) → transport parse failure;
   2. leading UTF-8 BOM (`EF BB BF`) → rejected;
   3. any NUL byte (`0x00`) anywhere → rejected;
   4. strict UTF-8 decode (`errors="strict"`); any invalid byte sequence → rejected. No
      replacement decoding is ever used;
   5. JSON parse using the strict parser rules of `protocol/canonical.md`'s `strict_json_parse`
      applied at the transport profile: duplicate object keys anywhere in the document are
      rejected (`duplicate_object_key`); lone surrogates are rejected. (Transport framing does not
      apply the canonical *value* profile — floats are legal in JSON-RPC metadata — only the
      structural strictness rules listed here.)
   6. The parsed value must be a JSON object shaped as a JSON-RPC 2.0 message the SDK accepts;
      it is then converted to the SDK `JSONRPCMessage`/`SessionMessage` value.
6. **Forward.** Only a fully validated `SessionMessage` is sent into the zero-capacity inbound
   stream. Raw parser, decoder, I/O, or transport exceptions never enter the SDK receive stream —
   the SDK sees validated messages or clean stream closure, nothing else.
7. **EOF.** `os.read` returning `b""` with an **empty** buffer is clean EOF. The production MCP
   server enables `drain_pending_responses`, keeping SDK input open for a bounded window until
   accepted requests have written responses; direct transport users default to immediate closure.
   `os.read` returning `b""` with a
   **non-empty** partial frame is a transport failure (partial-EOF): the incomplete frame is
   discarded, never forwarded, and the transport terminates through the failure path.

`EINTR` from either the readiness wait or `os.read` is retried from the same buffer state. Any
other read failure becomes only the bounded adapter-private `read_failed` reason. The task-group
boundary consumes that transport failure after closing the SDK streams, so no exception group,
raw `OSError`, traceback, errno text, or filesystem path reaches the SDK, stdout, or stderr.

Frame validation failures in step 5 for a *fully consumed, bounded* frame are recoverable: the
reader requests a fixed parse-error frame through the sole writer and continues with the next
frame, because the LF delimiter keeps the stream synchronized. Cap-plus-one (step 4) and
partial-EOF (step 7) are not recoverable and terminate the transport after the best-effort error
frame.

### Writer task

The writer is the sole stdout owner. It loops receiving `SessionMessage` values from the outbound
zero-capacity stream plus fixed transport-error frame requests from the reader (multiplexed through
one internal queue so exactly one task ever calls `os.write` on fd 1). For each outbound message:

1. Serialize the JSON-RPC payload to UTF-8 bytes with no embedded newline (compact separators; the
   serializer must not emit raw LF inside the payload).
2. **Outbound cap check.** If the payload byte length exceeds `max_json_bytes` (excluding the LF
   about to be appended), the frame is not written; this is an internal defect (Yoetz produced an
   oversized result), recorded internally with a correlation ID, and the transport closes as a
   bounded failure. No truncated frame is ever emitted.
3. Append exactly one LF.
4. Await descriptor write readiness, then loop over partial `os.write` results: each call may
   write fewer bytes than requested; the writer advances its offset and re-awaits readiness until
   the entire frame is written or a write raises. `BrokenPipeError`/`EPIPE` is a bounded transport
   failure that closes the transport; it is never converted into an exception on the SDK stream.

`EINTR` from readiness or `os.write` retries without advancing the byte offset. Other write errors
collapse to `write_failed`, and a zero/negative write is the same bounded failure. Cleanup restores
Python stdout and best-effort closes both owned descriptor duplicates without surfacing close-time
OS detail.

A slow or stalled client therefore blocks the writer at readiness-wait, which (through the
zero-capacity stream) backpressures `Server.run`, which backpressures the reader. Memory stays
bounded end to end.

### Transport error frames

When a frame is malformed or oversized, no request ID is recoverable. Per ADR-005 decision 5, the
adapter emits a **manually constructed JSON-RPC 2.0 error frame with `"id": null`** through the
sole writer — bypassing the SDK's non-null-ID message model, which cannot represent this frame —
carrying an ADR-approved fixed error object:

- malformed frame (empty, BOM, NUL, invalid UTF-8, duplicate key, JSON parse failure):
  JSON-RPC code `-32700` (Parse error) with the fixed message `"Parse error"` and a bounded
  `data.reason` drawn from a closed set
  (`empty_frame`, `bom_rejected`, `nul_rejected`, `invalid_utf8`, `duplicate_object_key`,
  `invalid_json`, `not_jsonrpc`);
- oversized frame (cap-plus-one): JSON-RPC code `-32600` (Invalid Request) with fixed message
  `"Frame exceeds maximum size"` and `data.reason = "frame_too_large"`; the frame content is
  never echoed and its size beyond "exceeded" is not reported.

These frames are byte-fixed except for nothing — no caller input, no lengths, no offsets, no
exception text appears in them. If the pinned Codex client is shown by transcript test to mishandle
the null-ID frame, the ADR-005 fallback is orderly transport termination without any error frame.
The implementation never fabricates an ID and never silently replaces bytes.

### Logging and platforms

- SDK and adapter log records are filtered at every level, including `ERROR` and `CRITICAL`:
  an untrusted tool name, parsed request, schema failure detail, raw stream exception text, or
  payload bytes can never be emitted. Adapter logs carry only bounded reason codes, byte counts,
  and correlation IDs, to stderr or the structured log per `observability/logging.md`.
- The `os.read`/`os.write` descriptor implementation is advertised only for the certified
  **macOS 11.0+ arm64** and **glibc 2.28+ Linux x86-64 (manylinux_2_28)** targets. Windows requires
  a separate transport implementation and its own test gate before any support claim; this file
  contains no Windows code path in v0.1.

## Errors and edge cases

| Situation | Behavior |
|---|---|
| Empty frame (`\n` alone) | Fixed parse-error frame (id null, reason `empty_frame`); continue |
| Frame of exactly `max_json_bytes` payload bytes + LF | Accepted; boundary test required |
| Payload byte `max_json_bytes + 1` without LF | Fixed `frame_too_large` frame, then close transport; never drain, never forward prefix |
| Multiple frames in one 64 KiB chunk | All are split and processed in order |
| Frame split across many chunks | Reassembled; identical result to single-chunk delivery |
| Invalid UTF-8 / BOM / NUL / duplicate key / malformed JSON | Fixed parse-error frame; continue with next frame |
| EOF with empty buffer | Clean shutdown: close inbound stream, let SDK finish, exit 0 path |
| EOF with partial frame | Transport failure; discard partial bytes; terminate |
| `os.read`/`os.write` raises (`EIO`, `EBADF`, `EPIPE`, …) | Bounded transport failure; correlation ID recorded; no exception object enters SDK streams |
| Outbound payload exceeds cap | Internal defect: frame suppressed, correlation ID recorded, transport closes |
| Partial `os.write` | Loop with readiness waits until fully written |
| Cancellation (task group cancelled) | Cancellation is re-raised, not converted; streams and fds are closed in `finally` |
| Client stops reading (stall) | Writer blocks on readiness; zero-capacity streams backpressure the whole pipeline; no queue growth |
| Clean EOF after accepted requests | When production drain tracking is enabled, keep the SDK input stream open for a bounded window until each accepted request response has been written; then close. Notifications do not enter the pending count. |

Nothing user-controlled — payload bytes, tool names, JSON fragments, exception strings — ever
appears in an emitted error frame or a log record from this file.

## Invariants

1. At most `max_json_bytes + 1` bytes of a single inbound frame are ever buffered.
2. Every read is preceded by a readiness wait and is at most 64 KiB.
3. Only validated `SessionMessage` values enter the SDK read stream; only complete, cap-checked,
   single-LF-terminated frames leave on stdout.
4. Exactly one task writes fd 1 for the process lifetime of `mcp serve`.
5. The cap counts JSON payload bytes and excludes the single terminating LF, inbound and outbound.
6. An oversized frame is never drained and no prefix of it is ever forwarded.
7. Parse-error frames use `"id": null`; no ID is ever fabricated.
8. Memory streams have capacity zero; backpressure replaces queuing.
9. No private SDK API is used; otherwise ADR-005 blocks release.
10. Clean EOF cannot cancel already accepted request responses merely because the reader observed
    EOF; the bounded drain tracker counts requests and written responses without retaining payloads.

## Tests

Subprocess golden tests (`specs/tests/subprocess.md`), run against the installed artifact on both
certified platforms, cover at minimum: empty frame; exact-limit frame;
cap-plus-one; multiple frames per chunk and frames split across chunks; invalid-byte; BOM; NUL;
duplicate-key; malformed JSON; partial-EOF; partial-write (small pipe buffer); slow-reader stall
with bounded memory assertion; broken-pipe; cancellation mid-frame; and stdout-noise (assert stdout
parses as protocol-only JSONL under all of the above). Plus: null-ID parse-error transcript fixture
against the pinned Codex client (ADR-005), and a fault-injection case proving an `os.read` failure
produces no SDK-stream exception.

## Open questions

None.
