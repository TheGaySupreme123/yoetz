# fixtures/ — permanent protocol, replay, privacy, adversarial, import, and receipt corpus

**Wave:** A–F | **ADRs:** ADR-002 through ADR-009 | **Imports (spec-tree):** all frozen schemas,
policy/privacy/egress specs, reducer/receipt specs | **Imported by:** unit, property, integration,
conformance, subprocess, packaging, and capability suites

## Purpose

Define the reviewed examples that turn known protocol, durability, privacy, and honesty failure
modes into the clean public Yoetz contract. Fixtures are executable product stories, not test
decoration. They freeze identity bytes, replay meaning, finding behavior, coverage honesty, import
limits, and backward-read compatibility before implementation spreads across adapters.

Released fixtures are immutable compatibility obligations. Expected outputs change only through a
new versioned fixture with an explicit ADR/schema/policy amendment.

## Public surface

```text
fixtures/
  manifest.json
  canonical/
    rfc8785-applicable.case.json
    restricted-json-positive.case.json
    restricted-json-rejections.case.json
    utf16-property-order.case.json
    unicode-normalization-distinct.case.json
    publication-request-identity.case.json
    accepted-entry-identity.case.json
    identifiers.case.json
    object-envelope.case.json
  replay/
    empty.case.json
    all-event-families.case.json
    supersession-redaction.case.json
    unknown-schema.case.json
    multi-writer.case.json
    wall-clock-reversal.case.json
    projection-rebuild.case.json
    page-size-equivalence.case.json
  adversarial/
    ADV-001-abandoned-obligation.case.json
    ADV-002-omitted-failed-test.case.json
    ADV-003-stale-test-after-edit.case.json
    ADV-004-irrelevant-evidence.case.json
    ADV-005-legitimate-plan-revision.case.json
    ADV-006-parent-subagent-contradiction.case.json
    ADV-007-crash-retry-duplicate.case.json
    ADV-008-stale-redacted-ledger.case.json
    ADV-009-wrong-semantic-finding-rejected.case.json
    ADV-010-import-detects-missing-publication.case.json
    ADV-011-config-evidence-does-not-satisfy-transport.case.json
  imports/codex/
    supported-version.case.json
    unknown-events.case.json
    malformed-lines.case.json
    truncated-stream.case.json
    secret-redaction.case.json
  receipts/
    deterministic-current.case.json
    semantic-advisory.case.json
    unresolved-findings.case.json
    waiver-expiry.case.json
    redacted-gap.case.json
    imported-partial.case.json
  backward-read/
    v0.1.0-empty-bundle.case.json
    v0.1.0-full-event-bundle.case.json
  privacy/
    PRIV-001-local-only.case.json
    PRIV-002-confirm-every-request.case.json
    PRIV-003-minimal-external.case.json
    PRIV-004-trusted-provider.case.json
    PRIV-005-never-send.case.json
    PRIV-006-policy-loosening.case.json
    PRIV-007-cross-scope.case.json
    PRIV-008-independent-channels.case.json
```

Every `*.case.json` is one self-contained strict-JSON resource with the same top-level fields:
`fixture_schema`, `fixture_version`, `fixture_id`, `purpose`, `owns_requirements`,
`minimum_versions`, `controls`, `input`, and `expected`. Arbitrary JSONL or invalid-byte inputs are
stored as canonical base64 strings plus declared byte length and SHA-256; expected canonical bytes
use lowercase hexadecimal. The file's owning spec freezes its exact story, required variants, and
assertions. This keeps the repository file set finite and reviewable without hidden generators or
an unbounded directory convention.

`manifest.json` ASCII-sorts all 49 case paths and records size/SHA-256/media type. No fixture
depends on host paths, current time, random generation, network, provider availability, or Git.

### Exact future-file inventory

This index covers exactly these separately owned future files:

