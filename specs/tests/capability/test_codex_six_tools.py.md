# tests/capability/test_codex_six_tools.py — real-Codex six-operation vertical slice

**Wave:** D/F | **ADRs:** ADR-002, ADR-005, ADR-006 | **Imports (spec-tree):** capability evidence,
installed skill/MCP/application specs | **Imported by:** Codex capability claim

## Purpose

Prove Codex discovers and correctly drives exactly six Yoetz tools against the installed candidate,
including structured results, durable effects, errors, cancellation, and idempotent response loss.

## Public surface

Cases: discovery/schema; start; atomic publish; deterministic check; respond; status; receipt;
strict validation; application/internal error; cancellation; post-commit delivery loss/retry; strict-
local network denial.

## Behavior

Use a synthetic material task with fixed plan, obligations, action/result/evidence/claim, one seeded
contradiction, and no private content. Through real interactive/exec Codex, list tools and compare
names/schemas/annotations to installed resource digests. Drive the workflow with stable request IDs.

Compare Codex-visible structured results and compact summaries with durable ledger: assigned IDs,
frontier/head, coverage, findings, response and receipt conclusions. Inject invalid field, bounded
application error, fenced unexpected exception, cancellation, and stdout loss after commit. Retry
same request proves one effect. Network monitor confirms strict-local makes no outbound connection.

## Errors and edge cases

- Hidden reasoning is never inspected or claimed; oracle is public tool/JSONL boundary plus ledger.
- Codex paraphrase alone cannot establish tool success.
- Findings remain successful results and coverage wording cannot strengthen.
- Any schema drift/extra tool/source-tree import fails the cell.

## Invariants

1. Exactly six tools map one-to-one to six operations.
2. Public result and durable state agree.
3. Retry after delivery ambiguity cannot duplicate effect.
4. Strict-local needs no provider credential/network.

## Tests

Run full slice for every advertised exact Codex/platform cell; bounded error submatrix may run once
per equivalent transport identity only when policy explicitly records equivalence.

## Open questions

None.
