# tests/capability/test_user_presence.py — exact OS-backed human-presence evidence

**Wave:** C/F | **ADRs:** ADR-008, ADR-009 | **Imports (spec-tree):**
`src/yoetz/ports/secret_memory.md`, human-control protocol, platform support manifest |
**Imported by:** capability suite and security/privacy claim gate

## Purpose

Determine which exact platform/profile cells can provide strong, action-bound OS user presence;
unsupported cells must use established passphrase reauthentication or remain fail-closed/local-only.

## Public surface

Artifact-bound capability cases for OS authenticated prompt, Yoetz/action-summary binding, fresh
interaction, one-use opaque attestation, cancel/timeout/replay, and wrong target/generation.

## Behavior

A trusted release operator runs the real prompt against a synthetic nonsecret policy/credential
action. Evidence records exact OS/adapter/profile/artifact identities and bounded outcome, never
biometric/passcode/prompt input. A pass requires exact challenge-bound one-use proof consumption;
TTY keypress, same-UID peer, unlocked screen, notification, or unbound prompt are negative controls.
The emitted `user_presence_cells` row freezes candidate-artifact digest, normalized release cell,
adapter/profile identity, all four active states, evidence digest, and required case IDs. The
keyring first-install gate accepts only an exact same-artifact row; neighboring or stale rows fail.

## Errors and edge cases

Unavailable UI/session, remote/headless execution, cancel, timeout, adapter error, wrong action,
replay, and inability to bind trusted display yield unsupported/inconclusive/fail, never inferred
support. No automation bypass approves the real prompt.

## Invariants

1. Platform name or same-UID authentication alone supports no presence claim.
2. Evidence contains no authentication secret or reusable attestation.
3. Unsupported keyring-mode cells cannot widen durable authority or activate external egress.
4. Unsupported presence prevents pristine automatic keyring creation entirely, while an already
   committed keyring vault remains eligible only for ready-local behavior.

## Tests

This file emits bounded `CapabilityEvidence`; integration tests use fakes for deterministic branches.

## Open questions

None.
