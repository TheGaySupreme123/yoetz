# src/yoetz/cli/app.py — local-service CLI client and service command tree

**Wave:** D/F | **ADRs:** ADR-001, ADR-002, ADR-005, ADR-007, ADR-008 |
**Imports (spec-tree):** `cli/render.md`, `cli/exits.md`, `cli/unlock.md`, `service/client.md`,
`mcp/server.md` | **Imported by:** console/module entrypoints and CLI tests

## Purpose

Owns the human command surface. Normal commands are thin clients of the persistent local service;
they never construct `Application`, open storage/keyring, load provider credentials, decrypt
objects, or fall back to direct execution.

## Public surface

- Typer `app`, `main()`, one `run_async` bridge.
- `build_service_client(client_kind=cli) -> ServiceClient`.
- Shared six-operation/status/receipt client commands.
- Command tree: the six workflows; `mcp serve`; import/review/backup/restore/migrate/version and
  Codex integration; `service run|status|lock|stop|unlock|initialize-passphrase`; and
  `provider credential set|rotate` for foreground confidential provisioning; plus
  `privacy receipts list|get` for bounded structural local audit inspection.
- No `build_runtime`, `RuntimeFactory`, application constructor, password/key/token option, or
  secret environment reader.

## Behavior

Every normal workflow/support command strictly parses its request, connects to the deterministic
same-UID service endpoint, invokes the matching `ServiceClient` method, validates the returned
result, and renders JSON/human output. Service absent reports bounded guidance; locked reports
`vault_locked` and directs a local human to `service unlock`. Neither state triggers hidden spawn,
direct runtime, prompt, or secret acceptance.

`mcp serve` delegates to the MCP bridge and owns only stdio framing/client connection. `service
run` is the foreground daemon entrypoint; it must not be nested inside another CLI event loop.
Native launchd/systemd-user install/start integration is deferred; an external user-selected
supervisor may run this foreground command, but normal workflows never start it.
`service lock` is CLI-only and drains/relocks; `service stop` drains/exits. Version may inspect
installed public package metadata locally but no user/vault state. `privacy setup|show|propose|
tighten` use least-authority ordinary control. `privacy decide-policy|decide-disclosure` delegate
to `cli/privacy_control.md`, never an ordinary decision method. `privacy receipts list|get` are
CLI/UI-only structural reads, never MCP tools; they render no excerpts/request bodies/object refs and
are local-projection/audit-exempt so inspection does not create a new receipt.

`service unlock` and the distinct first-install-only `service initialize-passphrase` delegate to
`cli/unlock.md`. The latter is available only for a pristine uninitialized vault, confirms the
passphrase twice locally, and sends one `vault_initialize` confidential value; it is never offered
as fallback/reset for an existing keyring/passphrase vault. There is no passphrase/password/secret flag,
environment/config/stdin/file option. Provider credential/recovery/reauthorization ceremonies use
the YZH1 human-control plus YZS1 one-secret helper family, not normal request parsing. Provider
set/rotate arguments contain only exact nonsecret profile/scope/purpose identifiers; credential and
reauth bytes never enter Typer values. Boolean confirmation never
authorizes privacy-policy loosening; fresh vault reauth/OS user presence does.

JSON stdin/flags remain available for ordinary non-secret requests. Diagnostics go to stderr;
normal JSON preserves exact envelopes. Connection loss never implies cancel/commit; retry preserves
the identical operation request ID.

## Errors and edge cases

- Usage/input shape → exit 2; conflicts 10; pending 11; service/storage/vault unavailable 20;
  provider 30; corruption 40; sanitized defect 70; interrupt 130.
- Non-TTY commands never prompt or hang. Unlock specifically opens the controlling TTY and rejects
  absence; it never falls back to stdin.
- Findings/incomplete semantic checks still exit 0 when deterministic operation completed.
- `mcp serve` emits protocol frames only on stdout.

## Invariants

1. CLI normal path imports only client/protocol/render code, not application/storage/key/provider
   composition.
2. Service absent/locked never causes direct execution or secret prompt in an ordinary operation.
3. No secret appears in argv/env/config/stdin/history/output/logs.
4. CLI and MCP preserve identical public operation semantics through one service.
5. No built-in service-manager install/start command or hidden spawn exists in v0.1.

## Tests

- `tests/subprocess/test_cli_invocations.py` covers command tree and service client behavior.
- `tests/subprocess/test_service_unlock_boundary.py` covers TTY-only confidential input.
- `tests/conformance/surfaces/test_cli_mcp_parity.py` covers exact operation parity.
- `tests/packaging/test_service_boundary_imports.py` covers import trust boundary.

## Open questions

None.
