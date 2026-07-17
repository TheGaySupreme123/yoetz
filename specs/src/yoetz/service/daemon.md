# src/yoetz/service/daemon.py — trusted per-user local service composition root

**Wave:** D | **ADRs:** ADR-001, ADR-004, ADR-006, ADR-008 | **Imports (spec-tree):**
`service/lifecycle.md`, `service/control_protocol.md`, `service/confidential_protocol.md`,
`service/vault.md`, `service/secret_ingress.md`, `service/human_control.md`, `application/service.md`, `adapters/runtime.md`,
`adapters/control/unix_socket.md`, `adapters/session_events.md`, `ports/secret_memory.md`,
`resources/support/runtime-support.json.md`, `config/load.md` |
**Imported by:** `cli/app.md` only for `service run`; service subprocess tests

## Purpose

This is the only production composition root allowed to construct the vault, catalog/task runtime,
application facade, privacy/egress gateway, or concrete provider adapters. It runs once per user
installation and dispatches all CLI/MCP/future-UI requests through one authenticated local service.

## Public surface

- `async run_service() -> Never` — foreground service main coroutine.
- `main() -> Never` — one `anyio.run(run_service)` process entrypoint with bounded exits.
- `class ServiceDaemon` with async `start`, `serve`, `dispatch`, `lock`, `stop`, and `close`.
- `@dataclass(frozen=True, slots=True) class ServiceComposition` — lifecycle, control listener,
  secret-ingress listener, human-control listener/service, session monitor, vault, and optional ready-only application; private to
  this module and constant-redacted.

No public constructor accepts caller-selected roots/endpoints, raw secrets, key handles, provider
clients, or an already-open database.

## Behavior

### Startup

`run_service` performs exactly:

1. Parse/validate service configuration and verified per-user data/runtime paths; configure
   allowlisted diagnostics; suppress core dumps where supported.
2. Acquire the lifecycle singleton. A second daemon exits boundedly before keyring/catalog access.
3. Advance service generation in the separate owner-only nonsecret lifecycle store, bind the three
   owner-only ordinary, one-secret, and human-control local endpoints,
   start peer-authentication and session-event monitoring, then publish `starting` status.
4. Construct `SecretMemoryPort` and the reviewed `UserPresencePort | None`, then inject that same
   presence port into `VaultService`, the sole proof minter; load
   and verify the packaged runtime-support allowlist, then measure the exact user-presence release
   cell before allowing any pristine keyring creation. No caller/config boolean supplies this
   result.
5. Expected keyring locked/unavailable, pristine `human_authority_unavailable`, or an explicitly passphrase-backed vault changes state to
   `locked` and continues serving structural status/confidential unlock. No catalog, provider,
   workflow runtime, or decrypted task state is opened.
   On a pristine first install, automatic keyring mode runs only when both the exact keyring and
   exact artifact-bound active user-presence cells pass. An unusable keyring reports its bounded
   reason; a usable keyring without verified presence reports `human_authority_unavailable`. Both
   remain `uninitialized/locked` with no vault/keyring mutation and never select passphrase mode
   until the local human explicitly completes the distinct `vault_initialize` ceremony.
6. Successful vault unlock constructs the installation catalog/runtime, application facade,
   central egress-policy gateway, and only the credential-free reviewed bundled provider factories
   allowed by active policy, once. No provider credential is retrieved at startup; the gateway
   mints one body/profile/deadline-bound opaque handle per authorized physical attempt, never from
   config/environment bytes.
   Catalog writer admission verifies/copies the already advertised service generation and fails on
   any greater/contradictory catalog value; locked startup never opens the catalog to obtain it.
7. Run startup gates, publish `ready`, then admit requests. A gate failure closes the partial
   ready composition and enters `failed` or `locked` according to the bounded failure class; it
   never exposes a half-ready application.

Before publishing `ready`, the daemon derives a generation-bound `HumanAuthorityCapability` from
vault mode plus measured `UserPresencePort`, then calls gateway `reconcile_policy` with it and the
durable effective policy. It may publish ready with an explicitly unavailable external binding when an installed
profile/factory is absent; deterministic checks then return incomplete semantic status. Every later
policy commit invokes the same generation-fenced
reconciliation without restarting the service.

