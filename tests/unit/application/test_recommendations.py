from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from yoetz.application.package_update import PackageUpdateAdvisory, build_package_update_advisory
from yoetz.application.recommendations import (
    RECOMMENDED_DEFAULTS,
    RecommendationContext,
    RecommendationStoreError,
    cached_pending_recommendations,
    decline_cached_recommendation,
    evaluate_recommendation_context,
    load_recommendation_state,
    record_recommendation_decision,
    refresh_pending,
)

_NOW = datetime(2026, 8, 12, 9, 30, tzinfo=UTC)


def _newer() -> PackageUpdateAdvisory:
    return build_package_update_advisory(
        installed_version="0.1.0", latest_version="0.2.0", source="cache"
    )


@pytest.mark.anyio
async def test_registry_evaluates_all_three_recommended_defaults(tmp_path: Path) -> None:
    state = await refresh_pending(
        context=RecommendationContext(
            observation_enabled=False,
            codex_activation_state="installed_not_activated",
            package_update=_newer(),
        ),
        root=tmp_path,
        version="0.1.0",
    )

    assert state.pending == tuple(item.id for item in RECOMMENDED_DEFAULTS)
    assert [item.id for item in cached_pending_recommendations(root=tmp_path)] == list(
        state.pending
    )
    store = tmp_path / "recommendations.json"
    assert stat.S_IMODE(store.stat().st_mode) == 0o600


@pytest.mark.anyio
async def test_decline_is_remembered_across_release_frontiers(tmp_path: Path) -> None:
    context = RecommendationContext(
        observation_enabled=True,
        codex_activation_state="installed_not_activated",
    )
    await refresh_pending(context=context, root=tmp_path, version="0.1.0")
    declined = record_recommendation_decision(
        "codex-plugin-activation",
        "declined",
        root=tmp_path,
        version="0.1.0",
        now=_NOW,
    )
    assert declined.pending == ()

    reevaluated = await refresh_pending(context=context, root=tmp_path, version="0.2.0")

    assert reevaluated.pending == ()
    assert reevaluated.decisions["codex-plugin-activation"].decision == "declined"


@pytest.mark.anyio
async def test_acceptance_suppresses_only_the_same_release_frontier(tmp_path: Path) -> None:
    context = RecommendationContext(observation_enabled=False)
    await refresh_pending(context=context, root=tmp_path, version="0.1.0")
    record_recommendation_decision(
        "observation-enabled", "accepted", root=tmp_path, version="0.1.0", now=_NOW
    )

    same = await refresh_pending(context=context, root=tmp_path, version="0.1.0")
    changed = await refresh_pending(context=context, root=tmp_path, version="0.2.0")

    assert "observation-enabled" not in same.pending
    assert "observation-enabled" in changed.pending


@pytest.mark.anyio
async def test_empty_cache_recomputes_only_when_version_changes(tmp_path: Path) -> None:
    satisfied = RecommendationContext(observation_enabled=True, codex_activation_state="active")
    first = await refresh_pending(context=satisfied, root=tmp_path, version="0.1.0")
    assert first.pending == ()

    stale_same_version = await refresh_pending(
        context=RecommendationContext(observation_enabled=False),
        root=tmp_path,
        version="0.1.0",
    )
    changed_version = await refresh_pending(
        context=RecommendationContext(observation_enabled=False),
        root=tmp_path,
        version="0.2.0",
    )

    assert stale_same_version.pending == ()
    assert changed_version.pending == ("observation-enabled",)


@pytest.mark.anyio
async def test_pending_cache_is_reconciled_on_every_heavy_touchpoint(tmp_path: Path) -> None:
    await refresh_pending(
        context=RecommendationContext(observation_enabled=False),
        root=tmp_path,
        version="0.1.0",
    )

    reconciled = await refresh_pending(
        context=RecommendationContext(observation_enabled=True),
        root=tmp_path,
        version="0.1.0",
    )

    assert reconciled.pending == ()


@pytest.mark.anyio
async def test_refresh_rereads_decisions_under_lock_before_writing(tmp_path: Path) -> None:
    await refresh_pending(
        context=RecommendationContext(observation_enabled=False),
        root=tmp_path,
        version="0.1.0",
    )

    async def concurrent_decline() -> RecommendationContext:
        record_recommendation_decision(
            "observation-enabled",
            "declined",
            root=tmp_path,
            version="0.1.0",
            now=_NOW,
        )
        return RecommendationContext(observation_enabled=False)

    refreshed = await refresh_pending(
        context_factory=concurrent_decline,
        root=tmp_path,
        version="0.1.0",
        force=True,
    )

    assert refreshed.pending == ()
    assert refreshed.decisions["observation-enabled"].decision == "declined"


