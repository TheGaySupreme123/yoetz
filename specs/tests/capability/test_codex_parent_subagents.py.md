# tests/capability/test_codex_parent_subagents.py — parent/subagent attribution and integration

**Wave:** D/F | **ADRs:** ADR-001, ADR-005, ADR-006 | **Imports (spec-tree):** capability evidence,
skill/ledger/check specs | **Imported by:** multi-agent capability claim

## Purpose

Observe whether real Codex versions can coordinate one parent and two subagents through one
authoritative Yoetz service while retaining distinct logical writer/actor attribution and surfacing
contradiction/evidence to the parent.

## Public surface

One synthetic task matrix covers supported Codex version/platform, subagent context propagation mode,
concurrent publication order, one evidence-backed result, one contradictory claim, parent integration,
response, recheck, and receipt.

## Behavior

Parent starts and publishes plan plus two assignments. Spawn two real Codex subagents through the
supported public mechanism. Each attaches/publishes with a distinct writer and caller-observed actor
assurance; neither inherits verified identity from prompt labels. Synchronize publication race, then
parent status/check must show both contributions and prioritized contradiction.

Parent publishes integration decision/action/result, responds to finding, checks again, and creates
receipt. Compare full durable writer chains, parents/refs, assertions, findings, coverage and receipt
to expected public fixture. If MCP context cannot reach subagents, record exact limitation and fail/
narrow multi-agent claim rather than simulating it.

## Errors and edge cases

- No private hidden reasoning or inter-agent internal messages are treated as evidence.
- Duplicate writer chain, author assurance upgrade, missing contribution, or unreported contradiction
  fails.
- Scheduling order may vary; canonical event semantics/reference oracle is order-tolerant where the
  contract permits.
- All processes/config/data are isolated and bounded.

## Invariants

1. Parent/subagents use one service owner but distinct logical writers.
2. Caller labels never upgrade identity assurance.
3. Contradictory material work is visible before receipt.
4. Claim is scoped to exact observed Codex mechanism/version.

## Tests

Evidence records writer counts/chain digests, public subagent mechanism, contradiction/finding IDs,
receipt conclusion, and private transcript digest.

## Open questions

None.