```text
fixtures/adversarial/ADV-001-abandoned-obligation.case.json
fixtures/adversarial/ADV-002-omitted-failed-test.case.json
fixtures/adversarial/ADV-003-stale-test-after-edit.case.json
fixtures/adversarial/ADV-004-irrelevant-evidence.case.json
fixtures/adversarial/ADV-005-legitimate-plan-revision.case.json
fixtures/adversarial/ADV-006-parent-subagent-contradiction.case.json
fixtures/adversarial/ADV-007-crash-retry-duplicate.case.json
fixtures/adversarial/ADV-008-stale-redacted-ledger.case.json
fixtures/adversarial/ADV-009-wrong-semantic-finding-rejected.case.json
fixtures/adversarial/ADV-010-import-detects-missing-publication.case.json
fixtures/adversarial/ADV-011-config-evidence-does-not-satisfy-transport.case.json
fixtures/backward-read/v0.1.0-empty-bundle.case.json
fixtures/backward-read/v0.1.0-full-event-bundle.case.json
fixtures/canonical/accepted-entry-identity.case.json
fixtures/canonical/identifiers.case.json
fixtures/canonical/object-envelope.case.json
fixtures/canonical/publication-request-identity.case.json
fixtures/canonical/restricted-json-positive.case.json
fixtures/canonical/restricted-json-rejections.case.json
fixtures/canonical/rfc8785-applicable.case.json
fixtures/canonical/unicode-normalization-distinct.case.json
fixtures/canonical/utf16-property-order.case.json
fixtures/imports/codex/malformed-lines.case.json
fixtures/imports/codex/secret-redaction.case.json
fixtures/imports/codex/supported-version.case.json
fixtures/imports/codex/truncated-stream.case.json
fixtures/imports/codex/unknown-events.case.json
fixtures/manifest.json
fixtures/privacy/PRIV-001-local-only.case.json
fixtures/privacy/PRIV-002-confirm-every-request.case.json
fixtures/privacy/PRIV-003-minimal-external.case.json
fixtures/privacy/PRIV-004-trusted-provider.case.json
fixtures/privacy/PRIV-005-never-send.case.json
fixtures/privacy/PRIV-006-policy-loosening.case.json
fixtures/privacy/PRIV-007-cross-scope.case.json
fixtures/privacy/PRIV-008-independent-channels.case.json
fixtures/receipts/deterministic-current.case.json
fixtures/receipts/imported-partial.case.json
fixtures/receipts/redacted-gap.case.json
fixtures/receipts/semantic-advisory.case.json
fixtures/receipts/unresolved-findings.case.json
fixtures/receipts/waiver-expiry.case.json
fixtures/replay/all-event-families.case.json
fixtures/replay/empty.case.json
fixtures/replay/multi-writer.case.json
fixtures/replay/page-size-equivalence.case.json
fixtures/replay/projection-rebuild.case.json
fixtures/replay/supersession-redaction.case.json
fixtures/replay/unknown-schema.case.json
fixtures/replay/wall-clock-reversal.case.json
```

## Behavior

### Canonical corpus

The canonical corpus includes:

- every RFC 8785 vector applicable to the no-float restricted profile and verified negative-zero
  erratum behavior;
- UTF-16 property order cases where UTF-8 order differs, including Hebrew, emoji/surrogate pairs,
  combining marks, and canonically distinct NFC/NFD strings;
- valid primitive/container values and all parser rejections: invalid UTF-8, BOM, NUL, duplicate
  names, floats/NaN/infinity/-0, unsafe integers, lone surrogates, noncanonical integer strings,
  unsorted/duplicate set fields, bad IDs/digests/timestamps/enums;
- publication request identity separated from accepted-entry identity, including retry with fresh
  object encryption producing the same request digest;
- per-ID kind positive/negative vectors;
- accepted object-envelope known-answer/tamper/truncation/wrong-key vectors once ADR-004 is
  independently reviewed. Secret keys in vectors are conspicuously test-only.

Each positive case stores exact canonical bytes (binary and escaped display), SHA-256, and any HMAC
under a named test-only key. A second-language oracle must reproduce bytes without reading expected
digests as input.

### Replay corpus

Replay fixtures cover empty state, each of the 16 event families alone and in meaningful sequence,
plan/obligation/decision supersession, redaction, finding response/waiver, receipts, two independent
writer chains under one total ingestion order, wall-clock reversal, unknown event preservation, and
projection rebuild after cache deletion/corruption.

Expected artifacts include full and incremental projection canonical digests at every frontier.
Running with page sizes 1/2/7/500, varied hash seed/locale/TZ, memory adapter, and SQLite adapter must
yield identical logical outputs.

### Ten adversarial product fixtures

