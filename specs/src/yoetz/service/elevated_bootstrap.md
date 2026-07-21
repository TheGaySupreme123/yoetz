# src/yoetz/service/elevated_bootstrap.py — ADR-015/016 consent registry + pending store

**Wave:** D | **ADRs:** ADR-015, ADR-016 | **Imports (spec-tree):** `config/paths.md`,
`protocol/canonical.md`, `protocol/json_types.md`, `domain/values.md` | **Imported by:**
`cli/elevated.md`, agent status projection

## Purpose

Owns the human-review consent catalog (ADR-016 risk classes) and the ADR-015 secret-ingress
bootstrap lane. It catalogues operations with an `implemented` flag, creates and validates one
ephemeral owner-only pending record for implemented ops, exposes structural status for agents,
reads approved secrets only from inherited file descriptors when required, and appends structural
audit events without secret material.

## Public surface

- `RiskClass` is exactly `default_safe | secret_ingress | secret_reauth | phrase_only | privacy_widen`.
- `ElevatedOperation` enumerates catalogued ops including bootstrap, rotate, idle-relock,
  privacy widen, backup/restore/migrate, skill install, and harness MCP register.
- `ConsentOperationSpec` / `CONSENT_OPERATIONS` / `catalog_payload()` are the agent-facing catalog.
- `PendingElevatedConsent` is the frozen pending record: schema, `pending_id`, operation,
  `risk_class`, `danger_text`, `danger_digest`, `confirmation_phrase`, creation/expiry Unix times,
  `target_digest`, optional nonsecret provider binding, and `secret_fds`.
- `prepare_pending`, `load_pending`, `clear_pending`, and `approve_pending` own pending lifecycle.
- `projection_for_status` / `status_payload` return the agent-safe structural view (includes catalog).
- `read_secret_fd(fd, maximum)` reads one bounded secret from an inherited descriptor.
- `ElevatedBootstrapError.reason` is the stable bounded failure token.

## Behavior

`prepare_pending` accepts only catalog ops with `implemented=true`. v0.1 implemented secret-ingress
ops are exactly `vault_initialize`, `provider_credential_set`, and `provider_credential_rotate`.
Catalogued phrase-only / privacy-widen / idle-relock rows remain `implemented=false` until owning
CLIs consume a durable digest-bound grant; prepare raises `operation_not_implemented`.

Provider binding is required exactly for credential set/rotate and must contain the closed key set
`provider_id`, `model_id`, `endpoint_profile_id`, `endpoint_profile_version`, `purpose`,
`scope_digest`, `purpose_digest` with valid `sha256:` digests for the digest fields.
`target_digest` must be a valid `sha256:` digest (`validate_sha256_digest`).

When no unexpired pending record exists, prepare creates an owner-only pending file under
`state_dir()/elevated-bootstrap/` with a random `pending_id`, catalog danger text, `danger_digest`,
random exact confirmation phrase (`YOETZ APPROVE` + hex), `target_digest`, and a fifteen-minute TTL.
The pending payload contains no passphrase, reauthentication secret, provider credential, bearer
token, proof, socket path, or file descriptor value. Active pending consent is singleton.

The `danger_digest` is a domain-separated canonical digest over schema tag, danger text, phrase,
operation, risk class, pending ID, target digest, expiry, secret FD names, and provider binding
when present. `load_pending` requires schema `yoetz.elevated-bootstrap.pending/1`, validates
operation and binding shape, rebinds `risk_class` / `danger_text` / `secret_fds` to the live catalog
(mismatch → `pending_tampered`), recomputes the digest, clears and audits expired records, and fails
closed on corrupt or tampered state.

`approve_pending` does not read secrets and does not complete the operation. It verifies exact
`pending_id`, exact `danger_digest`, and byte-for-byte exact confirmation phrase (constant-time
equal-length compares), refuses unimplemented ops, appends a structural audit event, **clears the
pending file immediately (single-shot)**, and returns the pending record to the CLI approve driver.
A second approve of the same challenge fails with `pending_absent`.

`read_secret_fd` accepts only inherited descriptors other than `0`, `1`, and `2`. It reads until EOF
or the configured maximum plus one byte, rejects empty and oversized input, strips one trailing LF or
CR delimiter when present, returns a mutable `bytearray`, and overwrites intermediate storage on
failure.

`projection_for_status` is the only agent-facing pending view. With pending state it reports
`danger_text`, `danger_digest`, `confirmation_phrase` (for human display), expiry, operation,
pending ID, `approve_command` with **`<confirmation_phrase>` placeholder** (never the live phrase
pre-filled), FD placeholders starting at `3`, and `forbidden_channels`. Agents must substitute the
phrase the human typed; they must not auto-fill from the projection.

`catalog_payload` returns schema `yoetz.consent.catalog/1` with `default_safe` MCP/privacy-tighten
ops, catalog rules (`no_standing_yolo`, path-safety not waivable, prefer TTY, one pending), and each
operation's risk class, `implemented` flag, FD requirements, and prepare hint
(`yoetz consent prepare …`).

Audit JSONL is owner-only append state without secret material.

## Errors and edge cases

Invalid/unimplemented operation, wrong provider-binding presence/shape, invalid digests, active
pending consent, corrupt JSON/schema, digest or catalog rebind mismatch, expiry, missing pending,
pending ID / danger-digest / confirmation mismatch, unsafe or reserved FD numbers, empty/oversized
secret, and read/clear failures all raise `ElevatedBootstrapError` with bounded reason strings.
Same-UID attackers can still race local files; this module records human intent and enforces the
orchestration boundary, not cryptographic exclusion of malicious same-UID code.

## Invariants

1. Prepare/approve succeed only for `implemented=true` catalog ops; phrase-only rows stay false until
   durable grant consumption exists at owning mutation boundaries.
2. Pending consent contains danger text, digest, phrase, target digest, and TTL, but no secrets.
3. Approval requires exact pending ID, exact danger digest, and exact phrase; pending is consumed
   immediately on accept (single-shot).
4. Secret bytes can enter only through inherited FDs other than `0`, `1`, and `2`.
5. `approve_command` never embeds the live confirmation phrase; agents substitute human-typed input.
6. Agent status projection is structural and never includes secrets, paths, tokens, or proofs.
7. Audit JSONL records structural consent events without secret material.

## Tests

- `tests/unit/service/test_elevated_bootstrap.py` freezes catalog implemented flags, pending
  payload/digest shape, TTL, singleton, single-shot approve, phrase placeholder, FD rejection/read
  bounds, tamper detection, and status catalog inclusion.

## Open questions

None for the implemented secret-ingress lane. Phrase-only grant store + consume at backup/restore/
migrate/skill/MCP-register boundaries remains a follow-up before flipping those `implemented` flags.
