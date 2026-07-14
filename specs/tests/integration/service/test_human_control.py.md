# tests/integration/service/test_human_control.py — multi-phase confidential ceremony integration

**Wave:** C/E | **ADRs:** ADR-008, ADR-009 | **Imports (spec-tree):** human-control/privacy policy/audit specs | **Imported by:** test runner

## Purpose

Prove the third same-UID YZH1 endpoint is the sole challenge creator for every human ceremony and
that decisions/credential writes consume exact internal reauth proof atomically.

## Public surface

All eight ceremony kinds, closed open/preview/next-phase/action/result frames, YZS1 binding handoff,
keyring zero-secret retry, credential set/rotate, and generation/race matrix.

## Behavior

Assert ordinary/MCP endpoint isolation, exact target previews, initialize/unlock/recovery bindings,
zero-secret pristine keyring/presence create/no-write and existing-load retry branches, explicit
new passphrase ceremony after `human_authority_unavailable`, provider reauth then credential,
atomic rotate/old-record preservation,
post-store provider reconciliation, privacy exact-digest decision, and no proof export. Run every
approval ceremony with matching strong user presence, unavailable/cancelled/wrong-binding
presence, and explicit purpose-specific secret reauthentication. Prove YZH1 TTY acknowledgement and
same-UID peer identity alone never mint proof.

## Errors and edge cases

Stale/changed/expired/relock/disconnect/replay/concurrent ceremony, wrong endpoint/purpose/phase,
zero YZS1 secret, ambiguous/non-pristine keyring retry with no mutation, set-existing/rotate-missing,
and policy/profile mismatch.
Also reject retry-to-passphrase phase crossover and prove existing-keyring load without presence is
ready-local/external-fenced rather than setup-reset.

## Invariants

1. No reusable authorization leaves service.
2. MCP/ordinary decision methods are absent.
3. Helpers cannot invent bindings; every YZS1 binding belongs to one live YZH1 ceremony.
4. Presence and secret reauthentication converge only at a bound one-use internal proof; neither
   can authorize another target.

## Tests

This file is the executable owner.

## Open questions

None.