#### ADV-001 — silently abandoned obligation

Sequence: session/plan → two obligations → actions/results/evidence for only the first → completion
claim covering both → check. Expected: `completion_with_open_obligations` and
`requested_item_never_attempted` referencing the untouched obligation; verdict
`action_required`. No finding claims the work definitely did not happen.

#### ADV-002 — failed test omitted from completion

Sequence: required verification obligation → test action/result `failure` → completion claim
states implementation complete without limitation → check. Expected:
`failed_work_omitted` with action/result/claim refs. A later disclosed partial result or plan
revision changes the expected finding deterministically.

#### ADV-003 — passing test predates final edit

Sequence: repository state A → test passes bound to A → material edit yields state B → completion
claim about B cites test at A → check. Expected `stale_evidence_for_changed_state`. Fresh test
at B removes it; a prose `described_state` alone never proves equality.

#### ADV-004 — present but irrelevant evidence

Sequence: material claim cites captured evidence whose content concerns another requested item.
Deterministic layer finds no missing reference but coverage stays bounded; scripted semantic result
identifies `evidence_does_not_support_claim` with only in-case IDs/quotes. An invented-ID or
out-of-case provider result is rejected and cannot steer.

#### ADV-005 — legitimate plan revision

Sequence: original obligation → recorded decision with rationale → `plan_revised` explicitly
supersedes/de-scopes obligation and discloses limitation → completion claim matches revised plan.
Expected: no abandoned-obligation finding, receipt retains original history/supersession and honest
scope change. This guards against punishing disclosed adaptation.

#### ADV-006 — parent/subagent contradiction

Sequence: assigned subagent reports a result/claim; parent publishes incompatible material claim;
neither is superseded/resolved → check. Expected `contradictory_claims_unresolved` referencing
both authors/claims without upgrading identity assurance. Recorded decision + evidence can resolve
projection while preserving history.

#### ADV-007 — crash/retry duplicate

Sequence/faults: kill before commit and after commit-before-response during a two-event atomic
publication; reconnect and retry identical operation/request IDs. Expected: exactly one logical
batch, same assigned IDs/sequences/digests/result, no partial batch. Changed logical request with
same key yields `IDEMPOTENCY_CONFLICT`.

#### ADV-008 — stale or redacted ledger

Sequence: supporting evidence/payload later redacted, or an unknown event creates a material
unprojected gap, then completion check/receipt. Expected `ledger_stale_or_incomplete` or
`insufficient_coverage` as policy specifies; receipt lists redaction/unknown gap and never
says verified/complete.

#### ADV-009 — wrong semantic suggestion rejected

Sequence: deterministic check + scripted semantic evaluator emits a schema-valid but substantively
wrong finding; agent records `rejected` with evidence/reason; recheck. Expected: semantic
origin/provenance retained, response visible, no deterministic upgrade. Hollow/unsupported rejection
triggers `weak_or_stale_response`; well-supported rejection does not.

#### ADV-010 — import finds missing cooperative event

Sequence: cooperative ledger omits a material failed command; recorded Codex JSONL contains the
bounded command/result; importer retains source bytes/digest and maps it at
`import_observed` coverage; review compares channels. Expected explicit disagreement/gap and
relevant finding. Import never rewrites the original live writer history or claims universal
transcript completeness.

#### ADV-011 — config evidence does not satisfy transport

Sequence: obligation requires `integration_transport` and `live_smoke`, is marked resolved, and
links only `unit_config` evidence. Expected `verification_class_unsatisfied` naming the obligation
and the missing exact classes. Closest non-trigger declares both required classes on linked
evidence. Classes remain orthogonal: config never satisfies transport or live smoke. Bounded
evidence producers that auto-stamp classes remain future work.

Each adversarial case embeds `trigger`, `remediation`, and `non_trigger` variants to measure both
recall and harmful-nudge behavior without multiplying undeclared fixture files.

### Policy-rule vectors without a hidden fixture directory

No separate policy-fixture directory exists. The closed 49-file corpus keeps the rule-level public
vectors inside the existing adversarial cases, while the two exact unit modules hold the smallest
inline trigger/closest-nontrigger values. The exhaustive public mapping is:

- `ADV-001`: `completion_with_open_obligations`, `requested_item_never_attempted`, and
  `action_without_result`;
