# src/yoetz/application/observation_check_policy.py — exact-byte check authority

**Wave:** D | **ADRs:** ADR-009, ADR-010 | **Imports (spec-tree):**
`specs/src/yoetz/adapters/approved_checks.py.md` |
**Imported by:** setup, observe CLI, and observation verification

## Purpose

Parse the fixed project policy and bind every approved command to the exact raw repository bytes
that a local human reviewed.

## Public surface

`CHECK_POLICY_FORMAT`, `CHECK_POLICY_PATH`, `ObservationCheckPolicy`, `raw_policy_digest`,
`parse_observation_check_policy`, and descriptor-safe `load_observation_check_policy`.

## Behavior

Strict TOML permits only format and nonempty sorted checks with exact ID/argv/timeout/network.
Approval commitments bind ID, argv, and network. Loading opens root, `.yoetz`, and file with
no-follow descriptors and bounded reads.

## Errors and edge cases

Unknown keys/types, duplicates, unsafe argv, oversize/empty data, symlinked components, or file
replacement during bounded read fail `invalid_approved_check_policy`.

## Invariants

1. Raw SHA-256, not parsed equivalence, is the trust identity.
2. Parsing grants no execution authority.
3. No shell command string or environment value exists in the schema.

## Tests

`tests/unit/application/test_observation_check_policy.py`.

## Open questions

None.
