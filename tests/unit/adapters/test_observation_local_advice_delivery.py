"""Hook-channel advice delivery cadence and dedup identity (#241).

The 2026-08-13 dogfood session received the same ``provider_not_ready``
developer message 29 times, byte-identical, over 24 minutes: the delivery gate
keyed on ``suppression_identity``, which folds in a digest over every retained
envelope and therefore churns on every tool call while the rendered text cannot
change. These tests pin the content-keyed replacement and the standing-advice
cadence.
"""

from __future__ import annotations

import io
import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest

from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.application.observation_advice import (
    ObservationAdviceBuildInput,
    build_observation_advice_snapshot,
)
from yoetz.cli.observe_hooks import handle_observe
from yoetz.domain.observation import (
    AdviceSnapshot,
    ObservationCursor,
    ObservationEnvelope,
    ObservationLifecycle,
    ObservationSource,
)
from yoetz.domain.values import JsonObject, Timestamp
from yoetz.kernel.policies.observation_advice import ObservationCompositionFact

_TIME = Timestamp("2026-08-13T14:00:00.000Z")
_STANDING = ObservationCompositionFact(
    semantic_configured=True,
    semantic_ready=False,
    provider_factory_ids=("fireworks",),
    connected_provider_ids=(),
)
_READY = ObservationCompositionFact(
    semantic_configured=False,
    semantic_ready=False,
    provider_factory_ids=(),
    connected_provider_ids=(),
)


def _envelope(
    commitment: str, identity: str, payload: dict[str, object], *, pos: int
) -> ObservationEnvelope:
    return ObservationEnvelope(
        session_commitment=commitment,
        event_kind="PostToolUse",
        source_identity=identity,
        source=ObservationSource.CODEX_HOOK,
        cursor=ObservationCursor(
            source_generation=1,
            byte_position=pos * 8,
            event_position=pos,
            last_source_commitment=commitment,
            mapping_version="codex-obs-hook/1.0.0",
        ),
        receipt_time=_TIME,
        structural_payload=JsonObject(payload),
        content_object_refs=(),
        gap_codes=(),
    )


def _snapshot(
    commitment: str,
    *,
    envelopes: int,
    composition: ObservationCompositionFact | None = None,
    payload: dict[str, object] | None = None,
) -> AdviceSnapshot:
    body: dict[str, object] = (
        {"tool_name": "shell", "exit_status": 0} if payload is None else payload
    )
    built = build_observation_advice_snapshot(
        ObservationAdviceBuildInput(
            envelopes=tuple(
                _envelope(commitment, f"hook:{index}", body, pos=index)
                for index in range(1, envelopes + 1)
            ),
            lifecycle=ObservationLifecycle.ACTIVE,
            gaps=(),
            composition=composition,
            has_real_observation=True,
        )
    )
    assert built is not None
    return built


def _compose(monkeypatch: pytest.MonkeyPatch, composition: ObservationCompositionFact) -> None:
    """Make the hook's advice refresh composition-aware, as the service side is.

    ``handle_observe`` calls ``refresh_advice`` with no composition fact, so
    without this the standing provider condition — the exact condition that
    stormed — could never be reproduced through the hook path.
    """

    original = LocalObservationStore.refresh_advice

    def patched(self: LocalObservationStore, workspace: str, **kwargs: object) -> object:
        kwargs.setdefault("composition", composition)
        return original(self, workspace, **kwargs)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(LocalObservationStore, "refresh_advice", patched)


