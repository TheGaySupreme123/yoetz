"""privacy_* support-handler results against the frozen control-result envelope.

Every one of these bodies leaves the process through ``validate_result``. Encoding them from
domain dataclasses by reflection silently drifts from the reviewed schema — field names,
decimal-string counters, and closed ``additionalProperties`` are all load-bearing — so each
encoder is asserted against the real envelope rather than against a hand-copied shape.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from builders.privacy_policies import (
    INSTALLATION_ID,
    POLICY_DIGEST,
    POLICY_ID,
    local_only_policy,
    machine_scope,
    minimal_external_policy,
)
from yoetz.application.privacy_control import (
    _encode_decision_required,  # pyright: ignore[reportPrivateUsage]
    encode_effective_privacy_policy,
    encode_privacy_policy_result,
)
from yoetz.application.privacy_policy import (
    PolicyDecisionRequired,
    PrivacyPolicyResult,
    privacy_widening_summary,
)
from yoetz.domain.values import JsonObject, freeze_json
from yoetz.ports.control import ControlMethod, ControlResult
from yoetz.ports.privacy import (
    EffectivePrivacyPolicy,
    PolicyTransitionProposal,
    PreparedPolicyTransition,
    ProviderReconciliation,
)
from yoetz.protocol.canonical import JsonValue
from yoetz.protocol.models import DataCategory
from yoetz.service.control_protocol import ControlProtocolError, validate_result

_NOW = datetime(2026, 7, 25, tzinfo=UTC)
_RPC_ID = "rpc_40000000-0000-4000-8000-000000000001"
_SERVICE_INSTANCE_ID = "svc_40000000-0000-4000-8000-000000000002"
_RECEIPT_ID = "egr_40000000-0000-4000-8000-000000000003"
_PROPOSAL_ID = "ppr_40000000-0000-4000-8000-000000000004"
_PREPARED_DIGEST = f"sha256:{'c' * 64}"
_DIFF_DIGEST = f"sha256:{'d' * 64}"
_BINDING_DIGEST = f"sha256:{'e' * 64}"
_COMMITMENT = f"hmac-sha256:{'f' * 64}"


def _projection() -> dict[str, JsonValue]:
    """The structural projection the control layer attaches after the handler returns."""

    return {
        "sink": "agent_context",
        "local_disclosure_receipt_id": _RECEIPT_ID,
        "policy_id": POLICY_ID,
        "policy_version": "1",
        "policy_digest": POLICY_DIGEST,
        "included_categories": [],
        "blocked_categories": [],
        "omitted_pointers": [],
        "projection_commitment": _COMMITMENT,
    }


def _projected(body: JsonObject) -> JsonObject:
    return JsonObject({**dict(body), "privacy_projection": _projection()})


def _validate(method: ControlMethod, body: JsonObject) -> None:
    validate_result(
        ControlResult(
            protocol_version="1.0",
            rpc_id=_RPC_ID,
            service_instance_id=_SERVICE_INSTANCE_ID,
            service_generation="1",
            method=method,
            outcome="ok",
            body=_projected(body),
        )
    )


def _prepared(expires_in_seconds: int = 300) -> PreparedPolicyTransition:
    proposal = PolicyTransitionProposal(
        scope=machine_scope(),
        expected_generation=1,
        proposed_policy=minimal_external_policy(),
        proposal_digest=_DIFF_DIGEST,
        created_at=_NOW,
        expires_at=_NOW + timedelta(seconds=expires_in_seconds),
        privacy_proposal_id=_PROPOSAL_ID,
        expected_policy_digest=POLICY_DIGEST,
    )
    return PreparedPolicyTransition(proposal, _PREPARED_DIGEST, _DIFF_DIGEST, True)


def test_effective_policy_body_matches_control_result_envelope() -> None:
    effective = EffectivePrivacyPolicy(local_only_policy(), 1, POLICY_DIGEST)
    body = encode_effective_privacy_policy(effective)
    _validate(ControlMethod.PRIVACY_GET_EFFECTIVE, body)
    assert body["schema_version"] == "1.0.0"


@pytest.mark.parametrize(
    "method", [ControlMethod.PRIVACY_TIGHTEN_POLICY, ControlMethod.PRIVACY_PROPOSE_POLICY]
)
def test_tightening_body_matches_control_result_envelope(method: ControlMethod) -> None:
    result = PrivacyPolicyResult(
        minimal_external_policy(),
        2,
        3,
        1,
        ProviderReconciliation(2, 1, 0, ((_BINDING_DIGEST, "binding_unavailable"),)),
    )
    body = encode_privacy_policy_result(result)
    _validate(method, body)


def test_reconciliation_uses_wire_names_not_domain_field_names() -> None:
    """The domain type says ``policy_generation``/``unavailable_bindings``; the wire does not.

    The domain also pairs each digest with an internal reason string that the reviewed schema
    deliberately excludes, so the encoded form must carry digests only.
    """

    result = PrivacyPolicyResult(
        minimal_external_policy(),
        2,
        0,
        0,
        ProviderReconciliation(2, 0, 0, ((_BINDING_DIGEST, "binding_unavailable"),)),
    )
    reconciliation = encode_privacy_policy_result(result)["provider_reconciliation"]
    assert reconciliation == freeze_json(
        {
            "policy_version": "2",
            "activated_count": 0,
            "deactivated_count": 0,
            "unavailable_binding_digests": [_BINDING_DIGEST],
        }
    )


def test_domain_shaped_reconciliation_is_rejected_by_the_envelope() -> None:
    """Guards the encoder against being replaced by a generic dataclass reflector.

    A reflected body looks plausible and passes every in-process type check, but the frozen
    envelope rejects it — so this asserts the failure mode is real rather than theoretical.
    """

    result = PrivacyPolicyResult(
        minimal_external_policy(), 2, 0, 0, ProviderReconciliation(2, 0, 0, ())
    )
    reflected = JsonObject(
        {
            **dict(encode_privacy_policy_result(result)),
            "provider_reconciliation": {
                "policy_generation": 2,
                "activated_count": 0,
                "deactivated_count": 0,
                "unavailable_bindings": [],
            },
        }
    )
    with pytest.raises(ControlProtocolError):
        _validate(ControlMethod.PRIVACY_TIGHTEN_POLICY, reflected)


def test_decision_required_body_matches_control_result_envelope() -> None:
    required = PolicyDecisionRequired(_prepared(), _PROPOSAL_ID)
    body = _encode_decision_required(required, expected_policy_version=1)
    _validate(ControlMethod.PRIVACY_PROPOSE_POLICY, body)
    assert body["outcome"] == "decision_required"
    assert body["expected_policy_version"] == "1"


def test_effective_policy_body_carries_the_requested_installation() -> None:
    effective = EffectivePrivacyPolicy(local_only_policy(), 1, POLICY_DIGEST)
    policy = encode_effective_privacy_policy(effective)["policy"]
    assert type(policy) is JsonObject
    assert policy["effective_scope"] == freeze_json(
        {"kind": "machine", "installation_id": INSTALLATION_ID}
    )


def test_widening_summary_covers_local_sinks_and_scope_ceilings() -> None:
    """The preview a human approves must name every widening, not just channel categories.

    An enabled-channel-only diff reports an empty summary for a widen that only raises a
    local-sink ceiling, so the human is asked to approve a change they were never shown.
    """

    base = local_only_policy()
    candidate = minimal_external_policy()

    categories, scopes = privacy_widening_summary(base, candidate)

    # llm_inference goes from disabled to enabled, so all five of its categories are new.
    assert "claim_text" in categories
    assert "repository_excerpt" in categories
    # Enabling a channel is itself a scope grant, reported at the channel's ceiling.
    assert scopes == ("task",)


def test_widening_summary_is_empty_when_nothing_widened() -> None:
    policy = minimal_external_policy()

    assert privacy_widening_summary(policy, policy) == ((), ())


def test_widening_summary_reports_a_raised_local_sink_ceiling() -> None:
    base = local_only_policy()
    widened = replace(
        base,
        local_model_categories=(*base.local_model_categories, DataCategory.CLAIM_TEXT),
    )

    categories, scopes = privacy_widening_summary(base, widened)

    assert categories == ("claim_text",)
    assert scopes == ()