In `os_keyring` mode with no measured strong user-presence capability, the snapshot is
`unavailable`: startup and every dispatch remain external-egress-disabled/local-only even if stored
policy/credentials are wider. The daemon does not rewrite either. The snapshot is fixed for one
service/vault generation; restart/relock/ready recomposition remeasures it, and an explicit
presence-unavailable outcome during human control invalidates it and reconciles. v0.1 does not
claim an asynchronous OS-presence watcher. Restoration may reactivate only through a fresh ready
composition and generation-fenced reconcile. Passphrase mode advertises `established_passphrase` because exact
reauthentication remains available. Bounded status may report `human_authority_unavailable` but no
credential/policy detail.
This paragraph applies to an already committed keyring vault. On a pristine installation the same
missing capability prevents keyring-mode creation entirely and leaves setup required.

### Dispatch

The ordinary listener authenticates peer UID before protocol parsing. `dispatch` applies client-
kind/method/state/admission policy, validates the method body, calls the one ready `Application` or
support service, and validates the internal result. For every ready content-capable workflow or
support success it invokes `Application.project_result_for_client(client_kind, method, result)`,
validates that projected body and its durable local-disclosure receipt binding, and only then
serializes a control envelope. The exact exceptions are handshake, fixed control-error bodies, the
closed structural-only `service_status`, `service_lock`, and `service_stop` results, and the
ready-only structural `privacy_receipts_list|privacy_receipts_get` inspection results. The first
group must remain available while locked/draining or after the Application has closed and admits no
user-content field. Receipt inspection is projection/audit-exempt only for authenticated ordinary
CLI/UI while ready, because projecting an already-durable receipt view would recursively create
another receipt. No logger, tracer, renderer, MCP bridge, or socket writer sees an unprojected
content-capable result. Requests from all clients
share runtime/task caches, writer queues, key handles, policy, and provider coordinator.

Ordinary `mcp_bridge` and `ui` results use `agent_context`. An ordinary CLI result uses
`local_human_view` only for validated human-readable rendering on an attached controlling terminal;
`--json`, piped/redirected output, non-TTY use, or any absent/contradictory presentation state uses
`agent_context`. Selection is server-side and fail-safe; a client cannot name an arbitrary sink.
Only the separate authenticated foreground human-control endpoint can request
`trusted_human_control`, and it never proxies an ordinary result back through MCP. Initial local-
audit reservation failure produces `privacy_projection_unavailable` and no content-bearing bytes;
a structural omission-only projection remains a normal successful response when policy blocks all
user content.

Confidential frames are handled only by `SecretIngressService`; secret bytes never enter
`dispatch`, ordinary envelopes, tracing, or the application facade. An accepted vault unlock asks
`VaultService` to transition, then constructs a fresh ready composition. Provider credential input
updates only an encrypted vault record and returns structural success.

All confidential bindings/challenges originate from `HumanControlService` on the third endpoint;
both server endpoints parse the one shared pure `confidential_protocol.md` contract. The client
implementation lives separately in `confidential_client.md` and is never imported here.
It coordinates zero-secret keyring retry, YZS1 secret phases, provider credential set/rotate, and
privacy typed decisions, plus exact human-reauthorized current-generation idle-relock policy
changes. The ordinary dispatcher/MCP cannot connect or proxy to it. Successful
credential storage atomically preserves/rotates the encrypted record, then triggers provider-policy
reconciliation before returning structural activation status.

An accepted `vault_initialize` frame is possible only for a freshly re-proven empty uninitialized
vault. `UnlockCoordinator` atomically commits the passphrase envelope/sentinel/mode before the
daemon constructs ready composition. If the outer ready gate then fails, the daemon remains locked
in committed passphrase mode; it never repeats initialization, falls back from an existing keyring
vault, or erases ambiguous first-install state.

