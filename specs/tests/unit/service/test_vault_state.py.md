# tests/unit/service/test_vault_state.py — vault state and handle-generation unit suite

**Wave:** C | **ADRs:** ADR-004, ADR-008 | **Imports (spec-tree):** `src/yoetz/service/vault.md` | **Imported by:** test runner

## Purpose

Freeze vault mode/state transitions and least-authority bundle/provider/authorization handles.

## Public surface

Model-adapter tests for keyring initialization, pristine passphrase initialization, later unlock,
lock/record operations and stale handles.

## Behavior

Cover OS/passphrase/uninitialized modes, both state-fenced keyring retry branches, correlated
create-once keyring entry/stage/sentinel/mode publication, the exact keyring/presence release-cell
cross-product before pristine mutation, distinct `vault_initialize`/
`vault_unlock` handles, atomic passphrase envelope/sentinel/mode publication, create-once BMK,
installation MAC derivation/purpose/domain handles, per-physical-attempt provider binding and
one-shot transport callback, policy proof digest/expiry and relock invalidation.
Freeze that vault verification runs only after a coordinator reservation, the vault never accesses
the throttle store, and only the vault constructs provider/privacy/security authorization proofs.

## Errors and edge cases

Missing/locked/wrong/tamper/stale/mismatch/replacement attempts, existing-ciphertext/keyring
fallback attempts, pre-entry safe stage cleanup, exact entry+stage resume, one-sided/mismatched/
multiple ambiguous keyring states, every publication crash point, concurrent initialize/unlock,
wrong provider body/profile/deadline/dispatch, callback reuse, and relock during header injection.
Keyring-usable plus absent/unavailable/inconclusive/stale/mismatched/cross-artifact presence must
remain uninitialized/locked with `human_authority_unavailable` and zero stage/IVK/entry/marker;
existing keyring load without presence must reach ready-local with external fencing.

## Invariants

1. Locked never returns a handle.
2. Provider credentials remain opaque and exactly bound.
3. Existing/ambiguous vault state can never re-enter initialization.
4. Existing-mode retry is load-only; pristine-uninitialized retry is the only create path.
5. Each provider physical attempt receives a new bound handle; no SDK client can retain the real
   credential.
6. The pristine create path additionally requires exact same-artifact active user-presence
   evidence; keyring usability alone never mutates state.

## Tests

This file is the executable owner.

## Open questions

None.