def _run(tmp_path: Path, event: str, session: str, **payload: object) -> str:
    """Run one hook and return its additionalContext (empty when none)."""

    out = io.BytesIO()
    body: dict[str, object] = {"session_id": session, "hook_event_name": event, **payload}
    code = handle_observe(
        event_name=event,
        stdin_bytes=json.dumps(body).encode(),
        stdout=out,
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    assert code == 0
    emitted = cast(Mapping[str, object], json.loads(out.getvalue().decode() or "{}"))
    specific = emitted.get("hookSpecificOutput")
    if not isinstance(specific, Mapping):
        return ""
    return str(cast(Mapping[str, object], specific).get("additionalContext") or "")


def _consented(tmp_path: Path) -> tuple[LocalObservationStore, str]:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    return store, workspace


def test_standing_advice_delivered_once_then_suppressed_across_n_envelope_ingests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#241 headline: 26 hooks, one standing-advice injection, not 26.

    The incident delivered the identical connect_provider text 29 times in one
    session because every new envelope moved the suppression identity.
    """

    _consented(tmp_path)
    _compose(monkeypatch, _STANDING)

    emitted = [_run(tmp_path, "SessionStart", "storm", source="startup")]
    for _ in range(25):
        emitted.append(_run(tmp_path, "PostToolUse", "storm", tool_name="shell", exit_status=0))

    standing = [index for index, text in enumerate(emitted) if "connect_provider" in text]
    assert standing == [0], (
        f"standing advice reached the agent {len(standing)} times across "
        f"{len(emitted)} hooks (indices {standing}); the incident measured 29"
    )


def test_standing_advice_never_masks_actionable_advice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cadence-gating a standing item falls through, it does not suppress the snapshot."""

    _consented(tmp_path)
    _compose(monkeypatch, _STANDING)

    _run(tmp_path, "SessionStart", "fall", source="startup")
    delivered = _run(
        tmp_path,
        "PostToolUse",
        "fall",
        tool_name="shell",
        claim_kind="semantic",
        exit_status=0,
    )
    assert "attempt_semantic_dispatch" in delivered
    assert "connect_provider" not in delivered


def test_standing_advice_reaches_the_agent_only_at_session_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SessionStart and Stop may carry it; the per-tool-call channel never does.

    The honest bound: Codex fires Stop per assistant turn, so standing advice
    can reappear at most once per turn, and only when a different text was
    delivered in between.
    """

    _consented(tmp_path)
    _compose(monkeypatch, _STANDING)

    first = _run(tmp_path, "SessionStart", "cadence", source="startup")
    middles = [
        _run(tmp_path, "PostToolUse", "cadence", tool_name="shell", exit_status=0)
        for _ in range(10)
    ]
    assert "connect_provider" in first
    assert not any("connect_provider" in text for text in middles)

    # A different advice text in between is what re-opens the single slot, and
    # the standing item must be top-ranked again once the work advice clears.
    failed = _run(
        tmp_path, "PostToolUse", "cadence", tool_name="shell", exit_status=1, correlation_id="c1"
    )
    assert "resolve_failed_command" in failed
    _run(tmp_path, "PostToolUse", "cadence", tool_name="shell", exit_status=0, correlation_id="c1")
    assert "connect_provider" in _run(tmp_path, "Stop", "cadence")


def test_changed_advice_is_still_delivered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _consented(tmp_path)
    _compose(monkeypatch, _READY)

    _run(tmp_path, "PostToolUse", "changed", tool_name="shell", exit_status=0)
    delivered = _run(
        tmp_path, "PostToolUse", "changed", tool_name="shell", exit_status=1, correlation_id="c1"
    )
    assert "resolve_failed_command" in delivered


def test_repeated_identical_advice_is_delivered_once(tmp_path: Path) -> None:
    store, workspace = _consented(tmp_path)
    first = _snapshot(workspace, envelopes=1, composition=_STANDING)

    store.set_advice_snapshot(workspace, first)
    assert store.peek_advice_for_delivery(workspace) is not None
    for grown in (4, 9):
        store.set_advice_snapshot(
            workspace, _snapshot(workspace, envelopes=grown, composition=_STANDING)
        )
        assert store.peek_advice_for_delivery(workspace) is None


def test_a_then_b_then_a_redelivers_a(tmp_path: Path) -> None:
    """Last-delivered is a single value, never a set: a reappearing condition speaks again."""

    store, workspace = _consented(tmp_path)
    advice_a = _snapshot(workspace, envelopes=1, composition=_STANDING)
    advice_b = _snapshot(
        workspace,
        envelopes=2,
        composition=_READY,
        payload={"tool_name": "shell", "exit_status": 1, "correlation_id": "x"},
    )

    texts: list[str] = []
    persisted: list[str | None] = []
    for snapshot in (advice_a, advice_b, advice_a):
        store.set_advice_snapshot(workspace, snapshot)
        delivery = store.peek_advice_for_delivery(workspace)
        assert delivery is not None
        texts.append(delivery.text)
        state = store._load(workspace)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        persisted.append(state.last_advice_suppression)

    assert texts[0] == texts[2] != texts[1]
    assert persisted[0] != persisted[1] and persisted[1] != persisted[2]
    assert persisted[0] == persisted[2]
