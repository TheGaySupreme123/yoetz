"""Honesty conformance: adversarial fixtures never let Yoetz overclaim capability or coverage.

Grounded entirely in the reviewed ``fixtures/adversarial/ADV-*.case.json`` corpus and the exhaustive
mapping frozen in ``fixtures/README.md``: every registered ``FindingKind`` is owned by exactly one
of the seven mapped adversarial cases, each mapped case carries a genuine trigger, an in-fixture
remediation/closest-non-trigger pairing that clears the finding, and every finding object inside the
corpus round-trips through the real domain codec (``finding_from_json``) -- proving the fixtures
describe structurally admissible, policy-bound findings rather than free-form prose.
"""

from __future__ import annotations

from typing import cast

from fixture_loader import FixtureLoader, JsonValue
from yoetz.domain.findings import Finding, FindingKind, finding_from_json
from yoetz.domain.values import freeze_json

# The exhaustive public policy-rule mapping frozen in specs/fixtures/README.md /
# fixtures/README.md. Every one of the 14 registered FindingKind values is owned by exactly
# one of these seven adversarial cases; ADV-005/007/010 exist but are deliberately excluded from
# this mapping (they exercise plan-revision honesty, crash/retry idempotency, and cross-channel
# import comparison -- not a new FindingKind of their own).
_ADV_KIND_MAP: dict[str, frozenset[FindingKind]] = {
    "ADV-001-abandoned-obligation": frozenset(
        {
            FindingKind.COMPLETION_WITH_OPEN_OBLIGATIONS,
            FindingKind.REQUESTED_ITEM_NEVER_ATTEMPTED,
            FindingKind.ACTION_WITHOUT_RESULT,
        }
    ),
    "ADV-002-omitted-failed-test": frozenset(
        {
            FindingKind.FAILED_WORK_OMITTED,
            FindingKind.RESULT_WITHOUT_ACTION,
            FindingKind.MATERIAL_LIMITATION_OMITTED,
        }
    ),
    "ADV-003-stale-test-after-edit": frozenset({FindingKind.STALE_EVIDENCE_FOR_CHANGED_STATE}),
    "ADV-004-irrelevant-evidence": frozenset(
        {
            FindingKind.CLAIM_WITHOUT_ADMISSIBLE_EVIDENCE,
            FindingKind.EVIDENCE_DOES_NOT_SUPPORT_CLAIM,
            FindingKind.DIFF_DOES_NOT_MATCH_ACCOUNT,
        }
    ),
    "ADV-006-parent-subagent-contradiction": frozenset(
        {FindingKind.CONTRADICTORY_CLAIMS_UNRESOLVED}
    ),
    "ADV-008-stale-redacted-ledger": frozenset({FindingKind.LEDGER_STALE_OR_INCOMPLETE}),
    "ADV-009-wrong-semantic-finding-rejected": frozenset(
        {FindingKind.WEAK_OR_STALE_RESPONSE, FindingKind.QUESTIONABLE_FINDING_REJECTION}
    ),
}

# All ten adversarial cases, including the three excluded from the kind-ownership mapping above.
_ALL_ADV_IDS = (
    "ADV-001-abandoned-obligation",
    "ADV-002-omitted-failed-test",
    "ADV-003-stale-test-after-edit",
    "ADV-004-irrelevant-evidence",
    "ADV-005-legitimate-plan-revision",
    "ADV-006-parent-subagent-contradiction",
    "ADV-007-crash-retry-duplicate",
    "ADV-008-stale-redacted-ledger",
    "ADV-009-wrong-semantic-finding-rejected",
    "ADV-010-import-detects-missing-publication",
)


def _fixture_path(adv_id: str) -> str:
    return f"adversarial/{adv_id}.case.json"


def _load_expected(fixture_loader: FixtureLoader, adv_id: str) -> dict[str, object]:
    document = cast(dict[str, object], fixture_loader.load_json(_fixture_path(adv_id)))
    return cast(dict[str, object], document["expected"])


def _variants(expected: dict[str, object]) -> dict[str, dict[str, object]]:
    raw = cast(dict[str, object], expected["variants"])
    return {name: cast(dict[str, object], value) for name, value in raw.items()}


def _relationships(expected: dict[str, object]) -> list[dict[str, str]]:
    return cast(list[dict[str, str]], expected["relationships"])


