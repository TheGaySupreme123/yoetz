"""State-file bounds for stream partials and store-stage attribution (#289, #290)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

import yoetz.adapters.integrations.observation_local as local_mod
from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.domain.observation import (
    ObservationCursor,
    ObservationEnvelope,
    ObservationGapCode,
    ObservationSource,
    ObservationStatusQuery,
)
from yoetz.domain.values import JsonObject, Timestamp
from yoetz.protocol.errors import ProtocolValueError

_DROPPED_GAP = "_local_stream_partial_dropped"
_MAX_PARTIAL = local_mod._MAX_STREAM_PARTIAL_BYTES  # pyright: ignore[reportPrivateUsage]


def _envelope(*, session: str, identity: str, ordinal: int = 1) -> ObservationEnvelope:
    return ObservationEnvelope(
        session_commitment=session,
        event_kind="PreToolUse",
        source_identity=identity,
        source=ObservationSource.CODEX_HOOK,
        cursor=ObservationCursor(1, 0, ordinal, f"hmac-sha256:{'ab' * 32}", "codex-obs-hook/1.0.0"),
        receipt_time=Timestamp("2026-01-01T00:00:00.000Z"),
        structural_payload=JsonObject({"tool_name": "shell", "tool_call_id": f"c{ordinal}"}),
        content_object_refs=(),
        gap_codes=(),
    )


def _consented_store(tmp_path: Path) -> tuple[LocalObservationStore, str, str]:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session = store.bind_codex_session(workspace, "sess-partials")
    return store, workspace, session


def _state_json(tmp_path: Path) -> dict[str, object]:
    path = next((tmp_path / "observation" / "workspaces").glob("*.json"))
    return cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))


def test_stream_partial_round_trips_within_bound(tmp_path: Path) -> None:
    store, workspace, session = _consented_store(tmp_path)
    partial = b'{"unterminated": tail' * 16
    store.set_stream_partial(workspace, session, partial)
    assert store.get_stream_partial(workspace, session) == partial
    store.set_stream_partial(workspace, session, b"")
    assert store.get_stream_partial(workspace, session) == b""


def test_stream_partial_rejects_non_bytes(tmp_path: Path) -> None:
    store, workspace, session = _consented_store(tmp_path)
    with pytest.raises(ProtocolValueError):
        store.set_stream_partial(workspace, session, cast(bytes, "text"))


def test_oversized_stream_partial_drops_with_gap_instead_of_raising(tmp_path: Path) -> None:
    """An oversized tail must not stall the stream or pin state-file bytes (#289)."""

    store, workspace, session = _consented_store(tmp_path)
    store.set_stream_partial(workspace, session, b"held")
    oversized = b"x" * (_MAX_PARTIAL + 1)

    store.set_stream_partial(workspace, session, oversized)

    assert store.get_stream_partial(workspace, session) == b""
    persisted = _state_json(tmp_path)
    assert _DROPPED_GAP in cast(list[str], persisted["gaps"])
    # The internal marker never leaks to status; it projects as SOURCE_LAG,
    # because the source tail is pending a reread until reconcile catches up.
    gaps = store.status(ObservationStatusQuery(workspace)).gaps
    assert _DROPPED_GAP not in gaps
    assert ObservationGapCode.SOURCE_LAG.value in gaps


def test_recovered_stream_partial_resolves_the_dropped_gap(tmp_path: Path) -> None:
    store, workspace, session = _consented_store(tmp_path)
    store.set_stream_partial(workspace, session, b"y" * (_MAX_PARTIAL + 1))
    assert (
        ObservationGapCode.SOURCE_LAG.value in store.status(ObservationStatusQuery(workspace)).gaps
    )

    store.set_stream_partial(workspace, session, b"reread tail")

    assert store.get_stream_partial(workspace, session) == b"reread tail"
    gaps = store.status(ObservationStatusQuery(workspace)).gaps
    assert ObservationGapCode.SOURCE_LAG.value not in gaps


def test_save_sheds_stream_partials_before_envelopes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The eviction ladder drops the read-cache before any durable row (#289)."""

    store, workspace, session = _consented_store(tmp_path)
    for ordinal in range(1, 4):
        store.ingest(_envelope(session=session, identity=f"hook:keep:{ordinal}", ordinal=ordinal))
    store.set_stream_partial(workspace, session, b"a" * 4_096)
    other = store.bind_codex_session(workspace, "sess-partials-2")
    store.set_stream_partial(workspace, other, b"b" * 8_192)
    state_path = next((tmp_path / "observation" / "workspaces").glob("*.json"))
    # Cap below the current size but above what remains once both partials
    # (their base64 forms dominate the overage) are shed.
    monkeypatch.setattr(local_mod, "_MAX_STATE_BYTES", state_path.stat().st_size - 8_192)

    store.note_coverage_gap(workspace, ObservationGapCode.SERVICE_UNAVAILABLE.value)

    reopened = LocalObservationStore(_state=tmp_path)
    # Largest-first shedding stops as soon as the state fits: the big partial
    # is gone, the small one survives untouched.
    assert reopened.get_stream_partial(workspace, other) == b""
    assert reopened.get_stream_partial(workspace, session) == b"a" * 4_096
    persisted = _state_json(tmp_path)
    assert _DROPPED_GAP in cast(list[str], persisted["gaps"])
    # Every envelope survived: partials were shed first and sufficed.
    assert len(cast(list[object], persisted["envelopes"])) == 3
    assert ObservationGapCode.TRUNCATED_PAYLOAD.value not in cast(list[str], persisted["gaps"])


def test_legacy_oversized_partial_is_dropped_on_next_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """States persisted before the bound existed are healed by any later save."""

    store, workspace, session = _consented_store(tmp_path)
    monkeypatch.setattr(local_mod, "_MAX_STREAM_PARTIAL_BYTES", 1 << 30)
    store.set_stream_partial(workspace, session, b"z" * 100_000)
    monkeypatch.undo()

    reopened = LocalObservationStore(_state=tmp_path)
    reopened.note_coverage_gap(workspace, ObservationGapCode.SERVICE_UNAVAILABLE.value)

    assert reopened.get_stream_partial(workspace, session) == b""
    persisted = _state_json(tmp_path)
    assert _DROPPED_GAP in cast(list[str], persisted["gaps"])


def test_store_stage_timings_attribute_hydrate_encode_write(tmp_path: Path) -> None:
    """The store accounts its hydrate/encode/write cost for hook timing rows (#290)."""

    seeded, workspace, session = _consented_store(tmp_path)
    seeded.ingest(_envelope(session=session, identity="hook:timed", ordinal=1))

    store = LocalObservationStore(_state=tmp_path)
    assert store.stage_timings_ms == {"hydrate": 0.0, "encode": 0.0, "write": 0.0}
    store.note_coverage_gap(workspace, ObservationGapCode.SERVICE_UNAVAILABLE.value)
    assert store.stage_timings_ms["hydrate"] > 0.0
    assert store.stage_timings_ms["encode"] > 0.0
    assert store.stage_timings_ms["write"] > 0.0
