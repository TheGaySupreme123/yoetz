# schemas/service/service-status-1.0.0.schema.json — safe local service and vault readiness status

**Wave:** C | **ADRs:** ADR-004, ADR-008 | **Imports (spec-tree):** protocol IDs and local-service
state model | **Imported by:** hello result, service-status method, CLI/MCP/UI safe status and tests

## Purpose

Freeze the safe status visible while ready or locked without revealing process, user, vault locator,
credential, path or decrypted state.

## Public surface

- Draft 2020-12, `$id` `https://schemas.yoetz.dev/core/0.1/service/service-status/1.0.0`.
- Closed required fields: `protocol_version`, `service_version`, `service_instance_id`,
  `service_generation`, `state`, `state_reason`, `vault_mode`, `capabilities`, `session_monitor`;
  optional `idle_relock_seconds`.

## Behavior

Protocol is const `1.0`; version is bounded SemVer, instance is canonical `svc_` UUIDv4, and
generation is canonical positive decimal as in hello-result.
`state` is `starting|locked|unlocking|ready|draining|failed`. `state_reason` is `none|
keyring_locked|keyring_unavailable|human_authority_unavailable|vault_uninitialized|unlock_failed|explicit_lock|idle_relock|
user_session_locked|system_suspend|session_monitor_lost|shutdown_requested|internal_error`.
`vault_mode` is `uninitialized|os_keyring|passphrase`. `capabilities` is sorted unique from
`workflow|maintenance|import_review|external_provider|confidential_ingress|
session_event_monitor`. `session_monitor` is `active|unavailable|lost`. Native user-service-manager
installation is deferred; v0.1 runs foreground or under an explicitly external supervisor.
Optional idle relock is integer 60..86400; absence is omitted, never null.

The domain state machine validates permitted state/reason/mode/capability combinations and readiness;
schema validity never implies the vault is unlocked or a caller is authorized.
`human_authority_unavailable` has exactly two valid combinations. `locked/uninitialized` means
pristine automatic keyring setup was rejected before mutation because exact release-cell presence
evidence did not pass and is rendered as setup required. `ready/os_keyring` means an already
committed vault admitted local workflows but omitted `external_provider` for that generation. Both
expose no backend/account/policy/credential detail.

## Errors and edge cases

Unknown/null/duplicate field, invalid enum, unsorted/duplicate capability, zero/leading-zero
generation, out-of-range idle value, path, PID, username, key locator, provider credential, timestamp
or arbitrary reason text fails. Locked is a normal valid state, never mapped to failed/reset.

## Invariants

1. Status is bounded structural readiness evidence only.
2. Locked/unlocking are explicit and distinguishable.
3. Capabilities do not imply current readiness or authorization.
4. No secret, locator, host identity or timing history is exposed.

## Tests

`tests/unit/protocol/test_service_control_schemas.py`,
`tests/subprocess/test_service_lock_and_confidential_unlock.py`, and service lifecycle integration
tests.

## Open questions

None.
