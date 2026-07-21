# tests/unit/service/test_elevated_bootstrap.py — elevated-bootstrap pending consent vectors

**Wave:** D | **ADRs:** ADR-015 | **Imports (spec-tree):**
`src/yoetz/service/elevated_bootstrap.md`, `src/yoetz/config/paths.md`,
`src/yoetz/protocol/canonical.md` | **Imported by:** unit suite

## Purpose

Freeze the service-side ADR-015 pending-consent contract: operation scope, owner-only pending state,
danger digest, fifteen-minute TTL, exact approval checks, status projection for agents, inherited-FD
secret ingress primitive, pending cleanup, and audit JSONL without secrets.

## Public surface

Table tests for `prepare_pending`, `load_pending`, `approve_pending`, `clear_pending`,
`projection_for_status`, `status_payload`, `read_secret_fd`, and `ElevatedBootstrapError.reason`.
Filesystem tests use an isolated state directory and injected time/secrets where needed; FD tests
use explicit inherited pipe descriptors and never stdin/stdout/stderr.

## Behavior

Assert that `prepare_pending` accepts only `vault_initialize` and `provider_credential_set`, rejects
wrong provider-binding presence, writes one active pending record with schema, random pending ID,
operation-specific danger text, `danger_digest`, random confirmation phrase, creation/expiry times,
target digest, and optional nonsecret provider binding, and sets expiry exactly fifteen minutes
after creation. The record and audit event must contain no passphrase, reauthentication secret,
provider credential, proof, bearer token, local socket path, FD content, or entered length.

Assert canonical danger-digest vectors over the exact pending fields and provider binding when
present. `load_pending` must return a live pending record only when the digest matches, clear and
audit expired records, and raise bounded errors for corrupt shape, invalid operation, malformed
provider binding, or tampering. An active unexpired pending record blocks another prepare; an
expired one is cleared before a later prepare proceeds.

Assert `approve_pending` checks pending ID, danger digest, and confirmation phrase exactly and in
the live pending state. Mismatches and absent/expired pending records fail with bounded reasons and
do not read any secret. Accepted approval records only structural audit fields and returns the
pending record for the CLI operation driver. Tests for the caller contract prove pending state is
cleared after accepted approve success and after accepted approve followed by operation failure.

Assert `projection_for_status(None)` reports `required:false`, `not_prepared`, empty structural
identifiers, and forbidden channels. Assert a live pending projection includes `danger_text`,
`danger_digest`, `confirmation_phrase`, expiry, operation, pending ID, approve-command template
with FD placeholder `3` for vault initialization or `3` and `4` for provider credential set, and
the forbidden channels `mcp`, `argv`, `env`, `stdin`, `config`, and `transcript`, without any secret
values, tokens, paths, or proofs.

Assert `read_secret_fd` rejects descriptors `0`, `1`, `2`, negative and non-integer descriptors,
nonpositive maximums, empty input, oversized input, and read failures. For valid inherited
descriptors it reads bounded bytes until EOF, strips a single trailing LF or CR delimiter when
present, returns a mutable `bytearray`, and overwrites intermediate storage on each rejection path.

## Errors and edge cases

Cover duplicate prepare, exact expiry boundary, corrupted JSON, non-object JSON, digest mismatch,
malformed provider binding, invalid operations in stored state, clear failure, pending ID mismatch,
danger digest mismatch, phrase case/spacing mismatch, zero-length secret after delimiter stripping,
maximum-plus-one reads, descriptor close during read, and audit append creation with owner-only
mode. The tests must not require a real user home, real platform state, real vault, real service
daemon, or real provider credential.

## Invariants

1. The only operations under test are `vault_initialize` and `provider_credential_set`.
2. Pending consent and audit JSONL are structural and contain no secret material.
3. Approval is exact pending ID plus exact danger digest plus exact phrase.
4. Secret reading is impossible through stdin/stdout/stderr and is bounded to inherited FDs.
5. Agent projection includes the danger text, approve-command template, and forbidden channels.
6. Expiry and post-approval completion/failure consume pending state.
7. Same-UID file honesty limits are documented by behavior tests but not treated as cryptographic
   isolation.

## Tests

This file is the executable owner for the service elevated-bootstrap contract and uses only
isolated filesystem, clock, randomness, and pipe fixtures.

## Open questions

None.
