# src/yoetz_core/adapters/control/unix_socket.py — same-UID local control transport

**Wave:** C | **ADRs:** ADR-001, ADR-008 | **Imports (spec-tree):** `ports/control.md`,
`service/control_protocol.md`, `service/confidential_protocol.md`, `config/paths.md` |
**Imported by:** `service/daemon.md`, `service/client.md`, `service/confidential_client.md`,
`service/secret_ingress.md`, `service/human_control.md`

## Purpose

Implements owner-only Unix-domain ordinary, secret-ingress, and human-control endpoints and mutual same-effective-UID
peer verification for certified macOS and Linux targets. It is transport/authentication, not
method authorization or secret parsing.

## Public surface

- `bind_control_listener()`, `bind_secret_listener()`, `bind_human_control_listener()`,
  `connect_control()`, `connect_secret()`, `connect_human_control()`.
- `authenticate_peer(socket) -> PeerIdentityHandle` using Linux `SO_PEERCRED` or macOS
  `getpeereid`.
- `remove_stale_endpoint(kind, service_lock)`, `close_endpoint(instance)`.
- Constants for fixed endpoint basenames and owner modes; no caller-supplied path overload.
- The connect-only confidential sub-surface imports no daemon/human-control/secret-ingress/vault/
  application code and is the only adapter surface reachable from `confidential_client.md`.

## Behavior

Derive endpoints beneath the verified per-user runtime directory, require every ancestor owner-only
and no-follow, bind with `0600` under `0700`, and authenticate both server and client peer UID before
protocol bytes. Endpoint replacement requires lifecycle singleton lock, lstat socket type/current
owner/link-count checks, failed live probe, unlink, and directory fsync. No TCP/abstract socket,
project path, environment override, bearer token, or fallback exists.

The three fixed sockets are distinct: ordinary control, one-secret ingress, and multi-phase human
control. The adapter returns byte streams only to their typed protocol owners and never forwards
between them. Human control may mint a one-use binding that the secret-ingress parser later
consumes, but no descriptor/stream/parser is shared and the ordinary endpoint cannot proxy either.

## Errors and edge cases

- Wrong/unknown peer credentials, symlink/hardlink/foreign owner/broad mode/non-socket path fail
  closed with bounded reasons.
- A same-UID malicious process remains outside the threat model; permissions are not claimed to
  defend against compromised active UID.
- Partial writes/reads/backpressure are handled by protocol owners; descriptors are nonblocking and
  close-on-exec.

## Invariants

1. Peer UID is positively verified on all three endpoints before payload exchange.
2. Endpoint location/type/owner cannot come from task/repository/client content.
3. Ordinary, secret-ingress, and human-control channels never share a listener or parser.
4. Descriptors are not inherited by child/provider processes.
5. Importing a connector performs no bind, stale-endpoint removal, keyring, config-load, or service
   composition effect.

## Tests

- `tests/integration/service/test_local_control_channel.py` covers identity/mode/path/peer attacks.
- `tests/integration/service/test_human_control.py` proves binding handoff between only the human
  and secret endpoints and rejects ordinary/MCP connections.
- `tests/subprocess/test_service_secret_boundary.py` covers descriptor inheritance and isolation.
- `tests/capability/test_local_control_channel.py` proves OS APIs on release runners.

## Open questions

None.
