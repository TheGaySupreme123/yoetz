# src/yoetz/protocol/coverage.py — coverage lattice and weakest-coverage helpers

**Wave:** A/B | **ADRs:** ADR-002, ADR-004, ADR-006 | **Imports (spec-tree):**
`protocol/errors.md`
**Imported by:** `protocol/models.md`, `domain/events.md`, `domain/findings.md`,
`domain/receipts.md`, `domain/values.md`, `kernel/ranking.md`, `kernel/receipt_builder.md`, checks,
and renderers

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
| `AUTHORSHIP_ASSURANCE_ORDER`, `ARTIFACT_OBSERVATION_ORDER`, `EVIDENCE_IMMUTABILITY_ORDER`, `LEDGER_FRESHNESS_ORDER` | exact immutable weakest-to-strongest rank maps |
| `COVERAGE_DEFAULTS_BY_CHANNEL` | exact immutable six-channel baseline table below |
| `weakest(a, b)` | component-wise weakest-coverage merge |
| `coverage_for_channel(channel)` | conservative default coverage for a channel |

## Behavior

`Coverage` is a frozen value object. Its dimensions are deliberately asymmetric:

- some are ordered lattices with a weakest-to-strongest progression;
- two are canonical sorted-unique sets because multiple channels/check kinds may contribute;
- `known_gaps` is a canonical sorted-unique tuple of bounded machine-readable gap tokens.

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

Its stored field order is exactly `publication_channels`, `authorship_assurance`,
`artifact_observation`, `evidence_immutability`, `ledger_freshness`, `check_types`, `known_gaps`.
The three tuple fields must have exact built-in `tuple` type; enum fields and tuple members must
have the exact corresponding enum type, and gap tokens must be exact built-in `str`. A subclass or
an object whose `__class__` merely impersonates one of those types is invalid, so the frozen value
cannot retain attacker-controlled representation, comparison, iteration, or encoding behavior.

The four `*_ORDER` mappings assign zero-based ordinals in exactly the weakest-to-strongest orders
above. Ordered enum values compare only with a member of their own enum; a cross-enum comparison
returns `NotImplemented`. No enum's wire string or declaration order is used as its strength.

`Coverage.__post_init__` validates, but never silently sorts, caller-constructed values. Validation
runs in the stored field order above and stops at the first failure: publication channels;
authorship assurance; artifact observation; evidence immutability; ledger freshness; check types;
known gaps. Within each tuple field it checks exact outer type, then emptiness/size bounds, then
exact member/token type and grammar, then duplicate/order rules:

- `publication_channels` is nonempty, contains `PublicationChannel` members, and is strictly
  ascending by unsigned ASCII bytes of `.value`;
- `check_types` is nonempty and has exactly one of the four shapes frozen by the Wave A schema:
  `(none)`, `(deterministic)`, `(semantic_model_derived)`, or
  `(deterministic, semantic_model_derived)`;
- `known_gaps` has at most 64 members; each member is an ASCII lower-snake machine token matching
  `^[a-z][a-z0-9_]{0,127}$`; and the tuple is strictly unsigned-ASCII sorted and duplicate-free;
- ordered fields are members of their exact enum.

The first failing rule in that total order raises the closed reasons `empty_publication_channels`,
`invalid_publication_channels`, `empty_check_types`, `invalid_check_types`,
`invalid_known_gap`, or `invalid_coverage_value`. Set order/duplicate failures reuse the canonical
owners `unsorted_set_field` and `duplicate_set_member`.

`weakest(a, b)` returns a new `Coverage` whose ordered dimensions are the weaker of the two inputs
and whose channel/check-kind/gap fields are sorted unions. An individual accepted event has one
singular publication channel; `coverage_for_channel` places it in a singleton tuple. It does not
average, score, or collapse coverage into a single scalar.