def _finding_kinds(variant: dict[str, object]) -> frozenset[FindingKind]:
    raw = variant.get("finding_kinds")
    if raw is None:
        return frozenset()
    return frozenset(FindingKind(value) for value in cast(list[str], raw))


def _raw_findings(variant: dict[str, object]) -> list[dict[str, object]]:
    # Deterministic-origin variants store their payload under "findings"; accepted semantic-origin
    # findings (ADV-004's "semantic_present_irrelevant") store the same shape under
    # "accepted_semantic_findings" instead.
    raw = variant.get("findings") or variant.get("accepted_semantic_findings")
    if not raw:
        return []
    return cast(list[dict[str, object]], raw)


_FINDING_WIRE_KEYS = frozenset(
    {
        "finding_id",
        "kind",
        "origin",
        "priority",
        "summary",
        "detail",
        "subject_refs",
        "policy_id",
        "policy_version",
        "subject_frontier",
        "coverage",
        "provenance",
    }
)


def _decode_findings(variant: dict[str, object]) -> tuple[Finding, ...]:
    # Fixture finding objects carry extra fixture-authoring fields beyond the closed wire
    # ``finding-1.0.0`` schema -- ``basis`` (the deterministic/semantic rule id, observed/missing
    # facts, and state relation that document *why* the finding exists) and, for accepted semantic
    # findings, ``reviewer_challenge``. Strip everything outside the real wire field set before
    # round-tripping through the real domain codec.
    findings: list[Finding] = []
    for raw in _raw_findings(variant):
        wire = {key: value for key, value in raw.items() if key in _FINDING_WIRE_KEYS}
        findings.append(finding_from_json(freeze_json(cast(JsonValue, wire))))
    return tuple(findings)


def test_adv_claim_fixtures_fail_closed(fixture_loader: FixtureLoader) -> None:
    """Every registered FindingKind is owned by exactly one mapped case, with no leftovers."""

    union: set[FindingKind] = set()
    for adv_id, kinds in _ADV_KIND_MAP.items():
        assert kinds, adv_id
        overlap = union & kinds
        assert not overlap, (adv_id, overlap)
        union |= kinds
    assert union == frozenset(FindingKind), frozenset(FindingKind) - union

    for adv_id, mapped_kinds in _ADV_KIND_MAP.items():
        expected = _load_expected(fixture_loader, adv_id)
        variants = _variants(expected)
        observed_kinds: set[FindingKind] = set()

        for name, variant in variants.items():
            declared = _finding_kinds(variant)
            # A fixture never claims a kind outside its own declared, policy-mapped set -- there is
            # no lookup of an undeclared policy-resource path.
            assert declared <= mapped_kinds, (adv_id, name, declared - mapped_kinds)
            observed_kinds |= declared

            # Every finding admissibly round-trips through the real domain codec: malformed or
            # policy-mismatched data would raise here instead of silently being accepted, and the
            # decoded kind set always matches what the fixture itself declares.
            findings = _decode_findings(variant)
            assert {finding.kind for finding in findings} == declared, (adv_id, name)
            for finding in findings:
                assert finding.kind in mapped_kinds

        # Every kind this case owns is actually exercised by at least one trigger variant.
        assert observed_kinds == mapped_kinds, (adv_id, mapped_kinds - observed_kinds)


def test_claim_language_remains_within_supported_bounds(fixture_loader: FixtureLoader) -> None:
    """Finding summaries/details in the adversarial corpus stay within conservative wording."""

    banned_tokens = (
        "definitely",
        "100%",
        "guarantee",
        "proven",
        "always correct",
        "certainly true",
    )
    checked_any = False
    for adv_id in _ALL_ADV_IDS:
        expected = _load_expected(fixture_loader, adv_id)
        for name, variant in _variants(expected).items():
            for finding in _decode_findings(variant):
                checked_any = True
                for text in (finding.summary, finding.detail):
                    lowered = text.lower()
                    for token in banned_tokens:
                        assert token not in lowered, (adv_id, name, token, text)
            must_not_claim = variant.get("must_not_claim")
            if must_not_claim:
                # A variant that explicitly records forbidden phrasing never lets that phrasing
                # leak into its own findings/detail text.
                for phrase in cast(list[str], must_not_claim):
                    for finding in _decode_findings(variant):
                        assert phrase not in finding.summary, (adv_id, name, phrase)
                        assert phrase not in finding.detail, (adv_id, name, phrase)
    assert checked_any


