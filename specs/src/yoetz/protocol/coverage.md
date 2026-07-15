# src/yoetz/protocol/coverage.py — coverage lattice and weakest-coverage helpers

**Wave:** A/B | **ADRs:** ADR-002, ADR-004, ADR-006 | **Imports (spec-tree):**
`protocol/models.md`, `domain/events.md`, `domain/findings.md`, `domain/receipts.md`,
`kernel/ranking.md`, `kernel/receipt_builder.md`
**Imported by:** checks, receipts, and renderers

## Purpose

Coverage is the honesty layer for Yoetz. It prevents the system from presenting imported,
redacted, stale, or unobserved material as if it were locally verified. This file defines the
ordered dimensions and the helper functions used to collapse many supporting facts into a single
weakest-coverage view.

## Public surface

| Name | Signature (natural language) |
|---|---|
| `Coverage` | frozen dataclass with six dimensions plus `known_gaps: tuple[str, ...]` |
| `PublicationChannel` | enum of publication channels |
| `AuthorshipAssurance` | ordered enum for authorship confidence |
| `ArtifactObservation` | ordered enum for observation strength |
| `EvidenceImmutability` | ordered enum for evidence immutability |
| `LedgerFreshness` | ordered enum for freshness |
| `CheckType` | enum of `none`, `deterministic`, `semantic_model_derived` |
| `weakest(a, b)` | component-wise weakest-coverage merge |
| `coverage_for_channel(channel)` | conservative default coverage for a channel |

## Behavior

`Coverage` is a frozen value object. Its dimensions are deliberately asymmetric:

- some are ordered lattices with a weakest-to-strongest progression;
- two are canonical sorted-unique sets because multiple channels/check kinds may contribute;
- `known_gaps` is an append-only tuple of bounded machine-readable or human-readable gap labels.

The dimensions and orderings mirror the shared registry:

- `publication_channels: tuple[PublicationChannel, ...]`: sorted-unique members drawn from
  `cooperative_mcp`, `local_cli`, `codex_jsonl_import`, `hook_observed`, `engine_derived`,
  `human_import`
- `authorship_assurance`: `self_asserted` < `harness_observed` < `locally_authenticated` <
  `service_authenticated` < `cryptographically_attested` (reserved)
- `artifact_observation`: `published_only` < `import_observed` < `hook_observed` <
  `content_captured` < `artifact_verified` < `independently_reproduced`
- `evidence_immutability`: `mutable_reference` < `metadata_only` < `content_digest` <
  `immutable_snapshot` < `independently_reproduced`
- `ledger_freshness`: `unknown` < `redacted_gap` < `partial` < `stale_after_material_change` <
  `current`
- `check_types: tuple[CheckType, ...]`: sorted-unique members drawn from `none`, `deterministic`,
  `semantic_model_derived`; `none` is valid only by itself and is removed when a real kind is
  present

`weakest(a, b)` returns a new `Coverage` whose ordered dimensions are the weaker of the two inputs
and whose channel/check-kind/gap fields are sorted unions. An individual accepted event has one
singular publication channel; `coverage_for_channel` places it in a singleton tuple. It does not
average, score, or collapse coverage into a single scalar.

`coverage_for_channel(channel)` returns a conservative baseline for a given channel. For example:

- a local CLI operation begins from `local_cli` with harness-observed authorship at best;
- a Codex JSONL import begins from `codex_jsonl_import` and `import_observed`;
- a direct engine-derived check begins from `engine_derived` but still needs post-validation before
  it can strengthen a claim.

The helper is intentionally conservative. Callers may weaken it further when they discover redaction
or stale material, but they may not strengthen it without evidence.

## Errors and edge cases

- Unknown enum names are invalid at the boundary and become `ProtocolValueError`.
- Coverage never pretends that an imported observation is independently reproduced.
- `known_gaps` is bounded and deduplicated in canonical order.
- No helper here may infer cryptographic attestation unless the source actually proves it.

## Invariants

1. Coverage only weakens when evidence becomes less direct, less fresh, or less immutable.
2. A receipt or finding may report one weakest material coverage, but never a stronger one than its
   supporting facts justify.
3. Coverage is evidence, not a quality score.
4. `known_gaps` is preserved through replay and rendering.
5. Merging two unlike channels or check kinds is commutative and lossless; it never picks one
   arbitrary unordered singleton.

## Tests

- `tests/unit/protocol/test_coverage.py` — ordering relations and union semantics.
- `tests/conformance/honesty/test_coverage_weakening.py` — weakest-coverage propagation through
  public conclusions.
- `tests/conformance/honesty/test_receipt_wording.py` — receipt wording stays no stronger than
  coverage.

## Open questions

None.
