# tests/unit/observability/test_privacy.py — privacy helpers and session-hash separation

**Wave:** F | **ADRs:** ADR-004, ADR-005, ADR-007, ADR-009 | **Imports (spec-tree):**
`src/yoetz/observability/privacy.md`, `src/yoetz/ports/keys.md`
**Imported by:** the observability unit suite

## Purpose

Lock the privacy helpers so canaries, hashes, and redaction checks behave deterministically without
revealing user content.

## Public surface

- `test_session_id_hash_is_separate_from_plain_id` — hashes do not equal or reveal raw session IDs.
- `test_redaction_helpers_strip_sensitive_text` — sensitive strings are removed or replaced.
- `test_canary_checks_are_detectable` — synthetic canary markers remain testable.
- `test_privacy_helpers_are_deterministic` — same input produces same redaction/hash output.
- `test_mac_helpers_require_exact_purpose_and_domain` — raw keys and cross-purpose handles are
  rejected.
- `test_request_commitment_covers_final_body_only` — deterministic body changes affect the
  commitment while HTTP/TLS framing and credential-auth metadata are outside its input contract.

## Behavior

The suite proves:

- session hashes are one-way and stable within one installation through the opaque
  `log_correlation` handle;
- request commitments use only the opaque `privacy_audit` handle over the exact final
  provider/application request body;
- known-answer vectors use byte domains ending in the literal `\x00` delimiter for both session
  correlation and privacy request-body commitments; omitting/re-encoding it changes the vector;
- redaction helpers preserve structure while removing sensitive content;
- canary patterns remain detectable for test assertions;
- privacy helpers do not depend on ambient process state.

## Errors and edge cases

- A privacy helper that echoes the input fails.
- A hash that is reversible by construction fails.
- A helper signature accepting raw key bytes, a stale/wrong-purpose/domain handle, credential
  metadata, or an ambiguous full-wire object fails.

## Invariants

1. Privacy helpers are deterministic.
2. Sensitive content is removed, not relabeled.
3. Plain IDs and hashed IDs remain distinct.
4. Log, privacy-audit, lookup, and bundle MAC purposes cannot substitute for one another.

## Tests

- `tests/unit/observability/test_privacy.py`

## Open questions

None.
