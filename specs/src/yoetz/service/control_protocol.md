# src/yoetz/service/control_protocol.py — frozen ordinary local-service wire protocol

**Wave:** C | **ADRs:** ADR-001, ADR-008 | **Imports (spec-tree):** `protocol/canonical.md`,
`protocol/errors.md`, `protocol/ids.md`, `protocol/schemas.md`, `ports/control.md` |
**Imported by:** `service/client.md`, `service/daemon.md`, `adapters/control/unix_socket.md`

## Purpose

Owns framing, handshake, strict parsing, request/result schema dispatch, cancellation messages, and
bounded error frames for ordinary same-user clients. It prevents CLI, MCP, or a future UI from
calling Python objects directly or inventing a secret-bearing/private service method.

## Public surface

- `CONTROL_PROTOCOL_VERSION = "1.0"`.
- `MAX_CONTROL_FRAME_BYTES = 6_291_456` excluding the four-byte length prefix: the absolute
  allocation guard needed for canonical-base64 transport of the importer's 4 MiB exact source.
- `MAX_ORDINARY_CONTROL_FRAME_BYTES = 1_048_576`; every frame other than the exact
  `import_codex_jsonl` call branch must remain at or below this stricter cap.
- `encode_control_frame(value) -> bytes` and `decode_control_frame(frame: bytes) -> ControlFrame`.
- `async client_handshake(stream, client_kind, client_version) -> ControlSession`.
- `async server_handshake(stream, peer_identity, service_status) -> ControlSession`.
- `validate_request(ControlRequest)`, `validate_result(ControlResult)`, and
  `schema_for_method(ControlMethod, direction)`, which selects only the reviewed branch compiled
  from the control request/result artifacts (six offline operation `$ref`s plus nineteen inline
  support `$defs`) and has no open-dict/default registry entry.
- `@dataclass(frozen=True, slots=True) class ControlSession` — negotiated version, client kind,
  service instance/generation, and authenticated peer identity handle; nonserializable.
- `class ControlProtocolError(Exception)` carrying only a bounded control reason.

The wire schemas are the five exact `service/*.schema.json` files named in `ports/control.md`.
The confidential secret-ingress protocol is deliberately not implemented or imported here.

## Behavior

Each frame is `u32be payload_length | canonical UTF-8 JSON payload`. The receiver reads exactly
four bytes, rejects zero or values above the absolute cap before allocation, then reads exactly the
declared payload. After strict parse, a frame above the ordinary cap is accepted only when it is
the exact closed `import_codex_jsonl` call and its canonical base64 decodes within 4 MiB; every
other branch is rejected. JSON uses strict UTF-8, no BOM/NUL/duplicate keys/lone surrogates/floats/negative zero, and
the canonical integer/string/object constraints. Trailing bytes belong to the next frame; partial
EOF is fatal. Neither side scans for delimiters or buffers beyond one capped frame.

The first client frame is `control-hello` with protocol/client versions, client kind, and
`connection_nonce`: exactly 64 lowercase hexadecimal characters from 32 fresh OS-CSPRNG bytes.
The nonce is a structural connection binding, not a prefixed Yoetz ID, credential, challenge
response, or reusable authority. It contains no process environment, cwd, project path, PID,
username, or token. The server has already authenticated peer UID at the transport layer. It replies with
`control-hello-result`, exact negotiated version, service status, service instance/generation, and
method capability set. Version/capability mismatch closes before a request.

For `client_kind=mcp_bridge`, the capability set and admission set are exactly the six workflow
methods. The larger closed schema remains parseable for CLI/trusted-local UI, but schema validity
never grants MCP authority to import source bytes or call another support branch.

Every subsequent request has one validated `rpc_id`, exact method discriminator, schema-versioned
body, optional bounded deadline, and service generation from the handshake. `schema_for_method`
selects the already closed method branch owned by the envelope artifact; no separately extensible
method-body registry or open dict path exists. Results repeat the RPC ID, method, and service
generation and validate against the exact matching result branch. A cancellation frame
names only the RPC ID and generation; it requests cancellation but never asserts outcome.

Server dispatch is concurrent under bounded admission, but one connection cannot exceed 32 active
requests and the service-wide bound is configured/frozen at startup. Responses may complete out of
order and are matched only by RPC ID. Backpressure uses zero/bounded-capacity streams; no unbounded
queue is allowed.

## Errors and edge cases

- Malformed length/JSON/schema/unknown method produces one fixed sanitized error when the stream is
  still synchronized, then closes; input and parse text are never echoed.
- A frame above the absolute cap closes immediately without draining the claimed payload. A frame
  above the ordinary cap that is not the exact bounded import branch fails before dispatch and its
  decoded source is discarded without logging.
- A stale service generation produces the control-level reason `service_generation_changed`, then
  closes the session without dispatch. The client maps that reason to public
  `SERVICE_UNAVAILABLE` and reconnects; a successor never accepts an old session.
- Response loss after service completion is ambiguous to the client; replaying the operation's
  identical request ID is the only resolution.
- Workflow errors remain exact public error envelopes. Control failures never masquerade as
  workflow verdicts.

## Invariants

1. Ordinary frames cannot represent a `SecretPurpose`, secret bytes, key handle, unlock request,
   credential mutation, or privacy-loosening proof.
2. Every body is validated twice: before dispatch/serialization and after parse/receipt.
3. One bounded frame is allocated at a time; declared oversize is rejected before payload read.
4. Peer authentication precedes protocol handshake; the hello is not authentication.
5. MCP and CLI use byte-identical request/result framing.
6. MCP admission is exactly the six workflow methods and rejects every support branch before body
   effects, including base64 decode/import capture.

## Tests

- `tests/unit/service/test_control_protocol.py` freezes canonical bytes and every malformed/cap
  boundary, method/schema map, hello negotiation, and secret-field rejection.
- `tests/integration/service/test_local_control_channel.py` covers partial reads/writes,
  backpressure, out-of-order results, cancellation, response loss, and stale generation.
- `tests/property/test_service_control_frames.py` fuzzes decode/encode bounds and canonical round
  trips without unbounded allocation.

## Open questions

None.
