# ADR-001 — Persistent local service, single-writer lifecycle, and client attachment

**Status:** Founder-selected working decision (2026-07-14). The persistent-service topology is
binding for v0.1 specification work. Lifecycle, peer-authentication, and crash-takeover proofs
remain release gates.
**Owning public specs:** `docs/adr/ADR-008-local-service-vault-trust-boundary.md`,
`specs/src/yoetz/service/daemon.md`, `specs/src/yoetz/service/lifecycle.md`,
`specs/src/yoetz/service/control_protocol.md`, `specs/src/yoetz/ports/runtime.md`,
`specs/src/yoetz/adapters/runtime.md`, and the service subprocess specifications.

## Context

CLI, MCP, and a future UI are communication surfaces, not independent Yoetz runtimes. Letting each
surface open SQLite and the key backend would put decrypted state and unlock behavior in untrusted
or agent-facing processes, make safe passphrase-based MCP operation impractical, and recreate the
multi-writer race the generation fence is meant to prevent.

## Decision

One persistent, per-user local Yoetz service is the only process allowed to own the installation
catalog, task-bundle writer connections, key handles, decrypted objects, provider adapters, or the
application facade.

1. CLI, MCP, and future UI processes are clients of the service through the authenticated local
   control protocol. They never open the catalog, a task bundle, an object store, a key backend, or
   a provider adapter.
2. The service owns one dedicated SQLite writer connection for the installation catalog and at
   most one dedicated writer connection for each task bundle it has opened. All logical clients
   are multiplexed through those service-owned writers.
3. The service can be `locked`: its local control endpoint and bounded structural status remain
   available, but no workflow, payload read, write, import, maintenance operation, provider call,
   or bundle route is admitted until its vault is ready.
4. Starting a second service for the same per-user installation is rejected before it opens a
   writer. CLI and MCP never fall back to direct execution when the service is absent, locked, or
   busy.
5. After a service crash, one successor validates durable state, advances the catalog/service
   generation, and lazily advances each bundle generation before opening its writer. Advancing a
   generation invalidates every older lease and handle regardless of wall clock.

The service remains alive when a client disconnects. A lost client response is resolved by
reconnecting and replaying the same request ID; it never transfers storage ownership to the
client.

## Local authority and attachment

- The service binds an owner-only Unix-domain control endpoint beneath the per-user runtime
  directory. Both sides verify peer credentials: effective UID equality via `SO_PEERCRED` on
  Linux and `getpeereid` on macOS. Socket mode alone is defense in depth, not authentication.
- No bearer token, vault key, passphrase, environment secret, or caller-supplied path selects the
  service. Endpoint discovery is deterministic from the verified per-user platform path.
- The normal control protocol has an exact method allowlist. It carries validated workflow and
  support requests, never key material or an unlock secret.
- The confidential unlock path is separate from the normal client and MCP bridge and is governed
  by ADR-008. MCP exposes no service-management or unlock tool.
- A future UI may become another authenticated local client, but it does not gain direct storage
  or vault access by being local.

## Ownership mechanism

The persistent service removes normal cross-client writer contention, but durable fencing remains
mandatory because stale or duplicate service processes must fail safely.

- A catalog owner generation identifies the active service instance. Each bundle has its own
  monotonic `owner_generation` and random owner nonce in `bundle_meta`.
- Acquisition and takeover use `BEGIN IMMEDIATE` compare-and-swap. Every write, operation lease,
  import lease, and checkpoint verifies the current generation inside its transaction.
- Heartbeat time, PID, process-start metadata, boot identity, service endpoint, and advisory lock
  files are diagnostics only. None can authorize a write.
- The service may cache task runtimes lazily. Every warm route rechecks generation and admitted
  capability. Generation loss poisons the cached handle; the current request fails closed and
  cannot silently reacquire authority mid-operation.
- Maintenance runs inside the same service and uses the same writer queues and fences. There is no
  separate maintenance process with a hidden direct-storage path.

## Lifecycle contract

v0.1 ships a foreground `yoetz service run` entrypoint suitable for an explicit terminal or
an external per-user supervisor. Normal CLI commands do not secretly spawn, daemonize, or unlock a
service. Native launchd/systemd-user installation and automatic startup are product conveniences,
not correctness prerequisites, and require separate reviewed packaging files before they can be
claimed.

Startup orders local path and endpoint verification before vault unlock; then it tries the approved
OS keyring. A successful unlock constructs the vault, catalog runtime, application, and provider
gateway once and changes state to `ready`. Expected keyring unavailability or lock leaves the
service alive in `locked`. Shutdown stops admission, resolves shielded commits, closes providers,
task runtimes, catalog, and vault handles in reverse order, then removes only the endpoint owned by
the current service instance.

## Alternatives considered

| Topology | Result | Reason |
|---|---|---|
| Persistent service owns writers and vault | **Selected for v0.1** | One trust boundary, one writer authority, unlock once, and CLI/MCP/UI parity. |
| Separate key broker plus independent application processes | Rejected for v0.1 | It still leaves multiple storage owners and either exports usable key capability or requires a second cryptographic RPC protocol for every object operation. It adds a failure boundary without solving writer topology. |
| Each CLI/MCP process opens and unlocks the bundle | Rejected | Secrets or unlock prompts reach agent-facing processes, MCP cannot safely prompt, repeated unlocks expand exposure, and independent writers contend. |
| MCP process is the service; CLI opens directly when MCP is absent | Rejected | Service lifetime becomes coupled to one integration and behavior changes based on which surface happened to start first. |

## Required proof before release

Prove: same-UID peer checks on every advertised platform; owner-only endpoint replacement and
symlink attacks; two services racing startup with one winner; CLI and multiple MCP bridges
concurrently using one writer; locked-state method denial; kill/restart generation advance; stale
service writes and checkpoints rejected; response-loss replay; bounded backpressure; service
shutdown during queued and committing work; no key/unlock material in any client process,
argument, environment, log, trace, transcript, or MCP frame.

## Consequences and deferred work

Persistent service and local IPC are in v0.1, not deferred. Distributed service access,
cross-user/system-wide service mode, TCP/network control, concurrent independent writers, and
client-side direct-storage fallback are out of scope. Native autostart integration may be added
only through explicit platform packaging specs; its absence must not weaken the foreground service
contract.
