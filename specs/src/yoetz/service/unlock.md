# src/yoetz/service/unlock.py — vault unlock and local-human reauthentication coordinator

**Wave:** C/D | **ADRs:** ADR-004, ADR-008 | **Imports (spec-tree):** `service/vault.md`,
`service/confidential_protocol.md`, `service/secret_ingress.md`, `service/lifecycle.md`,
`ports/secret_memory.md`, `ports/clock.md`,
`config/paths.md`, `protocol/canonical.md` |
**Imported by:** `service/daemon.md`, confidential ingress tests

## Purpose

Coordinates the explicit uninitialized-to-passphrase first-install transition, locked-to-ready
vault transitions, and fresh local-human reauthentication. It does not read a terminal itself and
is unreachable from ordinary control/MCP/application methods. It keeps initialization/unlock
state, rate limits, generation binding, and ready-composition construction atomic.

## Public surface

- `class UnlockCoordinator` with async `retry_keyring`, `begin_passphrase_initialization`,
  `complete_passphrase_initialization`, `begin_passphrase_unlock`,
  `complete_passphrase_unlock`, `begin_reauthentication(purpose, target_digest)`,
  `complete_reauthentication(source) -> HumanAuthorizationProof`, `cancel`, and `close`.
  The returned proof is the exact object minted by `VaultService`, remains service-internal, and is
  handed directly to `HumanControlService`; this coordinator never constructs one.
- `@dataclass(frozen=True, slots=True) class UnlockChallenge` — one-use structural binding for the
  confidential ingress; no user/provider/key detail.
- `@dataclass(frozen=True, slots=True) class UnlockResult` — state and bounded reason only.
- `class UnlockThrottleStore` — sole owner of the crash-safe locked-state throttle record.
- `@dataclass(frozen=True, slots=True) class UnlockThrottleRecord` — exact record below; no secret,
  user content, entered length, purpose target, or derived passphrase evidence.
- `class UnlockError(Exception)` — bounded reasons matching lifecycle/vault/ingress failures.

## Behavior

`retry_keyring` accepts no secret and serializes one state-fenced attempt. In a pristine
`uninitialized` state it re-probes the approved OS keyring and remeasures the installed
`UserPresencePort` against the same-artifact packaged runtime-support cell. It may run the vault's
staged create-once first-install protocol only when both pass and a fresh
`FirstInstallKeyringAuthority` is minted. A locked/unavailable keyring or absent/unverified
presence leaves the service uninitialized/locked with no write; the latter returns
`human_authority_unavailable`. In committed `os_keyring` mode it may only load and verify the exact existing
correlated entry. Both branches change service/vault state to `unlocking` only for their attempt and
either construct/validate complete ready state or return to locked. Passphrase mode, ambiguous or
non-pristine initialization, and missing/mismatched committed keyring material reject without
creation, deletion, replacement, or fallback.

`begin_passphrase_initialization` is available only when the service is locked, mode is exactly
`uninitialized`, and `VaultService` has just re-proven a pristine installation: no committed mode,
encrypted vault record/sentinel, keyring entry, or ambiguous staging artifact. It changes state to
`unlocking` and returns a one-use challenge expiring after
`confidential_protocol.CEREMONY_EXPIRY_SECONDS` with purpose `vault_initialize`, bound to the
service generation and a canonical first-install-state digest. It is not offered automatically
after keyring failure; the foreground local human explicitly chooses the separate initialize-
passphrase ceremony.
This includes the setup-required case where keyring storage is usable but automatic keyring mode
was blocked for missing release-cell user-presence evidence; because that branch wrote nothing,
the same pristine proof can authorize an explicit passphrase choice.