Ordinary privacy-control methods may read setup/effective policy, persist an inert proposal, tighten,
or list/get bounded structural privacy receipts. The two receipt reads use the exact exemption above.
The daemon never dispatches policy/disclosure approval through ordinary control. Those
decisions complete inside `HumanControlService` after foreground TTY preview and vault
reauthentication; its internal proof is consumed without crossing back to the helper.

Idle-relock mutation is likewise absent from ordinary control. The daemon starts every service
generation with the 900-second policy, and only the exact `idle_relock_policy_change` human-control
ceremony may apply a finite 60..86400-second value or disable idle relock for that generation. The
proof is minted only by `VaultService`, applied/consumed only by `ServiceLifecycle`, and never
persisted; restart restores 900 seconds. Explicit/session/suspend/monitor-loss relock remains active
even while idle relock is disabled.

### Lock and shutdown

Explicit lock, idle expiry, session lock, suspend, or monitor loss uses the lifecycle drain. It
cancels active human ceremonies and noncommitting/provider work, resolves shielded commits, closes application/runtime/provider
in reverse order, clears secret/vault handles best-effort, and remains alive `locked`. If cleanup
cannot meet the lock bound, the process terminates rather than report a false lock. Stop performs a
longer bounded drain, removes only this instance's endpoints, releases singleton authority, and
exits. KILL recovery depends on generations, not cleanup.

## Errors and edge cases

- An absent/locked provider credential cannot be supplied by a client; semantic work follows its
  configured incomplete-check semantics without bypassing the vault.
- A service crash after committing but before replying remains idempotently replayable by request
  ID after successor startup/unlock.
- A client disconnect does not stop the service or close shared task handles.
- A projection/audit failure after the workflow committed does not expose the internal result;
  identical request replay may recover the durable operation and retry projection without
  repeating the operation side effect.
- Initialization crash ambiguity or any existing mode/ciphertext/keyring record fails closed before
  ready composition and cannot be repaired by selecting passphrase mode again.
- Pristine keyring capability mismatch fails before staging/key generation and returns locked
  `uninitialized`/`human_authority_unavailable`; an existing keyring vault with the same current
  mismatch may enter ready-local but never external-ready.
- Forking/daemonizing after vault unlock is forbidden. Supervisors start the foreground process;
  the process never double-forks.
- Fatal internal exceptions produce only bounded structural correlation identity, trigger drain,
  and exit 70 without formatting, emitting, or capturing a traceback.

## Invariants

1. Only this module imports both application composition and concrete vault/storage/provider
   adapters in production.
2. Locked service has no live application/runtime/provider or decrypted bundle key handle.
3. Every normal request crosses peer authentication, frozen control validation, lifecycle
   admission, application validation, and result validation.
4. CLI/MCP disconnection cannot change writer or vault ownership.
5. Provider credential and unlock bytes never enter normal configuration, environment, or control
   dispatch.
6. First-install `vault_initialize` and later `vault_unlock` are distinct confidential purposes;
   neither can substitute for the other.
7. Pristine automatic keyring creation requires the exact same-artifact keyring/user-presence cell
   intersection; existing keyring load is exempt only for local data availability and remains
   externally fenced without presence.
8. Ordinary response serialization is impossible without service-side local-disclosure projection;
   bridges cannot opt out or reconstruct omitted content.
9. Throttle transitions belong only to `UnlockCoordinator`, proof minting only to `VaultService`,
   and idle-relock policy application only to `ServiceLifecycle`.

## Tests

- `tests/integration/service/test_daemon_clients.py` exercises every method through concurrent CLI,
  MCP, and synthetic UI clients against one application/runtime.
- `tests/subprocess/test_service_daemon_lifecycle.py` covers startup, second-daemon rejection,
  locked/setup-required service, the pristine two-capability gate, existing-keyring ready-local,
  signals, crashes, and supervisor-style foreground execution.
- `tests/subprocess/test_service_secret_boundary.py` canary-scans daemon/client process metadata,
  frames, stderr, logs, traces, temp files, and dumps.

## Open questions

None.
