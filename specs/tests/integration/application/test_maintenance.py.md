# tests/integration/application/test_maintenance.py — maintenance consent orchestration

**Wave:** D | **ADRs:** ADR-001, ADR-003, ADR-004, ADR-007 | **Imports (spec-tree):**
`src/yoetz/application/maintenance.py.md`, `src/yoetz/ports/maintenance.py.md`,
`src/yoetz/ports/diagnostics.md` | **Imported by:** integration application tests

## Purpose

Prove the maintenance application service preserves preview/confirmation identity, obtains portable
recovery material only through the typed post-confirmation seam, and exposes only bounded structural
plans, results, errors, and diagnostics.

## Public surface

- `test_preview_and_confirmation_digest_binding_precedes_every_effect`.
- `test_backup_dispatches_machine_bound_without_secret_and_portable_after_confirmation`.
- `test_restore_uses_restore_bound_secret_and_exact_original_command`.
- `test_migration_dispatches_confirmed_digest_without_secret_acquisition`.
- `test_frontier_and_service_generation_bindings_fail_closed`.
- `test_replay_errors_and_cancellation_remain_bounded_and_retryable`.
- `test_results_diagnostics_repr_and_close_exclude_locations_and_secrets`.

## Behavior

The suite composes the real application boundary values with scripted `MaintenancePort`, clock,
recovery-secret acquirer, and maintenance-diagnostic sink implementations. It asserts the exact
sequence preview -> accepted equal digest -> optional typed secret -> execution; exact original
command/frontier propagation; positive daemon-generation binding; all three operation dispatches;
and same-request structural replay.

## Errors and edge cases

- Decline and stale digest perform no secret acquisition or mutation.
- Changed frontier/mode/generation facts fail closed before execution.
- Target-equals-current migration is rejected through the port's unsupported-migration outcome;
  no no-op result or second migration call is fabricated.
- Typed port failures map to bounded public codes and cancellation is re-raised.
- Repeated close is safe and later admission fails without touching a port.

## Invariants

1. Machine-bound execution never calls the recovery-secret acquirer.
2. Portable backup uses `create`; portable restore uses `restore`.
3. No path or secret canary enters repr, result, error, or diagnostic output.
4. Maintenance diagnostics remain separate from startup capability evidence.

## Tests

- `tests/integration/application/test_maintenance.py`

## Open questions

None.
