# tests/capability/test_codex_optional_required_failure.py — truthful MCP availability behavior

**Wave:** D/F | **ADRs:** ADR-005, ADR-007 | **Imports (spec-tree):** capability evidence, Codex skill
and MCP config specs | **Imported by:** integration/degradation support claim

## Purpose

Distinguish optional server degradation from required server failure in real Codex and ensure neither
path fabricates live ledger state, verification, findings, or receipts.

## Public surface

Scenarios combine optional/required with missing executable, startup timeout, invalid resource,
locked key, incompatible protocol, immediate crash, and mid-session loss.

## Behavior

Configure identical synthetic sessions differing only in required flag. For optional failure,
observe Codex continues unrelated work and the skill states Yoetz unavailable/no live receipt; no
Yoetz tool result or durable event exists. For required failure, observe run initialization blocks
with bounded server-unavailable status and no task work starts.

For mid-session loss, status is last-known only when backed by prior tool result; subsequent skill
wording discloses unavailable/freshness limit. Restart/retry may reattach but cannot invent the
missed interval. Capture public transcript digest and filesystem/ledger absence/presence oracle.

## Errors and edge cases

- Codex version lacking required semantics is unsupported, not reinterpreted.
- Arbitrary Codex error text is private evidence; public record uses reason codes.
- Failure injection never uses production credentials or real user config.

## Invariants

1. Optional failure preserves Codex work but makes no Yoetz claim.
2. Required failure prevents the configured run.
3. No unavailable interval becomes verified history.
4. Behavior is empirically version-scoped.

## Tests

Each failure/cell emits evidence for continuation/blocking, tool inventory, ledger mutation count,
and bounded wording classification.

## Open questions

None.
