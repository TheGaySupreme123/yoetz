"""Default LOCAL_ONLY agent-context allowlist includes verification projection content."""

from __future__ import annotations

from datetime import UTC, datetime

from yoetz.domain.privacy import DataCategory, DataClass
from yoetz.service import ready_composition as ready_composition_module


def test_default_local_only_allows_receipt_projection_categories() -> None:
    policy = ready_composition_module._denied_policy(  # pyright: ignore[reportPrivateUsage]
        installation_id="ins_00000000-0000-4000-8000-000000000001",
        policy_id="pvy_00000000-0000-4000-8000-000000000001",
        policy_digest="sha256:" + "0" * 64,
        created_at=datetime(2026, 7, 24, tzinfo=UTC),
    )
    assert DataCategory.FINDING_SUMMARY in policy.agent_context_categories
    assert DataCategory.OBLIGATION_TEXT in policy.agent_context_categories
    assert DataCategory.BOUNDED_STRUCTURAL_METADATA in policy.agent_context_categories
    assert DataClass.ORDINARY_USER_CONTENT in policy.agent_context_data_classes
    assert DataClass.PUBLIC_STRUCTURAL in policy.agent_context_data_classes
    # Observation-derived / vault-adjacent categories stay off the default allowlist.
    assert DataCategory.REPOSITORY_EXCERPT not in policy.agent_context_categories
    assert DataCategory.TRANSCRIPT_EXCERPT not in policy.agent_context_categories
