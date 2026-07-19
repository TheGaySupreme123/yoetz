from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from yoetz.domain.privacy import (
    NEVER_SEND_KINDS,
    AuthorizationScope,
    AuthorizationScopeKind,
    ChannelPolicy,
    DataClass,
    EgressChannel,
    ForbiddenDataKind,
    PrivacyOutcome,
    PrivacyPolicy,
    PrivacyProfile,
    PrivacyReason,
    ReviewContextProfile,
    ReviewSelectionPolicy,
    outcome_reason_is_valid,
)
from yoetz.ports.privacy import PolicyTransitionProposal
from yoetz.protocol.models import DataCategory

_HERE = Path(__file__).resolve()
_ROOT = _HERE.parents[3]
_DIGEST = "sha256:" + "1" * 64
_POLICY_ID = "pvy_88888888-8888-4888-8888-888888888888"
_INSTALLATION_ID = "ins_08000000-0000-4000-8000-000000000001"
_NOW = datetime(2026, 3, 8, tzinfo=UTC)


def _disabled(channel: EgressChannel) -> ChannelPolicy:
    return ChannelPolicy(
        channel=channel,
        enabled=False,
        allowed_categories=(),
        allowed_data_classes=(),
        provider_binding=None,
        allowed_purposes=(),
        scope_ceiling=AuthorizationScopeKind.MACHINE,
        preview_required=False,
        max_bytes=0,
        max_tokens=0,
        authorization_ttl_seconds=0,
    )


def _local_only_policy(*, enabled_channel: EgressChannel | None = None) -> PrivacyPolicy:
    policies = {channel: _disabled(channel) for channel in EgressChannel}
    if enabled_channel is not None:
        policies[enabled_channel] = ChannelPolicy(
            channel=enabled_channel,
            enabled=True,
            allowed_categories=(DataCategory.BOUNDED_STRUCTURAL_METADATA,),
            allowed_data_classes=(DataClass.PUBLIC_STRUCTURAL,),
            provider_binding=None,
            allowed_purposes=("capability-testing-probe",),
            scope_ceiling=AuthorizationScopeKind.MACHINE,
            preview_required=False,
            max_bytes=4096,
            max_tokens=1024,
            authorization_ttl_seconds=60,
        )
    return PrivacyPolicy(
        policy_id=_POLICY_ID,
        version=3,
        policy_digest=_DIGEST,
        profile=PrivacyProfile.LOCAL_ONLY,
        review_context_profile=ReviewContextProfile.STRUCTURAL,
        review_selection=ReviewSelectionPolicy.for_profile(ReviewContextProfile.STRUCTURAL),
        require_current_provider_data_use_evidence=False,
        network_egress_permitted=enabled_channel is not None,
        effective_scope=AuthorizationScope(AuthorizationScopeKind.MACHINE, _INSTALLATION_ID),
        channel_policies=tuple(
            policies[channel] for channel in sorted(EgressChannel, key=lambda item: item.value)
        ),
        local_model_enabled=False,
        local_model_binding=None,
        local_model_categories=(),
        local_model_data_classes=(),
        agent_context_categories=(
            DataCategory.BOUNDED_STRUCTURAL_METADATA,
            DataCategory.DECLARED_FILE_TYPE,
        ),
        agent_context_data_classes=(DataClass.PUBLIC_STRUCTURAL,),
        trusted_human_control_categories=tuple(DataCategory),
        trusted_human_control_data_classes=(
            DataClass.ORDINARY_USER_CONTENT,
            DataClass.PUBLIC_STRUCTURAL,
            DataClass.SENSITIVE_CONFIDENTIAL,
        ),
        created_at=_NOW,
    )


def test_closed_privacy_vocabularies_and_never_send_set() -> None:
    assert NEVER_SEND_KINDS == frozenset(ForbiddenDataKind)
    assert {profile.value for profile in PrivacyProfile} == {
        "local_only",
        "confirm_every_request",
        "minimal_external",
        "trusted_provider",
    }
    assert {channel.value for channel in EgressChannel} == {
        "llm_inference",
        "product_telemetry",
        "crash_diagnostics",
        "update_checks",
        "capability_testing",
    }


def test_named_review_selectors_are_exact_and_orthogonal() -> None:
    structural = ReviewSelectionPolicy.for_profile(ReviewContextProfile.STRUCTURAL)
    assisted = ReviewSelectionPolicy.for_profile(ReviewContextProfile.ASSISTED)
    expanded = ReviewSelectionPolicy.for_profile(ReviewContextProfile.EXPANDED)

    assert structural.excerpt_kinds == ()
    assert not structural.include_finding_prose
    assert assisted.include_finding_prose and not assisted.include_exact_command_text
    assert expanded.include_exact_command_text
    assert structural.meet(expanded) == structural


def test_scope_preserves_and_checks_the_complete_ancestor_chain() -> None:
    workspace = AuthorizationScope(
        AuthorizationScopeKind.WORKSPACE,
        _INSTALLATION_ID,
        "hmac-sha256:" + "2" * 64,
    )
    task = AuthorizationScope(
        AuthorizationScopeKind.TASK,
        _INSTALLATION_ID,
        "hmac-sha256:" + "2" * 64,
        "tsk_08000000-0000-4000-8000-000000000002",
    )
    assert workspace.contains(task)
    with pytest.raises(ValueError, match="invalid_privacy_value"):
        AuthorizationScope(
            AuthorizationScopeKind.MACHINE, _INSTALLATION_ID, "hmac-sha256:" + "2" * 64
        )


def test_terminal_outcome_reason_matrix_is_closed() -> None:
    assert outcome_reason_is_valid(PrivacyOutcome.COMPLETED, None)
    assert not outcome_reason_is_valid(PrivacyOutcome.COMPLETED, PrivacyReason.STALE)
    assert outcome_reason_is_valid(
        PrivacyOutcome.CHANNEL_UNAVAILABLE, PrivacyReason.CHANNEL_UNAVAILABLE
    )
    assert not outcome_reason_is_valid(
        PrivacyOutcome.CHANNEL_UNAVAILABLE, PrivacyReason.TRANSPORT_FAILED
    )


def test_p0_4_non_llm_enablement_is_rejected_before_pending_consent() -> None:
    forced_policy = _local_only_policy(enabled_channel=EgressChannel.CAPABILITY_TESTING)
    fixture = json.loads(
        (_ROOT / "fixtures/privacy/PRIV-008-independent-channels.case.json").read_text()
    )
    expected = fixture["expected"]["unsupported_channels"]["capability_testing"]

    assert forced_policy.unsupported_enabled_channels == (EgressChannel.CAPABILITY_TESTING,)
    assert expected["outcome"] == "channel_unavailable"
    assert expected["policy_transition_committed"] is False
    assert expected["network_attempts"] == 0
    with pytest.raises(ValueError, match="channel_unavailable"):
        PolicyTransitionProposal(
            scope=forced_policy.effective_scope,
            expected_generation=2,
            proposed_policy=forced_policy,
            proposal_digest="sha256:" + "3" * 64,
            created_at=_NOW,
            expires_at=datetime(2026, 3, 8, 0, 1, tzinfo=UTC),
        )
