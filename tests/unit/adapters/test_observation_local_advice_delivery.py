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

from yoetz.adapters.integrations.observation_local import AdviceDelivery, LocalObservationStore
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


def test_hook_serializes_advice_selection_through_commit_without_workspace_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _consented(tmp_path)
    _compose(monkeypatch, _STANDING)
    phases: list[tuple[str, bool]] = []
    original_peek = LocalObservationStore.peek_advice_for_delivery
    original_commit = LocalObservationStore.commit_advice_delivery

    def peek(
        self: LocalObservationStore,
        workspace: str,
        *,
        yoetz_session_id: str | None = None,
        allow_standing: bool = True,
        session_commitment: str | None = None,
    ) -> AdviceDelivery | None:
        phases.append(
            ("peek", workspace in self._batch)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        )
        return original_peek(
            self,
            workspace,
            yoetz_session_id=yoetz_session_id,
            allow_standing=allow_standing,
            session_commitment=session_commitment,
        )

    def commit(
        self: LocalObservationStore,
        workspace: str,
        delivery_identity: str,
        *,
        yoetz_session_id: str | None = None,
        session_commitment: str | None = None,
    ) -> None:
        phases.append(
            ("commit", workspace in self._batch)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        )
        original_commit(
            self,
            workspace,
            delivery_identity,
            yoetz_session_id=yoetz_session_id,
            session_commitment=session_commitment,
        )

    monkeypatch.setattr(LocalObservationStore, "peek_advice_for_delivery", peek)
    monkeypatch.setattr(LocalObservationStore, "commit_advice_delivery", commit)

    assert "connect_provider" in _run(tmp_path, "SessionStart", "serialized", source="startup")
    assert phases == [("peek", False), ("commit", False)]


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
    delivery = store.peek_advice_for_delivery(workspace)
    assert delivery is not None
    store.commit_advice_delivery(workspace, delivery.delivery_identity)
    for grown in (4, 9):
        store.set_advice_snapshot(
            workspace, _snapshot(workspace, envelopes=grown, composition=_STANDING)
        )
        assert store.peek_advice_for_delivery(workspace) is None


def test_peek_alone_never_suppresses_the_next_peek(tmp_path: Path) -> None:
    """The selection is a pure read; only a committed delivery suppresses (#242 review).

    Recording the identity before the hook wrote its stdout meant one broken
    pipe suppressed that advice permanently — strictly worse than a repeat.
    """

    store, workspace = _consented(tmp_path)
    store.set_advice_snapshot(workspace, _snapshot(workspace, envelopes=1, composition=_STANDING))

    first = store.peek_advice_for_delivery(workspace)
    second = store.peek_advice_for_delivery(workspace)
    assert first is not None and second is not None
    assert first.delivery_identity == second.delivery_identity
    state = store._load(workspace)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    assert state.last_advice_suppression is None

    store.commit_advice_delivery(workspace, first.delivery_identity)
    assert store.peek_advice_for_delivery(workspace) is None