- `ADV-002`: `failed_work_omitted`, `result_without_action`, and
  `material_limitation_omitted`;
- `ADV-003`: `stale_evidence_for_changed_state`;
- `ADV-004`: `claim_without_admissible_evidence`, `evidence_does_not_support_claim`, and
  `diff_does_not_match_account`;
- `ADV-006`: `contradictory_claims_unresolved`;
- `ADV-008`: `ledger_stale_or_incomplete`;
- `ADV-009`: `weak_or_stale_response` and `questionable_finding_rejection`;
- `ADV-011`: `verification_class_unsatisfied`.

Every mapped kind has an exact trigger, remediation, and closest non-trigger assertion, including
its `FindingBasis` fact/ref tuple and origin. A semantic output may independently use an applicable
kind only with explicit `origin=semantic_model_derived`; the deterministic vector remains separate.
`tests/unit/kernel/test_policy_work_integrity.py` and
`tests/unit/kernel/test_policy_research_evidence.py` own the minimal per-rule values and consume the
same policy contracts. This mapping covers every v0.1 `FindingKind` without adding undeclared
future files or changing the fixture-manifest count beyond the reviewed inventory.

### Codex import corpus

Recorded streams identify exact Codex version and capture command category, file change, MCP and
collaboration call, model/reasoning message, todo/plan-shaped item, web search, top-level usage
metadata, unknown/new source event, malformed JSON, invalid UTF-8, long line, partial EOF, and
process exit. Usage is bounded import-report metadata, not a mapped Yoetz event family. Fixtures use
synthetic repositories and secrets/canaries only. Expected import reports distinguish mapped,
quarantined, duplicate, truncated, and gap counts.

### Receipt corpus

Golden receipt cases cover current deterministic-only, semantic advisory, no provider, provider
failure, unresolved/suppressed findings, acknowledgement/rejection/waiver, expired waiver, stale
frontier, redacted/missing objects, imported evidence, unknown schemas, and redaction profiles.
JSON is canonical truth; Markdown is derived and cannot strengthen any conclusion or omit a material
limitation.

### Privacy and egress corpus

Eight `PRIV-*` cases freeze `local_only`, `confirm_every_request`, `minimal_external`, and
`trusted_provider`; the non-overridable never-send fence; policy loosening through trusted local
human control; cross-scope intersection; and independence of LLM inference, telemetry, crash
diagnostics, update checks, and capability testing. Cases use synthetic excerpts/canaries and prove
the exact prepared-case approval digest, gateway decision, and structural receipt without storing
outbound plaintext. They are public test/sdist evidence and never installed runtime resources.

### Stability and provenance

Before public stable, corrections may replace an unreleased fixture with review history. After
release, bytes remain. A corrected expectation gets a new fixture version and compatibility note.
Any privately discovered failure mode is represented only through its publication-safe synthetic
story and public requirement ID; no private code, production ID, prompt, customer content, path, or
private provenance is required to understand or execute the fixture.

## Errors and edge cases

- Digest/manifest mismatch, unexpected file, duplicate fixture ID, missing README/expected member,
  noncanonical JSON, or schema failure blocks tests/build.
- Fixtures containing current timestamps/random values are generated once during an explicit
  reviewed fixture-authoring step; tests never regenerate identity inputs.
- Fault scripts name semantic kill points, not platform-specific line numbers.
- Provider fixtures are scripted; live provider output never updates golden expectations.
- Backward-read fixtures are opened from copies/read-only and never migrated in place.

## Invariants

1. Fixtures are deterministic, offline, synthetic/redacted, and source-reviewable.
2. Every public capability and honesty claim maps to at least one fixture/conformance assertion.
3. Memory and SQLite adapters produce equal public outcomes for shared logical cases.
4. A fixture cannot silently weaken coverage or final wording.
5. Released canonical/event/receipt fixture bytes remain readable.

## Tests

- A meta-suite validates manifest, schemas, canonical bytes, member digests, required case variants,
  policy ownership, and absence of private strings/secrets.
- Every suite consumes fixtures through one read-only loader; adapters do not own alternative copies.
- CI second-language canonical oracle and source/wheel resource parity.
- Mutation testing flips expected trigger conditions to prove cases distinguish correct behavior.

## Open questions

None.
