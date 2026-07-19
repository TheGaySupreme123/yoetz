# tests/integration/service/test_locked_ready_transitions.py — vault/service transition integration

**Wave:** C | **ADRs:** ADR-004, ADR-008 | **Imports (spec-tree):** lifecycle/vault/unlock/session-event specs | **Imported by:** test runner

## Purpose

Prove keyring initialization, explicit pristine passphrase initialization, later unlock, ready
construction, explicit/idle/session/suspend relock, and stale-handle denial.

## Public surface

Fault-injected state/event matrix across storage/provider/commit/secret-consumer boundaries.

## Behavior

Cover first setup; keyring locked then pristine re-probe; pristine create only when the exact
keyring and action-bound presence cells pass; usable keyring plus each nonpassing presence state
with zero durable artifacts; committed `os_keyring` locked then load-only retry and ready-local
without presence; correlated stage/entry/sentinel/mode verification; two-entry/one-send passphrase
initialization from proven-pristine local state with a fresh installation ID when keyring is
available-absent or locked/unavailable; queryable correlated-entry and any prior local identity/
catalog/artifact refuse initialization; committed passphrase startup performs zero keyring calls;
distinct unlock ceremony; correct-secret startup failure after committed
initialization; 15-minute true idle, monitor loss, wake stays locked and fresh ready composition.
The injected daemon activation observes the exact current service/vault generations only after the
vault is ready. A correct passphrase followed by activation/startup-gate failure returns bounded
`unlock_failed`, resets the successful passphrase throttle, relocks the vault, and leaves lifecycle
state locked without publishing a ready result.
Clean-crash continuation adopts an exact provisional initial throttle record without changing its
bytes or prior writer binding; any nonprovisional record blocks re-initialization.

## Errors and edge cases

Wrong/tamper/rate limit, crash at every keyring stage/entry/publication/fsync boundary, safe exact
pre-entry-stage cleanup, exact entry+stage resume, orphan/mismatched/multiple ambiguous keyring
artifacts with no delete/replace, crash before/at/after passphrase mode publication, partial/
ciphertext/keyring conflict, lock during provider/object/commit/unlock, and drain deadline
termination.
Also cover durable attempt reservation before KDF, crash-with-in-progress charging, exact 0/30/60/
120/240/300-second table, restart full-delay monotonic re-arm, wall-clock rollback/future evidence,
missing/corrupt throttle record maximum-delay recovery, and success reset before ready publication.

## Invariants

1. Locked has no ready-only handles/application.
2. No automatic fallback/unlock.
3. Initialization is first-install-only and never resets an existing vault.
4. Keyring retry never guesses between creation and load and never replaces an existing entry.
5. Missing pristine presence evidence blocks keyring-mode staging and never blocks explicit
   passphrase setup from proven-pristine local state; unknown keyring absence is recorded, not
   claimed. Missing current presence on an existing keyring vault fences external authority only.

## Tests

This file is the executable owner.

## Open questions

None.
