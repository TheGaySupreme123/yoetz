"""Approved bounded live semantic-provider capability probe (E-007).

Marked ``live_provider`` / ``live``. Written for the release capability job; excluded from the
default gate. Credentials must never enter this process — the cell exercises only an opaque
service-owned configured profile after an operator-provisioned ready ceremony.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests.capability.evidence import (
    CapabilityCase,
    EvidenceOutcome,
    Observation,
    bytes_digest,
    live_provider_authorized,
    record_and_write,
    runtime_capability_context,
)

from yoetz.adapters.providers.openai_responses import (
    OPENAI_MAX_OUTPUT_TOKENS,
    OPENAI_MAX_RESPONSE_BODY_BYTES,
    OpenAIProfile,
)
from yoetz.domain.privacy import ProviderDataUseProfile
from yoetz.protocol.canonical import canonical_digest

_TEST_REVISION = bytes_digest(Path(__file__).read_bytes())
_DIGEST = "sha256:" + "e" * 64
_NOW = datetime(2026, 7, 20, tzinfo=UTC)

_CASE_LIVE = CapabilityCase(
    case_id="PROV-LIVE-001",
    requirement_id="ADR-006.provider-live",
    claim_id="E-007.provider-profile-live",
    capability_family="provider_profile_live",
    required_observation_codes=frozenset({"live_authorized"}),
    allowed_observation_codes=frozenset(
        {
            "live_authorized",
            "credential_absent_from_process",
            "caps_bound",
            "data_use_eligibility_bound",
            "service_ready",
            "synthetic_case_bound",
        }
    ),
)


def _data_use_profile() -> ProviderDataUseProfile:
    return ProviderDataUseProfile(
        data_use_profile_id="openai-data-use",
        data_use_profile_version="1.0.0",
        customer_content_training="prohibited",
        retention="bounded",
        retention_days_ceiling=30,
        provider_human_access="restricted",
        reviewed_at=_NOW - timedelta(days=1),
        expires_at=_NOW + timedelta(days=30),
        evidence_digest=_DIGEST,
    )


@pytest.mark.live
@pytest.mark.live_provider
def test_live_provider_profile_requires_authorized_ready_service(tmp_path: Path) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir(mode=0o700)

    profile = OpenAIProfile(
        provider_id="openai",
        model="gpt-5",
        endpoint_profile_id="openai-responses",
        endpoint_profile_version="1.0.0",
        timeout_seconds=60,
        supports_structured_outputs=True,
        data_use_profile=_data_use_profile(),
    )
    assert profile.host == "api.openai.com"
    assert profile.port == 443
    assert profile.path == "/v1/responses"
    assert OPENAI_MAX_OUTPUT_TOKENS == 2_048
    assert OPENAI_MAX_RESPONSE_BODY_BYTES == 1_048_576
    assert profile.data_use_profile.recommendation_eligible(_NOW)

    # Spec: credential never enters/is consumed by this process. The cell only accepts an opaque
    # ready-service ceremony (see YOETZ_LIVE_SERVICE_READY / YOETZ_LIVE_PROVIDER_PROFILE_ID).

    context = runtime_capability_context(
        fixture_digest=bytes_digest(b"provider-profile-live-e007"),
        test_revision=_TEST_REVISION,
        config_profile_digest=canonical_digest(
            {
                "endpoint": profile.endpoint_profile_id,
                "model": profile.model,
                "provider": profile.provider_id,
                "version": profile.endpoint_profile_version,
            }
        ),
        external_tool="openai",
        external_version="absent",
        integration_channel="llm_inference",
        provider_id=profile.provider_id,
    )

    if not live_provider_authorized():
        evidence = record_and_write(
            _CASE_LIVE,
            context,
            (
                Observation("credential_absent_from_process", boolean_value=True),
                Observation("live_authorized", boolean_value=False),
            ),
            EvidenceOutcome.UNSUPPORTED,
            ("live_provider_not_authorized",),
            output_root=evidence_root,
        )
        assert evidence.outcome is EvidenceOutcome.UNSUPPORTED
        return

    # Ready isolated service + opaque configured profile are operator-provisioned outside this
    # process. Until that ceremony is present, the cell remains unsupported rather than bypassed.
    service_ready = os.environ.get("YOETZ_LIVE_SERVICE_READY") == "1"
    configured_profile = os.environ.get("YOETZ_LIVE_PROVIDER_PROFILE_ID", "").strip()
    if not service_ready or not configured_profile:
        evidence = record_and_write(
            _CASE_LIVE,
            context,
            (
                Observation("caps_bound", boolean_value=True),
                Observation("credential_absent_from_process", boolean_value=True),
                Observation("data_use_eligibility_bound", boolean_value=True),
                Observation("live_authorized", boolean_value=True),
                Observation("service_ready", boolean_value=False),
                Observation("synthetic_case_bound", boolean_value=True),
            ),
            EvidenceOutcome.UNSUPPORTED,
            ("ready_service_ceremony_unavailable",),
            output_root=evidence_root,
        )
        assert evidence.outcome is EvidenceOutcome.UNSUPPORTED
        return

    pytest.fail(
        "live provider dispatch path requires a wired ready-service client; "
        "ceremony flags were set but no service client is available in this revision"
    )
