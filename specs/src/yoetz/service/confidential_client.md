# src/yoetz/service/confidential_client.py — client-safe confidential transport clients

**Wave:** D | **ADRs:** ADR-004, ADR-008, ADR-009 | **Imports (spec-tree):**
`service/confidential_protocol.md`, connect-only surface of `adapters/control/unix_socket.md` |
**Imported by:** `cli/unlock.md`, `cli/privacy_control.md`, future trusted desktop helper

## Purpose

Connect foreground trusted human helpers to YZH1/YZS1 without importing server authority. This
module owns transport sequencing and mutable-buffer send/overwrite only; it does not render a
prompt, decide policy, create a challenge, load config, or import vault/unlock/application/provider
code.

## Public surface

- `class HumanControlClient` with `open(kind, target) -> HumanControlSession` and `close()`.
- `class HumanControlSession` with exact `opened`, `send_action`, `wait_phase_or_result`, `cancel`,
  and `close`; it validates ceremony ID, step, phase, terminal result/error, and close.
- Package-private `ConfidentialSessionToken`, created only by an open live human session and bound
  to its peer connection/service generation/ceremony ID.
- `class ConfidentialSecretClient` with package-private constructor and
  `send_once(binding, source: bytearray, session_token) -> None`; it returns no secret or result.
- `class ConfidentialClientError(Exception)` mapping only the bounded protocol/connector reasons.

## Behavior

`HumanControlClient.open` derives the fixed human endpoint through the connect-only local connector,
authenticates the service peer as the current effective UID, sends one exact client-open frame, and
validates one server-opened frame. It never accepts a caller-supplied ceremony ID, secret binding,
socket path, endpoint override, service generation, or arbitrary frame. One client instance owns at
most one live session.

`HumanControlSession` implements the exact YZH1 state machine from
`confidential_protocol.md`. Only an opened session can create the package-private token needed by
`ConfidentialSecretClient`. When a server phase supplies a secret binding, `send_once` verifies its
ceremony/service generation/purpose/target against that token, derives the fixed secret endpoint,
authenticates the same service UID, writes the fixed header/binding and the mutable source exactly
once, half-closes, requires zero response bytes, and overwrites the full source in `finally`.
Copying/pickling/repr of the session token or retaining it after phase transition is forbidden.

The clients contain no TTY logic. CLI helpers independently require `/dev/tty`, render the exact
preview, collect action/secret, and call these methods. MCP and ordinary `ServiceClient` never
import this module. A future desktop helper may import it only as a trusted local-human surface and
must satisfy the same preview/secret rules.

## Errors and edge cases

- Wrong peer, endpoint replacement, stale service generation, crossed binding, duplicate send,
  wrong purpose, YZS1 response bytes, missing terminal close, timeout, cancellation, and partial
  write close the session and return bounded ambiguity/failure.
- There is no standalone `connect_secret(binding)` API, raw frame API, socket-path overload,
  background/headless mode, password descriptor, or automatic retry. A new secret attempt starts a
  fresh YZH1 ceremony.
- Source overwrite is best effort and occurs on success, rejection, cancellation, and connector
  failure. This module never converts source to immutable `bytes` or `str`.

## Invariants

1. Its transitive import graph contains pure protocol and connect-only transport, not daemon,
   human-control server, secret-ingress server, vault, unlock, application, keys, or providers.
2. A secret connection is possible only from a live server-opened ceremony and matching phase.
3. Clients never mint authority, interpret a secret, or return reusable proof/token material.
4. Ordinary CLI/MCP client code cannot reach these clients through `service/client.py`.

## Tests

- `tests/unit/service/test_confidential_client.py` covers state/correlation/close, token opacity,
  one-send overwrite, exact connector calls, and forbidden constructor/raw-frame surfaces.
- `tests/subprocess/test_service_unlock_boundary.py` and
  `tests/subprocess/test_privacy_human_control.py` cover PTY integration and transcript canaries.
- `tests/packaging/test_service_boundary_imports.py` freezes allowed confidential-helper imports
  separately from ordinary CLI/MCP imports.

## Open questions

None.
