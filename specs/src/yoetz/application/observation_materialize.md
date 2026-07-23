# src/yoetz/application/observation_materialize.py — observation → ledger mapping

**Wave:** D | **ADRs:** ADR-005, ADR-010 | **Imports (spec-tree):** `domain/observation.py.md`,
`domain/events.md`, `protocol/coverage.md` | **Imported by:** `observation_coordinator.md`

## Purpose

Conservatively map supported `ObservationEnvelope` values into service-authored ledger
`EventDraft`s with `hook_observed` (or honest weaker) coverage. Unknown/unmapped shapes remain
observation-store-only with explicit gaps and never invent success.

## Public surface

- `materialize_observation_envelope(envelope, *, task_id) -> MaterializedObservationBatch`
- `stable_observation_id(...)` — deterministic IDs from task binding, source identity, mapping
  version, and event role
- `observation_operation_digest(...)`, `observation_author()`

## Behavior (conservative mapping)

- `PreToolUse` → pending `action_recorded` when a stable tool-call identity exists
- `PostToolUse` → linked `action_recorded` + `result_recorded`; unpaired post → evidence only +
  `unpaired_event` (no invented action)
- File-change tools → edit action/result; no file-content claim without inspection
- Command tools → command action/result with exit status; command text omitted/digest-only
- Permission events → `decision_recorded` evidence (not proof the action occurred)
- Subagent start/stop → evidence-only with stable correlation
- Completion/final → `claim_recorded` with observation provenance (never automatic completion proof)
- Unknown/unsupported → skip materialization (`unsupported_or_gap`)
- Hook+stream copies share stable IDs → one materialized action/result

## Invariants

1. Never retain transcript/reasoning/command output plaintext in drafts.
2. Stable IDs make duplicate materialization idempotent across sources.
3. Gaps remain explicit; unsupported never becomes success proof.

## Tests

`tests/unit/application/test_observation_coordinator.py`

## Open questions

None.
