# tests/unit/service/test_default_agent_context_allowlist.py — default agent-context disclosure suite

**Wave:** C | **ADRs:** ADR-009 | **Imports (spec-tree):** `src/yoetz/service/ready_composition.md`,
`domain/privacy.md`, `adapters/privacy/local_enforcer.md`, `adapters/privacy/catalog.md` |
**Imported by:** test runner

## Purpose

Freeze both halves of the default agent-context disclosure boundary: verification output the
requesting agent must be able to read, and everything adjacent to it that must stay blocked. The
narrow original default blocked the agent from reading its own completion receipt, which defeated
completion gating; widening it is only safe if the widening is bounded by a test.

## Public surface

Assertions over the seeded default LOCAL_ONLY policy's `agent_context_categories` and
`agent_context_data_classes`, and over the re-seed decision that carries an already-installed
narrower default forward.

## Behavior

The default policy admits the verification projection content categories — finding summaries,
obligation text, and bounded structural metadata — and the ordinary-user-content data class
alongside public-structural, so receipt and check documents project to agent context without
omissions in every format.

The re-seed cases cover the decision at both levels. Against a recording stub: an exact untouched
old default is carried forward with a bumped version and a distinct `policy_digest`, an edited
policy is never re-seeded, and a policy already at the current default causes no write. Against a
real catalog store: a row written by the first-run seed is swapped, and an owner tightening whose
contents equal the old default exactly is left alone because its `change_kind` is `tightening`.

## Errors and edge cases

Observation-derived and transcript categories are asserted absent. A future category added to the
default allowlist without extending this test is the failure this suite exists to prevent. The
owner-tightening case is the security edge: policy contents cannot prove policy origin, so a test
that only compares fields would pass while the implementation silently widened an owner's choice.

## Invariants

1. The default allowlist covers Yoetz-authored verification output for the requesting agent's own
   task, and nothing wider.
2. Widening the allowlist is an ADR-009 decision, not an implementation detail.
3. Re-seeding requires first-run seed provenance and exact contents together, and mints a new
   policy digest rather than reusing the superseded one.

## Tests

This file is the executable owner. End-to-end upgrade behavior through a built coordinator is
owned by `tests/integration/service/test_ready_composition.py`.

## Open questions

None.
