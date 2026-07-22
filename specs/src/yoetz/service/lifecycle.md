# src/yoetz/service/lifecycle.py — service singleton, state machine, relock, and shutdown

**Wave:** C | **ADRs:** ADR-001, ADR-004, ADR-008 | **Imports (spec-tree):** `ports/control.md`,
`ports/clock.md`, `ports/diagnostics.md`, `ports/secret_memory.md`, `protocol/canonical.md`,
`config/paths.md` | **Imported by:**
`service/daemon.md`, `adapters/session_events.md`

## Purpose

Owns the per-user service lifecycle independently of workflow logic: singleton acquisition,
`starting/locked/unlocking/ready/draining/failed` transitions, client/in-flight accounting,
explicit and automatic relock, session/suspend events, bounded drain, and endpoint cleanup.

## Public surface

- `class ServiceLifecycle` with async `acquire_singleton`, `publish_endpoint`, `transition`,
  `admit`, `release`, `request_lock`, `request_stop`, `on_session_event`, `note_activity`,
  `run_idle_monitor`, `change_idle_relock_policy`, and `close`.
- Private `ServiceGenerationStore` — owner-only, nonsecret durable metadata with
  `advance(instance_id) -> positive canonical generation`; it is the sole generation source
  available while locked and contains no catalog route, task, user content, key, or credential.
- `@dataclass(frozen=True, slots=True) class ServiceInstance` — random instance ID, monotonic
  service generation, process-start identity commitment, and current state; no PID/path.
- `@dataclass(frozen=True, slots=True) class Admission` — one nonserializable reference recording
  method, secret-use class, and commit-section state.
- `enum SessionSecurityEvent` — `user_session_locked`, `system_suspend`, `user_session_unlocked`,
  `system_resume`, `monitor_lost`.
- `@dataclass(frozen=True, slots=True) class IdleRelockPolicy` — default 900 seconds; valid
  `60..86400`, or disabled only through fresh local-human reauthorization. The internal disabled
  representation is `seconds=None`; wire targets/results use the explicit tagged form owned by
  `service/confidential_protocol.md`, never JSON null/infinity.
- Constants `LOCK_DRAIN_SECONDS = 5`, `STOP_DRAIN_SECONDS = 30`,
  `IDLE_STOP_SECONDS = 1800`.
- `class LifecycleError(Exception)` — bounded reasons including `service_already_running`,
  `invalid_transition`, `vault_locked`, `service_draining`, `session_monitor_unavailable`,
  `human_authorization_required`, `human_authorization_stale`.

Construction requires one injected `ClockPort`; lifecycle never reads ambient monotonic time.
Its private ready-state record carries `current_vault_generation: int | None`. The generation is
required and positive on every transition into `ready`, is unchanged while ready, and is cleared
before entering draining/locked/failed. It is internal proof-validation state, not service status.

## Behavior

### Singleton and second-daemon rejection

`acquire_singleton` verifies the owner-only runtime directory and takes nonblocking `fcntl.flock`
on the fixed service lock file. It probes any existing endpoint and validates its owner/type before
removing a stale socket. The lock is a lifecycle fast gate; catalog/service generation CAS remains
the ready-writer correctness fence after unlock. Only after singleton acquisition and nonsecret
generation commit may a process publish an endpoint; it may open the catalog only after vault
unlock. A second daemon reports `service_already_running` and exits without touching vault or
storage. The lock descriptor stays open until endpoint removal during final close.

While still under the singleton, the lifecycle opens only the separate nonsecret generation file
in the installation service-metadata directory (not the encrypted catalog). The canonical closed
record is `{schema_version:"1", installation_id:ins_, generation:positive-decimal,
last_instance_id:svc_, record_digest:sha256:<64 lowercase hex>}`. It advances generation by one via
owner-only temp file, fsync, atomic replace, and parent fsync before any endpoint advertises the new
instance. Gaps after crash are valid; missing-after-install, malformed, rollback, symlink, wrong
owner/mode, or non-atomic filesystem state fails startup and is never reset. A pristine install
creates generation 1 once under the same singleton.

`record_digest` is exactly
`sha256("yoetz/service-generation/v1\0" || canonical_json(record_without_record_digest))`; the
digest field itself is omitted while hashing. Encoding uses the canonical protocol, and the file
ends with one LF outside the hashed JSON. Writes use a same-directory unpredictable owner-only temp,
full file fsync, atomic rename, and directory fsync. The store opens only the fixed
`config/paths.service_generation_path()` with no-follow and rechecks owner/mode/type/link count.

The locked service does not open the encrypted catalog. After vault unlock, catalog admission CAS
copies/checks the already advertised service generation as writer owner. A catalog generation
greater than or contradictory to the nonsecret store proves rollback/corruption and terminates the
service; it never changes generation behind connected clients. Thus keyring-locked startup can
publish a durable fence without violating ADR-004's no-catalog-while-locked boundary.

### State transitions

Allowed transitions are exactly:

```text
starting -> locked | ready | failed
locked -> unlocking | draining | failed
unlocking -> ready | locked | failed
ready -> draining | failed
draining -> locked | failed | final close
failed -> draining | final close
```

`user_session_unlocked` and `system_resume` never transition to ready. Unlock retry is a fresh
keyring or confidential-ingress ceremony. State publication contains only bounded reason codes.
For `vault_mode=uninitialized`, `locked -> unlocking` is allowed only for a fresh keyring retry or
an explicit `vault_initialize` challenge after the vault re-proves pristine empty state. It is not
an unlock alias and cannot apply to an existing keyring/passphrase mode.

