# tests/unit/application/test_unit_of_work.py — bounded commit and ambiguity discipline

**Wave:** D | **ADRs:** ADR-001, ADR-002, ADR-003, ADR-004 | **Imports (spec-tree):**
`src/yoetz/application/unit_of_work.md`, `src/yoetz/ports/ledger.md`,
`src/yoetz/ports/start_catalog.md`, `src/yoetz/protocol/errors.md`
**Imported by:** the application unit suite

## Purpose

Lock the application-internal seam that submits one already prepared durable mutation, shields only
the bounded port commit, and resolves ambiguity from authoritative durable state.

## Public surface

- `test_prepared_mutation_is_immutable_and_mirrors_the_exact_command`
- `test_port_owns_commit_and_rollback_ordering_without_helper_retry`
- cancellation-before/during-commit tests
- same-prepared retry and ownership/generation/frontier fence tests
- ledger and start ambiguity-resolution tests
- `test_unit_of_work_has_no_concrete_adapter_or_transaction_dependency`

## Behavior

The suite uses structural port doubles, never a concrete adapter. It proves one port submission per
helper invocation; adapter-owned commit/rollback ordering; definite in-flight completion before
outer cancellation is re-raised; no submission when cancellation is already pending; exact same-ID
retry delegation; and unchanged propagation of ownership, generation, and frontier failures.

It also covers absent, complete, pending, quarantined, digest-conflict, and unreadable-storage
observations. Start resolution goes through catalog `reserve_or_resume`, not a task-ledger lookup.

## Errors and edge cases

- A commit failure is propagated after the adapter's rollback and is never retried locally.
- A storage verification failure becomes `unknown`; a non-storage public failure still raises.
- A digest mismatch raises the registered nonretryable `IDEMPOTENCY_CONFLICT`.
- Mismatched prepared identity/frontier/ref values fail construction.

## Invariants

1. Cancellation does not cancel a submitted port commit.
2. The helper neither caches results nor selects or constructs an adapter.
3. Ports remain the sole owners of rollback, durable idempotency, and fences.
4. Resolution never equates unreadable storage with absence.

## Tests

- `tests/unit/application/test_unit_of_work.py`

## Open questions

None.