`complete_passphrase_initialization` accepts only the matching
`SecretHandle(vault_initialize)`. It rechecks the identical pristine-state digest under singleton
authority, has `UnlockThrottleStore` stage the exact generation-1 zero-failure/no-active-attempt
record, and passes only that record's digest with the handle to
`VaultService.initialize_passphrase`. The coordinator remains sole owner of creating, publishing,
repairing, and later mutating the throttle record. The vault call atomically
commits the passphrase envelope, authenticated empty-vault sentinel/layout, and immutable
`passphrase` mode. Only then does it run the full ready startup gate. If that outer gate fails after
the vault commit, the new mode remains passphrase and the service returns locked with a bounded
startup reason; initialization cannot be repeated or rolled back implicitly. A crash/contradiction
that cannot prove either pristine-uninitialized or fully committed passphrase state fails closed as
tampered/failed and never deletes, overwrites, or guesses which state won.

`begin_passphrase_unlock` is available only for explicit passphrase mode. It checks service
generation, rate limit, no active attempt, and confidential endpoint readiness, then returns a
one-use challenge expiring after `confidential_protocol.CEREMONY_EXPIRY_SECONDS`.
`complete_passphrase_unlock` accepts only the matching `SecretHandle(vault_unlock)`. The
coordinator waits/reserves the durable throttle attempt before calling `VaultService.unlock`, then
alone charges or resets that record from the bounded vault outcome. It runs the complete ready
startup gate, and publishes ready only after application/runtime/provider policy composition is
fully valid. Any failure closes partial state and returns locked. It never reveals wrong-secret
versus tamper details to ordinary clients.

Reauthentication follows the same confidential ceremony while ready. It is purpose- and target-
bound for provider credential change, privacy-policy widening, or current-generation idle-relock
policy change. `begin_reauthentication` freezes the corresponding challenge and expiry. For an OS
presence source, `complete_reauthentication` delegates the exact opaque attestation and challenge
to `VaultService.mint_human_authorization`. For a passphrase source it first applies the throttle
gate/reservation, delegates the exact purpose-specific secret handle and challenge to that same
vault method, and alone charges/resets the throttle from its result. In both branches the only
returned object is the `HumanAuthorizationProof` constructed by `VaultService`, bound to the exact
purpose/target digest/generations/expiry. `HumanControlService` consumes it immediately with the
exact pending change; it is never returned or serialized outside the service. A
boolean confirmation, CLI flag, MCP call, agent message, or normal control request cannot complete
it. The proof is single-use and cannot unlock a vault or authorize another policy.

### Restart-safe passphrase throttle

The throttle applies to `vault_unlock`, `provider_reauthentication`,
`privacy_reauthentication`, and `security_reauthentication`; initialization and portable recovery
have separate creation/artifact semantics. Its fixed owner-only record at
`config.paths.unlock_throttle_path()` is canonical JSON:
`{schema_version:"1", installation_id, vault_mode:"passphrase", record_generation,
consecutive_failures, attempt_in_progress, last_failure_utc, last_writer_instance_id,
record_digest}`. Generation is positive and monotonic; failures are capped at 63;
`last_failure_utc` is RFC 3339 UTC or null. Digest is exactly
`sha256("yoetz/unlock-throttle/v1\0" || canonical_json(record_without_record_digest))`; one LF is
written after the canonical object and is outside the digest. Same-directory random temp create
with `0600`, file fsync, no-replace/rename, directory fsync, no-follow reopen, and owner/mode/type/
link-count recheck are mandatory.

Passphrase-mode initialization stages generation 1 with zero failures/no active attempt and binds
its digest into the authenticated vault layout before the immutable mode publication point. Before
each KDF verification, the coordinator waits for the current process monotonic deadline, then
atomically writes `attempt_in_progress=true`. A failed/tampered/cancelled ambiguous verification
atomically increments/caps failures, clears in-progress, records wall-clock evidence, and only then
returns; a crash with in-progress is charged as one failure on restart before another attempt.
Successful envelope+sentinel verification atomically resets failures/in-progress before ready or
proof publication.

