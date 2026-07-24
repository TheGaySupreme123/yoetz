# tests/unit/service/test_default_agent_context_allowlist.py — default agent-context disclosure suite

**Wave:** C | **ADRs:** ADR-009 | **Imports (spec-tree):** `src/yoetz/service/ready_composition.md`,
`domain/privacy.md`, `adapters/privacy/local_enforcer.md` | **Imported by:** test runner

## Purpose

Freeze both halves of the default agent-context disclosure boundary: verification output the
requesting agent must be able to read, and everything adjacent to it that must stay blocked. The
narrow original default blocked the agent from reading its own completion receipt, which defeated
completion gating; widening it is only safe if the widening is bounded by a test.

## Public surface

Assertions over the seeded default LOCAL_ONLY policy's `agent_context_categories` and
`agent_context_data_classes`.

## Behavior

The default policy admits the verification projection content categories — finding summaries,
obligation text, and bounded structural metadata — and the ordinary-user-content data class
alongside public-structural, so receipt and check documents project to agent context without
omissions in every format.

## Errors and edge cases

Observation-derived and transcript categories are asserted absent. A future category added to the
default allowlist without extending this test is the failure this suite exists to prevent.

## Invariants

1. The default allowlist covers Yoetz-authored verification output for the requesting agent's own
   task, and nothing wider.
2. Widening the allowlist is an ADR-009 decision, not an implementation detail.

## Tests

This file is the executable owner. Upgrade of an already-seeded narrower policy is owned by
`tests/integration/service/test_ready_composition.py`.

## Open questions

None.