def test_failed_stdout_write_redelivers_the_advice_on_the_next_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hook whose pipe closes mid-write must not lose the advice it selected."""

    _consented(tmp_path)
    _compose(monkeypatch, _STANDING)

    class _BrokenStdout(io.BytesIO):
        def write(self, data: object) -> int:
            del data
            raise BrokenPipeError("pipe closed by host")

    code = handle_observe(
        event_name="SessionStart",
        stdin_bytes=json.dumps({"session_id": "broken", "source": "startup"}).encode(),
        stdout=_BrokenStdout(),
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    assert code == 0

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    state = store._load(workspace)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    scope = store.session_commitment("broken")
    assert state.last_advice_suppression is None, (
        "a delivery that never reached stdout was recorded as delivered"
    )
    assert (state.session_advice_suppression or {}).get(scope) is None, (
        "a delivery that never reached stdout was recorded as delivered"
    )
    assert "connect_provider" in _run(tmp_path, "Stop", "broken")


def test_successful_emit_records_the_delivery_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _consented(tmp_path)
    _compose(monkeypatch, _STANDING)

    assert "connect_provider" in _run(tmp_path, "SessionStart", "recorded", source="startup")

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    state = store._load(workspace)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    recorded = (state.session_advice_suppression or {}).get(store.session_commitment("recorded"))
    assert type(recorded) is str and recorded.startswith("deliver-")
    assert "connect_provider" not in _run(tmp_path, "Stop", "recorded")


def test_a_then_b_then_a_redelivers_a(tmp_path: Path) -> None:
    """Last-delivered is a single value, never a set: a reappearing condition speaks again."""

    store, workspace = _consented(tmp_path)
    session = "ses_00000000-0000-4000-8000-0000000000aa"
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
        store.set_session_advice_snapshot(workspace, yoetz_session_id=session, snapshot=snapshot)
        delivery = store.peek_advice_for_delivery(workspace, yoetz_session_id=session)
        assert delivery is not None
        store.commit_advice_delivery(
            workspace, delivery.delivery_identity, yoetz_session_id=session
        )
        texts.append(delivery.text)
        state = store._load(workspace)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        persisted.append((state.session_advice_suppression or {}).get(session))

    assert texts[0] == texts[2] != texts[1]
    assert persisted[0] != persisted[1] and persisted[1] != persisted[2]
    assert persisted[0] == persisted[2]


def _map_session(tmp_path: Path, codex_session: str) -> str:
    from yoetz.adapters.integrations.codex_lifecycle import (
        mapping_from_start_ids,
        store_mapping,
    )
    from yoetz.protocol.ids import IdKind, new_id

    yoetz_session = new_id(IdKind.SESSION)
    store_mapping(
        mapping_from_start_ids(
            codex_session_id=codex_session,
            yoetz_task_id=new_id(IdKind.TASK),
            yoetz_session_id=yoetz_session,
            yoetz_writer_id=new_id(IdKind.WRITER),
            last_frontier=None,
        ),
        _state=tmp_path,
    )
    return yoetz_session


def test_workspace_snapshot_is_not_a_task_scoped_fallback(tmp_path: Path) -> None:
    """A workspace-wide failed_command snapshot is not current-task advice (#249)."""

    store, workspace = _consented(tmp_path)
    store.set_advice_snapshot(
        workspace,
        _snapshot(
            workspace,
            envelopes=1,
            payload={"tool_name": "shell", "exit_status": 1, "correlation_id": "old"},
        ),
    )
    assert store.peek_advice_for_delivery(workspace) is None
    assert store.peek_advice_for_delivery(workspace, allow_standing=False) is None


def test_unmapped_task_does_not_receive_prior_task_failed_command(
    tmp_path: Path,
) -> None:
    """#249: task B must not inherit task A's resolve_failed_command as current work."""

    _consented(tmp_path)
    first = _run(
        tmp_path,
        "PostToolUse",
        "task-a",
        tool_name="shell",
        exit_status=1,
        correlation_id="a1",
    )
    assert "resolve_failed_command" in first

    later = _run(tmp_path, "PostToolUse", "task-b", tool_name="shell", exit_status=0)
    assert "resolve_failed_command" not in later
    assert later == ""


def test_same_session_failed_command_stays_deliverable(tmp_path: Path) -> None:
    _consented(tmp_path)
    delivered = _run(
        tmp_path,
        "PostToolUse",
        "same-session",
        tool_name="shell",
        exit_status=1,
        correlation_id="s1",
    )
    assert "resolve_failed_command" in delivered


def test_mapped_session_without_own_snapshot_does_not_use_workspace_task_advice(
    tmp_path: Path,
) -> None:
    _consented(tmp_path)
    prior = _run(
        tmp_path,
        "PostToolUse",
        "task-a",
        tool_name="shell",
        exit_status=1,
        correlation_id="old",
    )
    assert "resolve_failed_command" in prior
    _map_session(tmp_path, "mapped-b")

    delivered = _run(tmp_path, "PostToolUse", "mapped-b", tool_name="shell", exit_status=0)
    assert "resolve_failed_command" not in delivered


def test_mapped_session_snapshot_still_delivers_its_own_failed_command(
    tmp_path: Path,
) -> None:
    store, workspace = _consented(tmp_path)
    yoetz_session = _map_session(tmp_path, "mapped-own")
    own = _snapshot(
        workspace,
        envelopes=1,
        payload={"tool_name": "shell", "exit_status": 1, "correlation_id": "own"},
    )
    store.set_session_advice_snapshot(workspace, yoetz_session_id=yoetz_session, snapshot=own)

    delivered = _run(tmp_path, "PostToolUse", "mapped-own", tool_name="shell", exit_status=0)
    assert "resolve_failed_command" in delivered


def test_unmapped_session_start_still_delivers_workspace_standing_advice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _consented(tmp_path)
    _compose(monkeypatch, _STANDING)
    prior = _run(
        tmp_path,
        "PostToolUse",
        "task-a",
        tool_name="shell",
        exit_status=1,
        correlation_id="old",
    )
    assert "resolve_failed_command" in prior

    started = _run(tmp_path, "SessionStart", "fresh-b", source="startup")
    assert "connect_provider" in started
    assert "resolve_failed_command" not in started


def test_delivery_in_task_a_does_not_suppress_same_condition_in_task_b(
    tmp_path: Path,
) -> None:
    _consented(tmp_path)
    first = _run(
        tmp_path,
        "PostToolUse",
        "suppress-a",
        tool_name="shell",
        exit_status=1,
        correlation_id="a1",
    )
    assert "resolve_failed_command" in first
    second = _run(
        tmp_path,
        "PostToolUse",
        "suppress-b",
        tool_name="shell",
        exit_status=1,
        correlation_id="b1",
    )
    assert "resolve_failed_command" in second