### Admission, lock, and stop

`admit` is possible only in `ready`, except structural service status. `client_connected` /
`client_disconnected` hold the process-idle lease for authenticated ordinary/human connections.
Admission increments connected,
in-flight, provider-call, secret-consumer, writer-queue, and shielded-commit counters under one
lock. An idle interval begins only when every such counter/lease is zero.

Explicit lock, user-session lock, system suspend, or idle expiry atomically changes `ready` to
`draining` and stops admission. Noncommitting work and provider calls are cancelled. Shielded
SQLite commits reach a definite result within `LOCK_DRAIN_SECONDS`; object temp files follow their
crash-cleanup rules. If any secret consumer or uninterruptible work remains at the deadline, the
service exits rather than claim it relocked while secrets may remain. Otherwise it closes the
application/runtime/provider, clears vault and secret-memory handles best-effort, and enters
`locked` while retaining the authenticated endpoint.

`request_stop` uses the 30-second bound, then removes only this instance's endpoint and exits.
Signal TERM uses stop; INT while foreground uses the same path. KILL relies on successor generation
fencing and stale-endpoint validation.

### Session monitoring and idle policy

The advertised macOS/Linux build must positively prove session-lock and suspend monitoring via
`adapters/session_events.md`. Loss of a previously active monitor triggers immediate draining and
locked state. Default idle relock is 15 minutes of true quiescence; a connected MCP bridge or
long-running operation is not idle. Changing/turning off this policy requires an OS user-presence
assertion or confidential reauthentication proof; an MCP request or boolean CLI confirmation is
insufficient.

Independently, 30 minutes with no connected local client and no admission requests a bounded full
stop from ready or locked state. Disconnect/activity resets the full interval. Restart restores the
same fixed policy and advances the service generation.

`change_idle_relock_policy(proposed, proof)` is the only mutation path. It is service-internal,
callable only by the still-live `idle_relock_policy_change` branch of `HumanControlService`, and
requires state `ready`. Under the lifecycle mutex it snapshots the current policy and recomputes
the exact target digest as
`sha256("yoetz/idle-relock-policy-change/v1\0" || canonical_json({service_generation,
current:<finite-or-disabled>, proposed:<finite-or-disabled>}))`. It requires an unexpired,
unconsumed `HumanAuthorizationProof` with purpose `idle_relock_policy_change`, that exact digest,
the current service generation, the required private current vault generation, and
`policy_generation=None`. Under the same mutex it samples `clock.monotonic_seconds()` once and
calls the proof's exact `consume` method with all those expected fields before swapping the policy.
A missing/changed vault generation or invalid clock is stale authority. Any validation/race failure
consumes no authority and leaves the prior policy unchanged.

A finite policy arms a fresh full interval from policy application time when the service is truly
quiescent; otherwise the interval begins only after every admission/activity counter becomes zero.
Disabling cancels only the idle deadline and does not affect explicit, session-lock, suspend, or
monitor-loss relock. The result and structural status omit `idle_relock_seconds` when disabled.
This v0.1 change is intentionally scoped to the current service generation and is not written to
config or any durable file; restart restores the safe 900-second default and requires a new human
ceremony to disable it again.

## Errors and edge cases

- Crash after lock acquisition but before endpoint publication leaves no discoverable service;
  successor obtains the released OS lock and advances the separate generation store again.
- Corrupt/missing/rolled-back generation metadata on a non-pristine install blocks all endpoints;
  catalog access is not used as a locked-state repair fallback.
- Stale socket removal uses no-follow owner/type checks and directory-fsync; a symlink, foreign
  owner, hard link, or non-socket blocks startup.
- Sleep may arrive during a shielded commit or provider response. Admission stops first; ambiguous
  operation results remain durably replayable.
- `monitor_lost` is never ignored while ready.
- Repeated/concurrent lock/stop requests coalesce on the one draining task and are idempotent.
- An idle-policy proposal raced by lock/drain, another policy change, expiry, proof replay, or
  service-generation change returns `human_authorization_stale` and preserves the prior policy.

## Invariants

1. At most one service instance for the per-user installation can publish/open authority.
2. Locked means no application, provider, decrypted object, bundle key handle, or workflow
   admission remains live.
3. Wake/session unlock/ordinary client activity cannot unlock the vault.
4. Idle timing excludes all active/queued/leased work and cannot interrupt a task misclassified as
   idle.
5. A relock deadline failure terminates the service; it never reports a false locked state.
6. Service generation is durably advanced before endpoint publication without opening the
   encrypted catalog; it never changes during one service instance.
7. Idle relock can be disabled only by consuming one exact vault-minted human-authorization proof;
   the exception is generation-scoped and restart restores the 900-second default.

## Tests

- `tests/unit/service/test_lifecycle.py` exhaustively covers allowed transitions, admission
  counters, idle calculation, concurrent lock/stop, deadline escalation, exact target-digest/proof
  validation, finite/disabled application, races/replay, and restart default restoration.
- `tests/subprocess/test_service_daemon_lifecycle.py` races two daemons, kills each startup/drain
  point, validates endpoint cleanup, nonsecret generation atomicity/rollback rejection, and proves
  locked startup does not open the catalog.
- `tests/integration/service/test_locked_ready_transitions.py` injects lock/suspend/resume/monitor-
  loss events during reads, provider calls, object writes, and shielded commits.

## Open questions

None.
