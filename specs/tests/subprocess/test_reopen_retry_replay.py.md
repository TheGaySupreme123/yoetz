# tests/subprocess/test_reopen_retry_replay.py — cross-process idempotent recovery oracle

**Wave:** C–F | **ADRs:** ADR-001, ADR-002, ADR-003, ADR-006 | **Imports (spec-tree):** ledger/
recovery/projection/application specs and child helper | **Imported by:** subprocess and release gates

## Purpose

Test the common recovery assertion shared by crash cases as a standalone end-to-end matrix: close or
lose a process, reopen installed product, retry, and replay without relying on in-memory state.

## Public surface

Cases cover clean close, abrupt pre/post-commit exit, response loss, pending check lease reclaim,
completed/quarantined operation, repeated same request, conflicting digest, corrupted projection,
orphan staged object, checkpointed/uncheckpointed WAL, restored/migrated bundle, and interruption of
the `import`/`review` support workflow while consuming a truncated source stream.

## Behavior

Create baseline and persist the expected canonical reference snapshot outside the child. Stop first
process at selected state, discard all Python objects, start a fresh installed process with same
catalog/bundle and allowed key source, then retry exact operation identity. Assert terminal result
bytes/digest match or conflict/pending/quarantine is stable; no new IDs/events/provider steering are
created for an existing request.

Query status, load raw canonical events through test inspection, discard projection tables, replay
with several page sizes/hash seeds, and compare frontiers, projections, findings, responses,
coverage, receipt, object inventory, writer/ledger chains to the independent reference.

For import recovery, split a reviewed truncated Codex JSONL fixture at every durable source-retention
and review-publication boundary. Kill the first installed process, reopen with no in-memory parser
state, retry the same import/review identity, and assert exact retained source digest, byte offsets,
quarantine/gap counts, publication frontier, and result identity without duplicate observations.

## Errors and edge cases

- Missing key/corrupt canonical event fails closed; test never repairs to manufacture equality.
- Projection-only corruption is rebuildable and cannot alter receipt truth.
- Orphan cleanup observes pin/age policy and cannot delete referenced ciphertext.
- A truncated import cannot be silently resumed from a guessed byte boundary or publish twice.
- Reopen uses no source checkout/network/ambient process state.

## Invariants

1. Durable idempotency survives process lifetime and response loss.
2. Replay from canonical bytes equals incremental state.
3. Recovery never replaces stable IDs or selected semantic attempts.
4. Corruption weakens/blocks; it never fabricates success.
5. Import/review recovery preserves exact source position and never duplicates publication.

## Tests

Run the matrix after representative fault cases and independently in PR bounded mode. Evidence keeps
only structural digests/reason codes and is public-boundary scanned.

## Open questions

None.
