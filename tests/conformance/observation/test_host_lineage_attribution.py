"""Host-attribution boundary rows for the multi-agent conformance matrix."""

from __future__ import annotations

from dataclasses import replace

import pytest

from yoetz.application.observation_materialize import (
    canonical_logical_identity,
    materialize_observation_envelope,
)
from yoetz.domain.observation import (
    ObservationCursor,
    ObservationEnvelope,
    ObservationSource,
)
from yoetz.domain.values import JsonObject, Timestamp

_TASK = "tsk_00000000-0000-4000-8000-000000000001"
_SESSION = "hmac-sha256:" + ("ab" * 32)
_SOURCE_COMMITMENT = "hmac-sha256:" + ("cd" * 32)


def _envelope(
    source: ObservationSource,
    event_kind: str,
    *,
    parent_tool_call_id: str | None = "parent-call-1",
) -> ObservationEnvelope:
    fields: dict[str, str] = {"subagent_id": "host-child-1"}
    if parent_tool_call_id is not None:
        fields["parent_tool_call_id"] = parent_tool_call_id
    return ObservationEnvelope(
        session_commitment=_SESSION,
        event_kind=event_kind,
        source_identity=f"{source.value}:child-1",
        source=source,
        cursor=ObservationCursor(1, 1, 1, _SOURCE_COMMITMENT, "obs-ledger/1.5.0"),
        receipt_time=Timestamp("2026-09-05T18:00:00.000Z"),
        structural_payload=JsonObject(fields),
        content_object_refs=(),
        gap_codes=(),
    )


@pytest.mark.parametrize(
    ("source", "event_kind"),
    (
        (ObservationSource.CLAUDE_HOOK, "SubagentStart"),
        (ObservationSource.CODEX_HOOK, "SubagentStart"),
        (ObservationSource.CURSOR_HOOK, "SubagentStart"),
    ),
)
def test_each_host_identity_is_pending_service_observation_without_child_mint(
    source: ObservationSource,
    event_kind: str,
) -> None:
    batch = materialize_observation_envelope(_envelope(source, event_kind), task_id=_TASK)

    assert batch.skip_reason is None
    assert len(batch.drafts) == 1
    draft = batch.drafts[0]
    assert draft.role == "subagent"
    payload = draft.draft.payload
    assert "origin=host_observed" in (getattr(payload, "description", "") or "")
    assert "acceptance=pending" in (getattr(payload, "description", "") or "")
    assert (getattr(payload, "reference", "") or "").startswith("host-lineage:lineage:")


def test_missing_child_identity_is_one_explicit_gap_without_an_annotation() -> None:
    envelope = _envelope(ObservationSource.CODEX_HOOK, "SubagentStop")
    missing = replace(envelope, structural_payload=JsonObject({}))

    batch = materialize_observation_envelope(missing, task_id=_TASK)

    assert batch.drafts == ()
    assert batch.skip_reason == "missing_subagent_identity"


def test_hook_and_stream_partial_context_share_one_parent_scoped_observation_key() -> None:
    hook = _envelope(ObservationSource.CODEX_HOOK, "SubagentStop")
    stream = _envelope(
        ObservationSource.CODEX_SESSION_STREAM,
        "SubagentStop",
        parent_tool_call_id=None,
    )

    assert canonical_logical_identity(hook) == canonical_logical_identity(stream)
    hook_batch = materialize_observation_envelope(hook, task_id=_TASK)
    stream_batch = materialize_observation_envelope(stream, task_id=_TASK)
    assert hook_batch.drafts[0].draft.event_id == stream_batch.drafts[0].draft.event_id
    assert hook_batch.drafts[0].draft.payload == stream_batch.drafts[0].draft.payload