`coverage_for_channel(channel)` accepts only an exact `PublicationChannel` enum member and returns
the corresponding immutable row of `COVERAGE_DEFAULTS_BY_CHANNEL` below. Any other runtime type,
including a raw token or spoofed `__class__`, raises `invalid_coverage_value`. Every row lists all
seven
fields; `channels` is the singleton tuple containing the table key and `checks` is `(none,)`.

| Channel | Authorship | Artifact observation | Evidence immutability | Freshness | Checks | Known gaps |
|---|---|---|---|---|---|---|
| `cooperative_mcp` | `self_asserted` | `published_only` | `metadata_only` | `current` | `none` | empty |
| `local_cli` | `self_asserted` | `published_only` | `metadata_only` | `current` | `none` | empty |
| `codex_jsonl_import` | `self_asserted` | `import_observed` | `metadata_only` | `partial` | `none` | `import_source_range_not_universal` |
| `hook_observed` | `harness_observed` | `hook_observed` | `metadata_only` | `current` | `none` | empty |
| `engine_derived` | `service_authenticated` | `published_only` | `metadata_only` | `current` | `none` | empty |
| `human_import` | `self_asserted` | `import_observed` | `metadata_only` | `partial` | `none` | `human_import_scope_not_universal` |

These are channel-only baselines, not final event coverage. In particular, an authenticated local
transport does not authenticate the asserted author, and an engine-authored event is not by itself
proof that an artifact was captured or a check ran. A caller may strengthen one dimension only from
a separately validated fact (for example an immutable captured object or a completed deterministic
check), and may weaken any dimension or add gaps as later facts require. `coverage_for_channel`
itself never examines such facts.

`weakest(a, b)` selects the lower registered ordinal for each ordered dimension. It computes ASCII-
sorted set union for channels and gaps. For check types it unions the two inputs and removes `none`
iff either real check type is present, then emits the one canonical tuple spelling. Thus the helper
constructs canonical output even though the public constructor rejects noncanonical input. Before
constructing the result it checks the gap union size. If two individually valid inputs have more
than 64 distinct `known_gaps` in union, it raises
`ProtocolValueError("invalid_known_gap")`; it never truncates, selects a subset, or replaces exact
gaps with a summary token. This makes the bounded wire value fail closed while preserving every gap
on every successful merge. The overflow check is symmetric, so argument order cannot change the
outcome.

`weakest` requires exact `Coverage` inputs, checked left then right before reading either value's
fields; any other runtime type raises `invalid_coverage_value`. This is a protocol-value helper,
not a duck-typed extension point.

## Errors and edge cases

- Unknown enum names are invalid at the boundary and become
  `ProtocolValueError("invalid_coverage_value")`.
- Coverage never pretends that an imported observation is independently reproduced.
- `known_gaps` is bounded ASCII machine data and is deduplicated in canonical order by merge;
  direct construction with duplicates or wrong order fails, and a merge whose distinct union would
  exceed 64 fails with `invalid_known_gap` rather than losing evidence.
- No helper here may infer cryptographic attestation unless the source actually proves it.

## Invariants

1. Coverage only weakens when evidence becomes less direct, less fresh, or less immutable.
2. A receipt or finding may report one weakest material coverage, but never a stronger one than its
   supporting facts justify.
3. Coverage is evidence, not a quality score.
4. `known_gaps` is preserved through replay and rendering on every successful merge; an
   unrepresentable union fails closed.
5. Merging two unlike channels or check kinds is commutative and lossless; it never picks one
   arbitrary unordered singleton. Gap-union overflow is also commutative and never truncates.
6. Channel defaults encode only facts guaranteed by that channel and never authenticate caller
   assertions by transport alone.

## Tests

- `tests/unit/protocol/test_coverage.py` — ordering relations and union semantics.
- `tests/conformance/honesty/test_coverage_weakening.py` — weakest-coverage propagation through
  public conclusions.
- `tests/conformance/honesty/test_receipt_wording.py` — receipt wording stays no stronger than
  coverage.

## Open questions

None.
