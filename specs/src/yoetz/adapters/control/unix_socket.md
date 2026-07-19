# src/yoetz/adapters/control/unix_socket.py — same-UID local control transport

**Wave:** C | **ADRs:** ADR-001, ADR-008 | **Imports (spec-tree):** `ports/control.md`,
`service/control_protocol.md`, `service/confidential_protocol.md`, `config/paths.md` |
**Imported by:** `service/daemon.md`, `service/client.md`, `service/confidential_client.md`,
`service/secret_ingress.md`, `service/human_control.md`

## Purpose

Implements owner-only Unix-domain ordinary, secret-ingress, and human-control endpoints and mutual same-effective-UID
peer verification for certified macOS and Linux targets. It is transport/authentication, not
method authorization or secret parsing.

## Public surface

- `enum EndpointKind` — exactly `control`, `secret`, `human_control`; this is an adapter endpoint
  discriminator, not a serializable control-protocol value.
- Fixed constants `CONTROL_ENDPOINT_BASENAME="control.sock"`,
  `SECRET_ENDPOINT_BASENAME="secret-ingress.sock"`,
  `HUMAN_CONTROL_ENDPOINT_BASENAME="human-control.sock"`, `RUNTIME_DIRECTORY_MODE=0o700`, and
  `ENDPOINT_MODE=0o600`. There is no caller-supplied basename/path overload.
- Async `bind_control_listener()`, `bind_secret_listener()`, and
  `bind_human_control_listener() -> UnixEndpointListener`; async `connect_control()`,
  `connect_secret()`, and `connect_human_control() -> AuthenticatedUnixStream`.
- `authenticate_peer(socket) -> PeerIdentityHandle` using Linux `SO_PEERCRED` or macOS
  `getpeereid`. `PeerIdentityHandle` is opaque, redacted in `repr`, and nonserializable; it is
  transport evidence carried into a protocol session, never a wire or workflow model.
- `class AuthenticatedUnixStream` — async bounded-chunk `receive(max_bytes)`, backpressured
  `send_all(buffer)`, `shutdown_write`, idempotent `aclose`, opaque `peer_identity`, and `fileno`
  only for capability/inheritance probes. It offers no frame, JSON, schema, endpoint, or TCP API.
- `class UnixEndpointListener` — async `accept() -> AuthenticatedUnixStream`, idempotent `aclose`,
  fixed `endpoint_kind`, and capability-only `fileno`. Accepted-open connections are bounded; a
  full bound leaves later connections in the finite kernel backlog.
- Async `remove_stale_endpoint(kind, service_lock)` and `close_endpoint(instance)`. Stale removal
  requires the lifecycle-owned singleton authority to prove its lock is held; the adapter does not
  mint that authority.
- `class LocalControlTransportError(Exception)` — bounded adapter-only reasons exactly
  `connection_failed`, `endpoint_exists`, `endpoint_in_use`, `endpoint_missing`,
  `endpoint_unsafe`, `listener_closed`, `peer_untrusted`, `runtime_directory_unsafe`,
  `service_lock_required`, and `unsupported_platform`. It contains no path, peer UID, payload, or
  exception text and is mapped by the typed service/client owner before any public result.
- The connect-only confidential sub-surface imports no daemon/human-control/secret-ingress/vault/
  application code and is the only adapter surface reachable from `confidential_client.md`.

## Behavior

Derive endpoints beneath `platformdirs.PlatformDirs(appname="yoetz", appauthor=False,
roaming=False).user_runtime_path`, create/verify that fixed directory as owner-only `0700`, reject
symlink components, bind with `0600`, and authenticate both server and client peer UID before
protocol bytes. Bind fails when any endpoint path already exists; it never implicitly unlinks.
Endpoint replacement requires lifecycle singleton lock, lstat socket type/current
owner/link-count checks, an explicitly refused live probe, inode-stable unlink, and directory
fsync. No TCP/abstract socket, project path, environment override, bearer token, or fallback
exists.

The three fixed sockets are distinct: ordinary control, one-secret ingress, and multi-phase human
control. The adapter returns byte streams only to their typed protocol owners and never forwards
between them. Human control may mint a one-use binding that the secret-ingress parser later
consumes, but no descriptor/stream/parser is shared and the ordinary endpoint cannot proxy either.

## Errors and edge cases

- Wrong/unknown peer credentials, symlink/hardlink/foreign owner/broad mode/non-socket path fail
  closed with bounded reasons.
- A same-UID malicious process remains outside the threat model; permissions are not claimed to
  defend against compromised active UID.
- Partial writes/reads are exposed as bounded nonblocking stream operations. Canonical length
  framing, strict JSON/schema validation, the per-connection 32-request bound, out-of-order result
  matching, and response-queue backpressure remain owned by `service/control_protocol.md`; YZH1/
  YZS1 caps/state remain owned by `service/confidential_protocol.md`. Descriptors are nonblocking
  and close-on-exec, and this adapter creates no buffering queue.

## Invariants

1. Peer UID is positively verified on all three endpoints before payload exchange.
2. Endpoint location/type/owner cannot come from task/repository/client content.
3. Ordinary, secret-ingress, and human-control channels never share a listener or parser.
4. Descriptors are not inherited by child/provider processes.
5. Importing a connector performs no bind, stale-endpoint removal, keyring, config-load, or service
   composition effect.
6. Adapter transport types and bounded failures never appear in public workflow/control result
   models.

## Tests

- `tests/integration/service/test_local_control_channel.py` covers identity/mode/path/peer attacks.
- `tests/integration/service/test_human_control.py` proves binding handoff between only the human
  and secret endpoints and rejects ordinary/MCP connections.
- `tests/subprocess/test_service_secret_boundary.py` covers descriptor inheritance and isolation.
- `tests/capability/test_local_control_channel.py` proves OS APIs on release runners.

## Open questions

None.
