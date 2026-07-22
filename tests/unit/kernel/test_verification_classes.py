"""Verification-class vocabulary, payload bounds, work-integrity rule, and receipt disclosure."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import pytest

from builders.policy_cases import (
    BASE_COVERAGE,
    FRONTIER,
    evd,
    evidence_record,
    make_case,
    obl,
    obligation_record,
)
from yoetz.domain.events import (
    SCHEMA_VERSION,
    EventSchema,
    EvidenceKind,
    EvidenceRecordedPayload,
    ObligationPublishedPayload,
    ObligationStatus,
    VerificationClass,
    decode_payload,
    encode_payload,
)
from yoetz.domain.findings import FindingKind
from yoetz.domain.receipts import (
    PolicyVersionEntry,
    ReceiptObligation,
    ReceiptObligationStatus,
    ReceiptSectionKey,
    ReceiptVersionSlice,
    SchemaVersionEntry,
)
from yoetz.domain.values import (
    object_id,
    receipt_id,
    session_id,
    task_id,
    timestamp_from_string,
)
from yoetz.kernel.deterministic_checks import (
    CaseAvailabilityFacts,
    CaseGap,
    run_deterministic_policies,
)
from yoetz.kernel.policies.work_integrity import WORK_INTEGRITY_POLICY_PACK
from yoetz.kernel.receipt_builder import ReceiptBuildContext, build_receipt
from yoetz.protocol.canonical import JsonValue
from yoetz.protocol.coverage import (
    ArtifactObservation,
    AuthorshipAssurance,
    CheckType,
    Coverage,
    EvidenceImmutability,
    LedgerFreshness,
    PublicationChannel,
    weakest,
)
from yoetz.protocol.errors import ProtocolValueError
from yoetz.protocol.models import ReceiptInclude, ReceiptRedactionProfile

_NOW = timestamp_from_string("2026-01-01T00:00:00.000Z")
_DIGEST_A = "sha256:" + "1" * 64
_DIGEST_B = "sha256:" + "2" * 64


def _kinds(case: object) -> tuple[FindingKind, ...]:
    result = run_deterministic_policies(case, WORK_INTEGRITY_POLICY_PACK)  # type: ignore[arg-type]
    return tuple(item.candidate.kind for item in result.assessments)


def _evidence(
    number: int,
    *classes: VerificationClass,
) -> EvidenceRecordedPayload:
    return EvidenceRecordedPayload(
        evidence_id=evd(number),
        evidence_kind=EvidenceKind.TEST_RESULT,
        strength=EvidenceImmutability.IMMUTABLE_SNAPSHOT,
        observed_at=_NOW,
        captured_object_id=object_id(f"obj_10000000-0000-4000-8000-{number:012x}"),
        content_digest=_DIGEST_A if number == 1 else _DIGEST_B,
        verification_classes=classes,
    )


def _resolved(
    number: int,
    *required: VerificationClass,
    resolution: tuple[str, ...] = (),
) -> ObligationPublishedPayload:
    refs = resolution if resolution else (evd(number),)
    return ObligationPublishedPayload(
        obligation_id=obl(number),
        description="Synthetic classed obligation",
        evidence_expectation="Class-declared evidence",
        status=ObligationStatus.RESOLVED,
        resolution_evidence_refs=refs,  # type: ignore[arg-type]
        required_verification_classes=required,
    )


def test_verification_class_registry_is_exact_and_orthogonal() -> None:
    assert tuple(item.value for item in VerificationClass) == (
        "unit_config",
        "integration_transport",
        "production_composition",
        "capability",
        "live_smoke",
        "source_review",
    )
    assert len(VerificationClass) == 6


def test_obligation_and_evidence_reject_unknown_duplicate_and_overlong_classes() -> None:
    with pytest.raises(ProtocolValueError):
        ObligationPublishedPayload(
            obligation_id=obl(1),
            description="d",
            evidence_expectation="e",
            status=ObligationStatus.OPEN,
            required_verification_classes=("not_a_class",),  # type: ignore[arg-type]
        )
    with pytest.raises(ProtocolValueError, match="duplicate_set_member"):
        ObligationPublishedPayload(
            obligation_id=obl(1),
            description="d",
            evidence_expectation="e",
            status=ObligationStatus.OPEN,
            required_verification_classes=(
                VerificationClass.UNIT_CONFIG,
                VerificationClass.UNIT_CONFIG,
            ),
        )
    with pytest.raises(ProtocolValueError):
        ObligationPublishedPayload(
            obligation_id=obl(1),
            description="d",
            evidence_expectation="e",
            status=ObligationStatus.OPEN,
            required_verification_classes=tuple(VerificationClass)
            + (VerificationClass.UNIT_CONFIG,),
        )
    with pytest.raises(ProtocolValueError, match="unsorted_set_field"):
        EvidenceRecordedPayload(
            evidence_id=evd(1),
            evidence_kind=EvidenceKind.TEST_RESULT,
            strength=EvidenceImmutability.METADATA_ONLY,
            observed_at=_NOW,
            description="d",
            verification_classes=(
                VerificationClass.LIVE_SMOKE,
                VerificationClass.CAPABILITY,
            ),
        )


def test_optional_class_fields_round_trip_and_omit_when_empty() -> None:
    obligation = ObligationPublishedPayload(
        obligation_id=obl(1),
        description="d",
        evidence_expectation="e",
        status=ObligationStatus.OPEN,
        required_verification_classes=(
            VerificationClass.INTEGRATION_TRANSPORT,
            VerificationClass.LIVE_SMOKE,
        ),
    )
    encoded = encode_payload(obligation)
    encoded_obj = cast(Mapping[str, JsonValue], encoded)
    assert encoded_obj["required_verification_classes"] == (
        "integration_transport",
        "live_smoke",
    )
    decoded = decode_payload(
        EventSchema("obligation_published", SCHEMA_VERSION),
        encoded,
    )
    assert decoded == obligation

    legacy = ObligationPublishedPayload(
        obligation_id=obl(1),
        description="d",
        evidence_expectation="e",
        status=ObligationStatus.OPEN,
    )
    legacy_encoded = cast(Mapping[str, JsonValue], encode_payload(legacy))
    assert "required_verification_classes" not in legacy_encoded


def test_verification_class_unsatisfied_missing_one_all_and_legacy() -> None:
    trigger_all = make_case(
        obligations={
            obl(1): obligation_record(
                _resolved(
                    1,
                    VerificationClass.INTEGRATION_TRANSPORT,
                    VerificationClass.LIVE_SMOKE,
                ),
                1,
            )
        },
        evidence={evd(1): evidence_record(_evidence(1, VerificationClass.UNIT_CONFIG), 2)},
    )
    result = run_deterministic_policies(trigger_all, WORK_INTEGRITY_POLICY_PACK)
    finding = next(
        item
        for item in result.assessments
        if item.candidate.kind is FindingKind.VERIFICATION_CLASS_UNSATISFIED
    )
    assert finding.candidate.subject_refs == (obl(1),)
    assert tuple(fact.fact_code for fact in finding.basis.observed_facts) == (
        "class_requirement_present",
    )
    assert tuple(fact.fact_code for fact in finding.basis.required_but_missing_facts) == (
        "unsatisfied_class_integration_transport",
        "unsatisfied_class_live_smoke",
    )

    trigger_one = make_case(
        obligations={
            obl(1): obligation_record(
                _resolved(
                    1,
                    VerificationClass.INTEGRATION_TRANSPORT,
                    VerificationClass.LIVE_SMOKE,
                    resolution=(evd(1), evd(2)),
                ),
                1,
            )
        },
        evidence={
            evd(1): evidence_record(_evidence(1, VerificationClass.INTEGRATION_TRANSPORT), 2),
            evd(2): evidence_record(_evidence(2, VerificationClass.UNIT_CONFIG), 3),
        },
    )
    one = next(
        item
        for item in run_deterministic_policies(trigger_one, WORK_INTEGRITY_POLICY_PACK).assessments
        if item.candidate.kind is FindingKind.VERIFICATION_CLASS_UNSATISFIED
    )
    assert tuple(fact.fact_code for fact in one.basis.required_but_missing_facts) == (
        "unsatisfied_class_live_smoke",
    )

    legacy = make_case(
        obligations={
            obl(1): obligation_record(
                ObligationPublishedPayload(
                    obligation_id=obl(1),
                    description="d",
                    evidence_expectation="e",
                    status=ObligationStatus.RESOLVED,
                    resolution_evidence_refs=(evd(1),),
                ),
                1,
            )
        },
        evidence={evd(1): evidence_record(_evidence(1), 2)},
    )
    assert FindingKind.VERIFICATION_CLASS_UNSATISFIED not in _kinds(legacy)

    unclassified = make_case(
        obligations={
            obl(1): obligation_record(
                _resolved(1, VerificationClass.INTEGRATION_TRANSPORT),
                1,
            )
        },
        evidence={evd(1): evidence_record(_evidence(1), 2)},
    )
    assert FindingKind.VERIFICATION_CLASS_UNSATISFIED in _kinds(unclassified)

    satisfied = make_case(
        obligations={
            obl(1): obligation_record(
                _resolved(
                    1,
                    VerificationClass.INTEGRATION_TRANSPORT,
                    VerificationClass.LIVE_SMOKE,
                    resolution=(evd(1), evd(2)),
                ),
                1,
            )
        },
        evidence={
            evd(1): evidence_record(_evidence(1, VerificationClass.INTEGRATION_TRANSPORT), 2),
            evd(2): evidence_record(_evidence(2, VerificationClass.LIVE_SMOKE), 3),
        },
    )
    assert FindingKind.VERIFICATION_CLASS_UNSATISFIED not in _kinds(satisfied)


def test_receipt_discloses_satisfied_and_unsatisfied_verification_classes() -> None:
    case = make_case(
        obligations={
            obl(1): obligation_record(
                _resolved(
                    1,
                    VerificationClass.INTEGRATION_TRANSPORT,
                    VerificationClass.LIVE_SMOKE,
                    VerificationClass.UNIT_CONFIG,
                    resolution=(evd(1),),
                ),
                1,
            )
        },
        evidence={evd(1): evidence_record(_evidence(1, VerificationClass.UNIT_CONFIG), 2)},
        gaps=(
            CaseGap(
                marker="gap_check_absent",
                code="check_not_recorded",
                subject_refs=(),
            ),
        ),
    )
    coverage = weakest(
        BASE_COVERAGE,
        Coverage(
            publication_channels=(PublicationChannel.ENGINE_DERIVED,),
            authorship_assurance=AuthorshipAssurance.SELF_ASSERTED,
            artifact_observation=ArtifactObservation.CONTENT_CAPTURED,
            evidence_immutability=EvidenceImmutability.IMMUTABLE_SNAPSHOT,
            ledger_freshness=LedgerFreshness.PARTIAL,
            check_types=(CheckType.DETERMINISTIC,),
            known_gaps=("check_not_recorded",),
        ),
    )
    versions = ReceiptVersionSlice(
        package_name="yoetz",
        package_version="0.1.0",
        protocol_version="0.1",
        engine_version="0.1.0",
        projection_version="yoetz/0.1.0",
        object_format_version="yoetz-object/1",
        catalog_schema_version="1",
        bundle_schema_version="1",
        policy_versions=(PolicyVersionEntry(policy_id="work-integrity", policy_version="0.1.0"),),
        schema_versions=(
            SchemaVersionEntry(schema_id="events/obligation-published", schema_version="1.0.0"),
        ),
        resource_manifest_digest="sha256:" + "c" * 64,
    )
    context = ReceiptBuildContext(
        projection=case.projection,
        subject_frontier=FRONTIER,
        availability=CaseAvailabilityFacts(),
        coverage=coverage,
        gaps=(
            CaseGap(
                marker="gap_check_absent",
                code="check_not_recorded",
                subject_refs=(),
            ),
        ),
        finding_states=(),
        applicable_check=None,
    )
    document = build_receipt(
        context,
        receipt_id("rcp_00000000-0000-4000-8000-000000000001"),
        task_id("tsk_00000000-0000-4000-8000-000000000001"),
        session_id("ses_00000000-0000-4000-8000-000000000001"),
        _NOW,
        versions,
        ReceiptRedactionProfile.DEFAULT_LOCAL_EXPORT,
        ReceiptInclude.STANDARD,
    )
    obligation = document.obligations[0]
    assert obligation.required_verification_classes == (
        "integration_transport",
        "live_smoke",
        "unit_config",
    )
    assert obligation.satisfied_verification_classes == ("unit_config",)
    assert obligation.unsatisfied_verification_classes == (
        "integration_transport",
        "live_smoke",
    )
    limitations = next(
        section
        for section in document.sections
        if section.key is ReceiptSectionKey.LIMITATIONS_AND_COVERAGE
    )
    assert "Satisfied verification classes: unit_config." in limitations.body
    assert (
        "Unsatisfied verification classes: integration_transport, live_smoke." in limitations.body
    )
    assert "satisfied:unit_config" in limitations.items
    assert "unsatisfied:integration_transport" in limitations.items
    assert "unsatisfied:live_smoke" in limitations.items


def test_receipt_obligation_class_fields_validate_bounds() -> None:
    with pytest.raises(ProtocolValueError):
        ReceiptObligation(
            obligation_id=obl(1),
            status=ReceiptObligationStatus.RESOLVED,
            source_refs=(),
            required_verification_classes=("unit_config",),
            satisfied_verification_classes=("live_smoke",),
        )


def test_semantic_origin_cannot_satisfy_required_verification_classes() -> None:
    """Requirement: semantic-origin findings/data never satisfy required_verification_classes.

    Class satisfaction is derived only from published EvidenceRecordedPayload.verification_classes
    on admissible resolution evidence. Semantic findings are advisory and never enter that set.
    """

    from yoetz.domain.findings import FindingOrigin

    case = make_case(
        obligations={
            obl(1): obligation_record(
                _resolved(1, VerificationClass.LIVE_SMOKE, VerificationClass.UNIT_CONFIG),
                1,
            )
        },
        evidence={evd(1): evidence_record(_evidence(1), 2)},
    )
    assert FindingKind.VERIFICATION_CLASS_UNSATISFIED in _kinds(case)

    # Evidence payloads carry producer-declared classes only; they have no semantic origin field
    # and empty class tuples never upgrade a required class to satisfied.
    for record in case.projection.evidence.values():
        assert record.payload is not None
        assert not hasattr(record.payload, "origin")
        assert record.payload.verification_classes == ()
    assert FindingOrigin.SEMANTIC_MODEL_DERIVED.value == "semantic_model_derived"

    # Adding classed evidence is the only path that clears the finding — findings origin never
    # participates.
    cleared = make_case(
        obligations={
            obl(1): obligation_record(
                _resolved(
                    1,
                    VerificationClass.LIVE_SMOKE,
                    VerificationClass.UNIT_CONFIG,
                    resolution=(evd(1), evd(2)),
                ),
                1,
            )
        },
        evidence={
            evd(1): evidence_record(_evidence(1, VerificationClass.LIVE_SMOKE), 2),
            evd(2): evidence_record(_evidence(2, VerificationClass.UNIT_CONFIG), 3),
        },
    )
    assert FindingKind.VERIFICATION_CLASS_UNSATISFIED not in _kinds(cleared)