def test_counterexample_shrinks_to_named_claim(fixture_loader: FixtureLoader) -> None:
    """Every adversarial relationship names two real variants and a nonempty explanation."""

    for adv_id in _ALL_ADV_IDS:
        expected = _load_expected(fixture_loader, adv_id)
        variants = _variants(expected)
        relationships = _relationships(expected)
        assert relationships, adv_id

        for relationship in relationships:
            source_name = relationship["from"]
            target_name = relationship["to"]
            assertion = relationship["assertion"]
            assert source_name in variants, (adv_id, source_name)
            assert target_name in variants, (adv_id, target_name)
            assert isinstance(assertion, str) and assertion.strip(), (adv_id, relationship)

            source = variants[source_name]
            target = variants[target_name]
            if "finding_kinds" in source and "finding_kinds" in target:
                source_kinds = _finding_kinds(source)
                target_kinds = _finding_kinds(target)
                # Where both sides declare finding kinds, the two named claim sets are always
                # nested (one is a subset of the other, usually the empty set on one side) -- a
                # relationship never links two orthogonal, unrelated named claims.
                assert source_kinds <= target_kinds or target_kinds <= source_kinds, (
                    adv_id,
                    source_name,
                    target_name,
                    source_kinds,
                    target_kinds,
                )


def test_semantic_packet_and_challenge_fixtures_are_exact(fixture_loader: FixtureLoader) -> None:
    """ADV-002, ADV-003, ADV-004, and ADV-009 lock their documented semantic-authority fences."""

    # ADV-002: the trigger's reviewer challenge asks the main agent for the smallest useful next
    # step, citing real refs; a fixture that discloses the failure carries no residual challenge.
    adv_002 = _variants(_load_expected(fixture_loader, "ADV-002-omitted-failed-test"))
    trigger_002 = adv_002["trigger"]
    challenges = cast(list[dict[str, object]], trigger_002["assisted_semantic_challenges"])
    assert challenges
    for challenge in challenges:
        assert challenge["requested_next_step"]
        assert cast(list[str], challenge["cited_refs"])
    disclosed = adv_002["disclosed_partial"]
    assert disclosed.get("assisted_semantic_challenges") == []
    assert _finding_kinds(disclosed) == frozenset()

    # ADV-003: claimed change, the same|different|unknown state relation, and content visibility
    # vary independently; withholding excerpt content never renders as "no diff" (never "same").
    adv_003 = _variants(_load_expected(fixture_loader, "ADV-003-stale-test-after-edit"))
    for name in ("trigger", "changed_state_content_withheld"):
        findings = _raw_findings(adv_003[name])
        assert findings, name
        for finding in findings:
            basis = cast(dict[str, object], finding["basis"])
            relation = basis["subject_state_relation"]
            assert relation in {"same", "different", "unknown"}, (name, relation)
            assert relation != "same", (
                name,
                "withheld or observed-different content is never same",
            )
    assert _finding_kinds(adv_003["closest_non_trigger_same_state"]) == frozenset()

    # ADV-004: refs cited by an accepted semantic finding are limited to the case's own admissible
    # set; an invented reference or a mutated deterministic basis is rejected before construction.
    adv_004 = _variants(_load_expected(fixture_loader, "ADV-004-irrelevant-evidence"))
    for rejected_name, expected_reason in (
        ("semantic_invented_ref", "rejected_reference_outside_case"),
        ("semantic_basis_mutation", "rejected_basis_mutation"),
    ):
        rejected = adv_004[rejected_name]
        assert rejected["accepted_semantic_findings"] == []
        assert rejected["post_validation"] == expected_reason

    # ADV-009: accepted reviewer guidance always requires a fresh check, and no variant grants the
    # model a waiver of the finding.
    adv_009 = _variants(_load_expected(fixture_loader, "ADV-009-wrong-semantic-finding-rejected"))
    assert adv_009
    for name, variant in adv_009.items():
        assert "waiver" not in variant and "waiver_scope" not in variant, name
        fresh_required = variant.get("fresh_check_required")
        if fresh_required is not None:
            assert fresh_required is True, name