`UnlockCoordinator` is the only caller of `UnlockThrottleStore` and the only owner of delay,
reservation, charge, reset, restart re-arm, and repair. `VaultService` neither imports the store nor
returns a rate-limit decision; it performs one requested verification only after the coordinator's
reservation. Every vault verification outcome returns through the coordinator before ready/proof
publication, so there is no uncharged passphrase path.

Delay is exact: failures 0..2 => 0; for `n >= 3`,
`min(300, 30 * 2**(n - 3))` seconds. Within a process it is armed only from fresh monotonic time.
Monotonic values are never serialized. On every restart with `n >= 3`, the full derived delay is
re-armed from the new process's monotonic clock rather than subtracting wall time. Wall-clock
rollback/non-UTC/implausible-future evidence, record digest/generation/mode mismatch, missing record
after passphrase publication, or unsafe file state never shortens the wait: it arms the full
300-second delay and requires bounded repair/reconstruction under singleton authority. This
mechanism claims crash/restart throttling, not defense against a malicious active same-UID user who
can rewrite local files.

## Errors and edge cases

- Concurrent attempts coalesce/reject; no two KDF jobs run for one vault.
- Initialization raced with keyring creation, a new record/mode marker, or any changed pristine-
  state digest consumes/rejects the challenge without writing. A keyring retry likewise rechecks
  its exact branch/state digest and, for pristine creation, both capability cells before any
  create/load action.
- Missing, stale, inconclusive, or mismatched user-presence evidence on pristine keyring retry is
  `human_authority_unavailable`, not keyring failure; it writes nothing and does not silently open
  a passphrase challenge.
- Cancellation/timeout consumes the challenge, overwrites the secret, and restores locked/ready
  state without partial authorization.
- Session lock/suspend/explicit lock during unlock cancels it and remains locked.
- Throttle persistence failure, in-progress crash, clock rollback, and restart never produce an
  immediate attempt; the exact record/re-arm rules above apply and no persisted monotonic value is
  interpreted across reboot.
- Ready-composition failure after correct vault unlock relocks and reports the startup gate reason,
  not success.

## Invariants

1. A service is ready only after vault and complete application startup gates succeed together.
2. Unlock/reauth secrets are consumed once and never become application/control values.
3. Reauthentication proof is exact-purpose/digest/generation/expiry bound, single-use, and never
   crosses the service boundary.
4. No ordinary client can trigger a secret-consuming completion method.
5. First-install initialization, later passphrase unlock, and privacy reauthentication have
   distinct purposes/challenges and cannot substitute for one another.
6. Existing keyring/passphrase ciphertext can never enter initialization as a fallback/recovery
   route.
7. Keyring retry creates only from re-proven pristine uninitialized state; committed `os_keyring`
   retry is load-only and any ambiguous state is fail-closed.
8. Every passphrase verification is reserved in the durable throttle record before KDF work;
   restart/clock anomalies can only lengthen, never erase, the derived delay.
9. Pristine keyring retry is a two-capability gate; existing keyring retry remains load-only and
   may reach ready-local without current presence.
10. Throttling has one owner: `UnlockCoordinator`; proof minting has one owner: `VaultService`.
    Neither duplicates the other's state transition.

## Tests

- `tests/integration/service/test_locked_ready_transitions.py` covers pristine passphrase
  initialization, pristine keyring/presence re-probe/create/no-write matrix, existing-keyring
  ready-local load without presence, outer ready-composition
  failure after commit, and every ambiguous crash boundary.
- `tests/integration/service/test_secret_ingress.py` covers challenges, rate limits,
  reauthentication binding, cancellation, and session-lock races.
- `tests/unit/service/test_unlock_throttle.py` freezes record/digest/atomic-write vectors, exact
  delay table, in-progress crash charging, restart monotonic re-arm, and wall-clock anomalies.
- `tests/subprocess/test_service_unlock_boundary.py` proves no normal CLI/MCP/env/config/stdin path
  can supply an initialization or unlock secret and verifies distinct prompts/purposes.

## Open questions

None.
