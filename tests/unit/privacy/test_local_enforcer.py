"""Provider-free deterministic privacy enforcement tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from yoetz.adapters.privacy.local_enforcer import LocalPrivacyEnforcer, scan_exact_bytes
from yoetz.application.egress import PrivacyCoordinator
from yoetz.domain.privacy import (
    AuthorizationScope,
    AuthorizationScopeKind,
    CandidateContext,
    CandidateContextItem,
    ChannelPolicy,
    ClassifiedContext,
    DataClass,
    DisclosureProvenance,
    EgressChannel,
    ForbiddenDataKind,
    LocalDisclosureSink,
    PrivacyDecision,
    PrivacyOutcome,
    PrivacyPolicy,
    PrivacyProfile,
    ProjectionProvenanceContext,
    ReviewContextProfile,
    ReviewSelectionPolicy,
)
from yoetz.domain.values import Frontier
from yoetz.ports.privacy import EffectivePrivacyPolicy
from yoetz.protocol.models import DataCategory

_INSTALLATION = "ins_10000000-0000-4000-8000-000000000001"
_TASK = "tsk_10000000-0000-4000-8000-000000000002"
_OTHER_TASK = "tsk_10000000-0000-4000-8000-000000000007"
_REQUEST = "req_10000000-0000-4000-8000-000000000003"
_WORKSPACE = f"hmac-sha256:{'1' * 64}"
_DIGEST = f"sha256:{'2' * 64}"
_NOW = datetime(2026, 7, 19, tzinfo=UTC)
_SESSION = "ses_10000000-0000-4000-8000-000000000005"
_WRITER = "wri_10000000-0000-4000-8000-000000000006"
_FRONTIER = Frontier(3, f"sha256:{'3' * 64}")


def _scope() -> AuthorizationScope:
    return AuthorizationScope(
        AuthorizationScopeKind.TASK,
        _INSTALLATION,
        _WORKSPACE,
        _TASK,
    )


def _disabled(channel: EgressChannel) -> ChannelPolicy:
    return ChannelPolicy(
        channel,
        False,
        (),
        (),
        None,
        (),
        AuthorizationScopeKind.MACHINE,
        False,
        0,
        0,
        0,
    )


def _effective() -> EffectivePrivacyPolicy:
    policy = PrivacyPolicy(
        policy_id="pvy_10000000-0000-4000-8000-000000000004",
        version=1,
        policy_digest=_DIGEST,
        profile=PrivacyProfile.LOCAL_ONLY,
        review_context_profile=ReviewContextProfile.STRUCTURAL,
        review_selection=ReviewSelectionPolicy.for_profile(ReviewContextProfile.STRUCTURAL),
        require_current_provider_data_use_evidence=False,
        network_egress_permitted=False,
        effective_scope=_scope(),
        channel_policies=tuple(
            _disabled(channel) for channel in sorted(EgressChannel, key=lambda c: c.value)
        ),
        local_model_enabled=False,
        local_model_binding=None,
        local_model_categories=(),
        local_model_data_classes=(),
        agent_context_categories=(DataCategory.FINDING_SUMMARY,),
        agent_context_data_classes=(DataClass.ORDINARY_USER_CONTENT, DataClass.PUBLIC_STRUCTURAL),
        trusted_human_control_categories=tuple(DataCategory),
        trusted_human_control_data_classes=(
            DataClass.ORDINARY_USER_CONTENT,
            DataClass.PUBLIC_STRUCTURAL,
        ),
        created_at=_NOW,
    )
    return EffectivePrivacyPolicy(policy, 1, _DIGEST)


def test_exact_scanner_reuses_shared_sensitive_content_detectors() -> None:
    kinds = scan_exact_bytes(b"api_key=sk-proj-abcdefghijklmnopqrstuvwxyz012345")
    assert kinds == (ForbiddenDataKind.API_CREDENTIAL,)


def test_classification_is_scope_bound_and_source_never_send_is_absolute() -> None:
    candidate = CandidateContext(
        request_id=_REQUEST,
        channel=None,
        local_sink=LocalDisclosureSink.LOCAL_HUMAN_VIEW,
        purpose="client-result-projection",
        scope=_scope(),
        subject_digest=_DIGEST,
        provider_binding=None,
        items=(
            CandidateContextItem(
                "public",
                DataCategory.BOUNDED_STRUCTURAL_METADATA,
                _scope(),
                "event:summary",
                b"three findings",
            ),
            CandidateContextItem(
                "secret",
                DataCategory.DIAGNOSTIC_METADATA,
                _scope(),
                "environment:raw",
                b"innocent-looking",
            ),
        ),
    )

    classified = LocalPrivacyEnforcer().classify(candidate, _effective())

    assert classified.items[0].data_class is DataClass.PUBLIC_STRUCTURAL
    assert classified.items[0].scope_valid
    assert classified.items[1].data_class is DataClass.SECRET_OR_CRYPTOGRAPHIC
    assert classified.items[1].forbidden_findings == (ForbiddenDataKind.UNRELATED_ENVIRONMENT,)


def test_minimization_is_deterministic_and_cannot_include_forbidden_item() -> None:
    candidate = CandidateContext(
        request_id=_REQUEST,
        channel=None,
        local_sink=LocalDisclosureSink.LOCAL_HUMAN_VIEW,
        purpose="client-result-projection",
        scope=_scope(),
        subject_digest=_DIGEST,
        provider_binding=None,
        items=(
            CandidateContextItem(
                "allowed",
                DataCategory.FINDING_SUMMARY,
                _scope(),
                "finding:one",
                b"bounded finding",
            ),
            CandidateContextItem(
                "forbidden",
                DataCategory.FINDING_SUMMARY,
                _scope(),
                "credential:file",
                b"not itself a token",
            ),
        ),
    )
    enforcer = LocalPrivacyEnforcer()
    classified = enforcer.classify(candidate, _effective())
    decision = PrivacyDecision(
        ("allowed", "forbidden"),
        (),
        PrivacyOutcome.COMPLETED,
        None,
    )

    first = enforcer.minimize_and_scan(classified, decision)
    second = enforcer.minimize_and_scan(classified, decision)

    assert first == second
    assert first.included_item_ids == ("allowed",)
    assert b"not itself a token" not in first.prepared_bytes
    assert first.forbidden_findings == ()


class _Provenance:
    def __init__(self, facts: dict[str, DisclosureProvenance | None]) -> None:
        self._facts = facts

    def resolve(
        self,
        context: ProjectionProvenanceContext,
        candidate: CandidateContext,
        item: CandidateContextItem,
    ) -> DisclosureProvenance | None:
        assert context == _provenance_context()
        del candidate
        return self._facts[item.item_id]


def _provenance_context() -> ProjectionProvenanceContext:
    return ProjectionProvenanceContext(_SESSION, _WRITER, _FRONTIER)


def _decision(classified: ClassifiedContext) -> PrivacyDecision:
    coordinator = object.__new__(PrivacyCoordinator)
    return coordinator._local_decision(  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
        classified, _effective()
    )


def test_local_human_view_is_not_category_or_data_class_gated() -> None:
    candidate = CandidateContext(
        request_id=_REQUEST,
        channel=None,
        local_sink=LocalDisclosureSink.LOCAL_HUMAN_VIEW,
        purpose="client-result-projection",
        scope=_scope(),
        subject_digest=_DIGEST,
        provider_binding=None,
        items=(
            CandidateContextItem(
                "human-visible",
                DataCategory.REPOSITORY_EXCERPT,
                _scope(),
                "result:detail",
                b"private project detail",
            ),
        ),
    )
    classified = LocalPrivacyEnforcer().classify(candidate, _effective())

    decision = _decision(classified)

    assert decision.outcome is PrivacyOutcome.COMPLETED
    assert decision.approved_item_ids == ("human-visible",)


def test_agent_context_uses_trusted_provenance_and_ambiguity_denies_widening() -> None:
    candidate = CandidateContext(
        request_id=_REQUEST,
        channel=None,
        local_sink=LocalDisclosureSink.AGENT_CONTEXT,
        purpose="client-result-projection",
        scope=_scope(),
        subject_digest=_DIGEST,
        provider_binding=None,
        items=(
            CandidateContextItem(
                "self-authored",
                DataCategory.TASK_DESCRIPTION,
                _scope(),
                "event:self",
                b"the agent's own text",
            ),
            CandidateContextItem(
                "ambiguous",
                DataCategory.REPOSITORY_EXCERPT,
                _scope(),
                "result:unknown",
                b"unknown authorship",
            ),
            CandidateContextItem(
                "granted-other",
                DataCategory.FINDING_SUMMARY,
                _scope(),
                "finding:other",
                b"other writer finding",
            ),
        ),
        provenance_context=_provenance_context(),
    )
    resolver = _Provenance(
        {
            "self-authored": DisclosureProvenance.SELF_AUTHORED,
            "ambiguous": None,
            "granted-other": DisclosureProvenance.OTHER_WRITER,
        }
    )
    classified = LocalPrivacyEnforcer(provenance_resolver=resolver).classify(
        candidate, _effective()
    )

    decision = _decision(classified)

    assert decision.outcome is PrivacyOutcome.COMPLETED
    assert decision.approved_item_ids == ("granted-other", "self-authored")
    assert DataCategory.REPOSITORY_EXCERPT in decision.blocked_categories


def test_agent_self_authorship_never_widens_sensitive_data_class() -> None:
    candidate = CandidateContext(
        request_id=_REQUEST,
        channel=None,
        local_sink=LocalDisclosureSink.AGENT_CONTEXT,
        purpose="client-result-projection",
        scope=_scope(),
        subject_digest=_DIGEST,
        provider_binding=None,
        items=(
            CandidateContextItem(
                "sensitive-self",
                DataCategory.TASK_DESCRIPTION,
                _scope(),
                "event:self",
                b"sensitive value",
            ),
        ),
        provenance_context=_provenance_context(),
    )
    classified = LocalPrivacyEnforcer(
        provenance_resolver=_Provenance({"sensitive-self": DisclosureProvenance.SELF_AUTHORED})
    ).classify(candidate, _effective())
    sensitive = ClassifiedContext(
        classified.candidate,
        (replace(classified.items[0], data_class=DataClass.SENSITIVE_CONFIDENTIAL),),
    )

    decision = _decision(sensitive)

    assert decision.outcome is PrivacyOutcome.BLOCKED_BY_POLICY
    assert decision.approved_item_ids == ()


def test_missing_agent_provenance_context_is_ambiguous_and_grants_no_widening() -> None:
    candidate = CandidateContext(
        request_id=_REQUEST,
        channel=None,
        local_sink=LocalDisclosureSink.AGENT_CONTEXT,
        purpose="client-result-projection",
        scope=_scope(),
        subject_digest=_DIGEST,
        provider_binding=None,
        items=(
            CandidateContextItem(
                "ambiguous",
                DataCategory.REPOSITORY_EXCERPT,
                _scope(),
                "result:unknown",
                b"unknown authorship",
            ),
        ),
    )
    classified = LocalPrivacyEnforcer().classify(candidate, _effective())

    decision = _decision(classified)

    assert decision.outcome is PrivacyOutcome.BLOCKED_BY_POLICY
    assert decision.approved_item_ids == ()


def test_provenance_context_is_forbidden_for_non_agent_sink() -> None:
    with pytest.raises(ValueError, match="invalid_privacy_value"):
        CandidateContext(
            request_id=_REQUEST,
            channel=None,
            local_sink=LocalDisclosureSink.LOCAL_HUMAN_VIEW,
            purpose="client-result-projection",
            scope=_scope(),
            subject_digest=_DIGEST,
            provider_binding=None,
            items=(),
            provenance_context=_provenance_context(),
        )


def test_cross_task_item_denies_agent_provenance_widening() -> None:
    other_scope = AuthorizationScope(
        AuthorizationScopeKind.TASK,
        _INSTALLATION,
        _WORKSPACE,
        _OTHER_TASK,
    )
    candidate = CandidateContext(
        request_id=_REQUEST,
        channel=None,
        local_sink=LocalDisclosureSink.AGENT_CONTEXT,
        purpose="client-result-projection",
        scope=_scope(),
        subject_digest=_DIGEST,
        provider_binding=None,
        items=(
            CandidateContextItem(
                "cross-task",
                DataCategory.TASK_DESCRIPTION,
                other_scope,
                "event:other-task",
                b"text outside the projection task",
            ),
        ),
        provenance_context=_provenance_context(),
    )
    classified = LocalPrivacyEnforcer(
        provenance_resolver=_Provenance({"cross-task": DisclosureProvenance.SELF_AUTHORED})
    ).classify(candidate, _effective())

    decision = _decision(classified)

    assert decision.outcome is PrivacyOutcome.CLASSIFICATION_UNCERTAIN
    assert decision.approved_item_ids == ()


def test_candidate_origin_accepts_bounded_canonical_json_pointer() -> None:
    item = CandidateContextItem(
        "pointer-item",
        DataCategory.FINDING_SUMMARY,
        _scope(),
        "/findings/0/detail~1text",
        b"detail",
    )

    assert item.origin_ref == "/findings/0/detail~1text"


def test_empty_local_projection_is_an_approved_structural_decision() -> None:
    candidate = CandidateContext(
        request_id=_REQUEST,
        channel=None,
        local_sink=LocalDisclosureSink.AGENT_CONTEXT,
        purpose="client-result-projection",
        scope=_scope(),
        subject_digest=_DIGEST,
        provider_binding=None,
        items=(),
        provenance_context=_provenance_context(),
    )
    classified = LocalPrivacyEnforcer().classify(candidate, _effective())

    decision = _decision(classified)

    assert decision == PrivacyDecision((), (), PrivacyOutcome.COMPLETED, None)