def test_corrupt_or_oversized_state_is_not_replaced(tmp_path: Path) -> None:
    store = tmp_path / "recommendations.json"
    store.write_text('{"schema":"wrong"}', encoding="utf-8")
    store.chmod(0o600)
    original = store.read_bytes()

    with pytest.raises(RecommendationStoreError, match="recommendation_store_corrupt"):
        load_recommendation_state(root=tmp_path)
    assert store.read_bytes() == original

    store.write_bytes(b"x" * (32 * 1024 + 1))
    store.chmod(0o600)
    with pytest.raises(RecommendationStoreError, match="recommendation_store_corrupt"):
        load_recommendation_state(root=tmp_path)


def test_store_parser_rejects_unknown_fields(tmp_path: Path) -> None:
    document: dict[str, object] = {
        "schema": "yoetz.recommendations/1",
        "last_evaluated_version": "0.1.0",
        "decisions": {},
        "pending": [],
        "unexpected": True,
    }
    store = tmp_path / "recommendations.json"
    store.write_text(json.dumps(document), encoding="utf-8")
    store.chmod(0o600)

    with pytest.raises(RecommendationStoreError, match="recommendation_store_corrupt"):
        load_recommendation_state(root=tmp_path)


def test_store_rejects_world_readable_or_symlink_leaf(tmp_path: Path) -> None:
    store = tmp_path / "recommendations.json"
    store.write_text("{}", encoding="utf-8")
    store.chmod(0o644)
    with pytest.raises(RecommendationStoreError, match="recommendation_store_unsafe"):
        load_recommendation_state(root=tmp_path)

    store.unlink()
    target = tmp_path / "foreign.json"
    target.write_text("{}", encoding="utf-8")
    target.chmod(0o600)
    os.symlink(target, store)
    with pytest.raises(RecommendationStoreError, match="recommendation_store_unsafe"):
        load_recommendation_state(root=tmp_path)


def _skipped_policy() -> PackageUpdateAdvisory:
    return build_package_update_advisory(
        installed_version="0.1.0",
        latest_version=None,
        source="none",
        outcome="skipped_policy",
    )


@pytest.mark.anyio
async def test_policy_skipped_advisory_is_unknown_and_retains_pending(tmp_path: Path) -> None:
    established = await refresh_pending(
        context=RecommendationContext(package_update=_newer()),
        root=tmp_path,
        version="0.1.0",
    )
    assert "package-update" in established.pending

    retained = await refresh_pending(
        context=RecommendationContext(package_update=_skipped_policy()),
        root=tmp_path,
        version="0.1.0",
        force=True,
    )

    assert "package-update" in retained.pending


@pytest.mark.anyio
async def test_policy_skipped_advisory_never_creates_pending(tmp_path: Path) -> None:
    state = await refresh_pending(
        context=RecommendationContext(package_update=_skipped_policy()),
        root=tmp_path,
        version="0.1.0",
    )

    assert "package-update" not in state.pending


@pytest.mark.anyio
async def test_performed_up_to_date_advisory_clears_pending(tmp_path: Path) -> None:
    await refresh_pending(
        context=RecommendationContext(package_update=_newer()),
        root=tmp_path,
        version="0.1.0",
    )

    up_to_date = build_package_update_advisory(
        installed_version="0.1.0", latest_version="0.1.0", source="network"
    )
    cleared = await refresh_pending(
        context=RecommendationContext(package_update=up_to_date),
        root=tmp_path,
        version="0.1.0",
    )

    assert up_to_date.outcome == "up_to_date"
    assert "package-update" not in cleared.pending


@pytest.mark.anyio
async def test_decline_cached_declines_pending_without_context(tmp_path: Path) -> None:
    await refresh_pending(
        context=RecommendationContext(package_update=_newer()),
        root=tmp_path,
        version="0.1.0",
    )

    declined = decline_cached_recommendation(
        "package-update", root=tmp_path, version="0.1.0", now=_NOW
    )

    assert "package-update" not in declined.pending
    assert declined.decisions["package-update"].decision == "declined"


def test_decline_cached_requires_cached_pending(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="recommendation_not_pending"):
        decline_cached_recommendation("package-update", root=tmp_path, version="0.1.0", now=_NOW)

    with pytest.raises(ValueError, match="recommendation_unknown"):
        decline_cached_recommendation("not-real", root=tmp_path, version="0.1.0", now=_NOW)


@pytest.mark.anyio
async def test_context_resolution_reuses_package_update_advisory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    async def fake_resolve(**kwargs: object) -> PackageUpdateAdvisory:
        calls.append(kwargs)
        return _newer()

    monkeypatch.setattr(
        "yoetz.application.recommendations.resolve_package_update_advisory", fake_resolve
    )

    context = await evaluate_recommendation_context(
        observation_enabled=False,
        codex_activation_state="active",
        network_egress_permitted=True,
        update_checks_enabled=True,
        allow_network=False,
    )

    assert context.package_update == _newer()
    assert calls == [
        {
            "policy": None,
            "network_egress_permitted": True,
            "update_checks_enabled": True,
            "allow_network": False,
        }
    ]
