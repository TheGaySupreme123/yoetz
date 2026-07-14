# tests/subprocess/test_process_owner_fencing.py — single-writer generation race suite

**Wave:** C/F | **ADRs:** ADR-001, ADR-003 | **Imports (spec-tree):** runtime/catalog/recovery specs,
child/fault helpers | **Imported by:** durability and release gates

## Purpose

Prove one authoritative service generation owns writes/checkpoints and that stale-owner recovery
cannot be won by PID, elapsed time alone, or two successors simultaneously.

## Public surface

Cases cover two daemons racing singleton/catalog authority, concurrent CLI/MCP clients sharing the
winner, heartbeat delay, graceful exit, SIGKILL stale owner, two-successor CAS race, PID-reuse
metadata, stale lease with current/old generation, checkpoint/migration ownership, and distinct
bundles.

## Behavior

Start an installed service and synchronize after singleton/catalog/bundle generation acquisition.
Race a second service against the same installation; it returns `service_already_running` before
vault/writer access. Concurrent CLI/MCP clients route through the winner and never open a competing
writer. Kill the owner, then release two successors simultaneously. Exactly one acquires singleton,
advances generation, and writes after unlock/recovery; the loser is fenced.

Inspect structural catalog/bundle metadata and replay ledger to prove generation monotonicity, no
dual heartbeat, no duplicate append, and old-generation lease invalidity even when unexpired. Forge
PID/start-time diagnostic matches; they never authorize mutation.

## Errors and edge cases

- Wall-clock jumps use injected clock; generation/CAS remains authority.
- A live slow owner is not stolen merely because one heartbeat sample is delayed.
- Network/shared filesystems are rejected before race tests and are not advertised.
- Process metadata/path never enters public output.

## Invariants

1. One service generation writes/checkpoints per bundle.
2. Generation increases exactly once per successful recovery race.
3. Stale generation can never regain authority.
4. Contention failure leaves canonical state unchanged.

## Tests

Run repeated synchronized daemon/client races and kill/reopen on both advertised platforms,
retaining only structural generation/result digests.

## Open questions

None.
