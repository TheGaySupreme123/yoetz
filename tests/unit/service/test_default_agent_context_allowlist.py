"""Default LOCAL_ONLY agent-context allowlist includes verification projection content."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from yoetz.domain.privacy import AuthorizationScope, DataCategory, DataClass, PrivacyPolicy
from yoetz.service import ready_composition as ready_composition_module

_INSTALLATION = "ins_00000000-0000-4000-8000-000000000001"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _current_default() -> PrivacyPolicy:
    return ready_composition_module._denied_policy(  # pyright: ignore[reportPrivateUsage]
        installation_id=_INSTALLATION,
        policy_id="pvy_00000000-0000-4000-8000-000000000001",
        policy_digest="sha256:" + "0" * 64,
        created_at=datetime(2026, 7, 24, tzinfo=UTC),
    )


def _legacy_default() -> PrivacyPolicy:
    return replace(
        _current_default(),
        agent_context_categories=(
            DataCategory.BOUNDED_STRUCTURAL_METADATA,
            DataCategory.DECLARED_FILE_TYPE,
        ),
        agent_context_data_classes=(DataClass.PUBLIC_STRUCTURAL,),
    )


class _RecordingStore:
    """Captures the re-seed call so the upgrade decision is observable without a database."""

    def __init__(self, stored: PrivacyPolicy) -> None:
        self.stored = stored
        self.calls: list[tuple[PrivacyPolicy, PrivacyPolicy]] = []

    async def reseed_untouched_bootstrap_default(
        self,
        scope: AuthorizationScope,
        *,
        expected_current: PrivacyPolicy,
        replacement: PrivacyPolicy,
    ) -> PrivacyPolicy:
        del scope
        self.calls.append((expected_current, replacement))
        return replacement


def test_default_local_only_allows_receipt_projection_categories() -> None:
    policy = _current_default()
    assert DataCategory.FINDING_SUMMARY in policy.agent_context_categories
    assert DataCategory.OBLIGATION_TEXT in policy.agent_context_categories
    assert DataCategory.BOUNDED_STRUCTURAL_METADATA in policy.agent_context_categories
    assert DataClass.ORDINARY_USER_CONTENT in policy.agent_context_data_classes
    assert DataClass.PUBLIC_STRUCTURAL in policy.agent_context_data_classes
    # Observation-derived / vault-adjacent categories stay off the default allowlist.
    assert DataCategory.REPOSITORY_EXCERPT not in policy.agent_context_categories
    assert DataCategory.TRANSCRIPT_EXCERPT not in policy.agent_context_categories


@pytest.mark.anyio
async def test_untouched_legacy_default_is_carried_forward() -> None:
    """An installation seeded before the widening must not keep unreadable receipts forever."""

    stored = _legacy_default()
    store = _RecordingStore(stored)
    scope = stored.effective_scope

    result = await ready_composition_module._reseed_untouched_default_policy(  # pyright: ignore[reportPrivateUsage]
        store,  # pyright: ignore[reportArgumentType]
        scope,
        stored,
    )

    assert len(store.calls) == 1
    expected_current, replacement = store.calls[0]
    assert expected_current == stored
    assert result == replacement
    assert DataCategory.FINDING_SUMMARY in result.agent_context_categories
    assert DataClass.ORDINARY_USER_CONTENT in result.agent_context_data_classes
    # A superseding version is written, never an in-place rewrite of the recorded policy.
    assert replacement.version == stored.version + 1


@pytest.mark.anyio
async def test_owner_edited_policy_is_never_reseeded() -> None:
    """Any owner edit must survive upgrade; only the exact untouched old default is replaced."""

    edited = replace(
        _legacy_default(),
        agent_context_categories=(DataCategory.BOUNDED_STRUCTURAL_METADATA,),
    )
    store = _RecordingStore(edited)

    result = await ready_composition_module._reseed_untouched_default_policy(  # pyright: ignore[reportPrivateUsage]
        store,  # pyright: ignore[reportArgumentType]
        edited.effective_scope,
        edited,
    )

    assert store.calls == []
    assert result is edited


@pytest.mark.anyio
async def test_current_default_is_left_alone() -> None:
    """A policy already carrying the current default needs no write on every ready build."""

    stored = _current_default()
    store = _RecordingStore(stored)

    result = await ready_composition_module._reseed_untouched_default_policy(  # pyright: ignore[reportPrivateUsage]
        store,  # pyright: ignore[reportArgumentType]
        stored.effective_scope,
        stored,
    )

    assert store.calls == []
    assert result is stored
