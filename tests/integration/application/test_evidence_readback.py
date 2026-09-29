"""An agent can read back and find its own evidence without widening any sink (issue #914).

Every test runs the daemon's exact post-commit projection for an MCP bridge client through the
production-composed ``LocalPrivacyEnforcer`` (``ready_composition.build_local_privacy_enforcer``)
and the shipped default policy, whose ``agent_context`` ceiling does not grant
``evidence_excerpt``. The agent's requests use the cooperative MCP wire shape; host-observed rows
come from the canonical OBS-001 captured-evidence envelope materialized for the task and appended
under the observation coordinator's own writer, as the observation drain does.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import cast

import pytest

from builders.projection_workflow import (
    ProjectionCase,
    build_projection_application,
    frontier_json,
    project_case,
    request_base,
)
from builders.start_application import protocol_id, start_request
from fixture_loader import load_fixture_json
from yoetz.application.observation_materialize import (
    materialize_observation_envelope,
    observation_author,
    observation_writer_id,
)
from yoetz.application.publish_work import PublishWorkInternalResult
from yoetz.application.service import Application
from yoetz.application.start import StartInternalResult
from yoetz.application.status import StatusInternalResult
from yoetz.domain.events import (
    EventDraft,
    EventSchema,
    EvidenceKind,
    EvidenceRecordedPayload,
    encode_payload,
    media_type_for,
)
from yoetz.domain.observation import (
    ObservationContentKind,
    ObservationContentManifest,
    ObservationCursor,
    ObservationEnvelope,
    ObservationSource,
)
from yoetz.domain.values import (
    Actor,
    Frontier,
    JsonObject,
    Timestamp,
    event_id,
    evidence_id,
    object_id,
    timestamp_from_datetime,
)
from yoetz.ports.control import ControlMethod
from yoetz.ports.ledger import AppendCommand, AppendEntry, OperationKind
from yoetz.ports.objects import ObjectKind, ObjectMetadata, ObjectSource
from yoetz.protocol.canonical import JsonValue, canonical_encode
from yoetz.protocol.coverage import (
    Coverage,
    EvidenceImmutability,
    PublicationChannel,
    coverage_for_channel,
)
from yoetz.protocol.models import PublishWorkRequest, StatusRequest

pytestmark = pytest.mark.anyio

_DIGEST = "sha256:" + "4" * 64
_OMITTED = {
    "category": "evidence_excerpt",
    "omitted": True,
    "reason": "local_disclosure_not_authorized",
}
# The two items the dateutil semantic attempt published at codex.txt L167 (descriptions trimmed).
_DIFF_DESCRIPTION = (
    "Commit de58a86 source diff (22,099 bytes). Bounded changed-code excerpt: _date_property "
    "emits Z for UTC and TZID for named local time."
)
_TEST_DESCRIPTION = (
    "PYTHONPATH=src /usr/local/bin/python -m pytest tests -q: 2036 passed, 47 skipped, "
    "16 xfailed in 1.94s."
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _evidence_draft(
    seed: int,
    *,
    kind: str,
    subject: str,
    description: str,
    reference: str,
) -> dict[str, JsonValue]:
    return {
        "event_id": protocol_id("evt_", seed),
        "schema": {"name": "evidence_recorded", "version": "1.1.0"},
        "occurred_at": "2026-07-28T12:00:04.000Z",
        "causal_parents": [],
        "payload": {
            "evidence_id": protocol_id("evd_", seed),
            "evidence_kind": kind,
            "strength": "content_digest",
            "observed_at": "2026-07-28T12:00:04.000Z",
            "description": description,
            "reference": reference,
            "content_digest": "sha256:" + f"{seed:x}".rjust(64, "a"),
            "digest_binding": {
                "subject": subject,
                "content_availability": "digest_only",
                "byte_count": 22099,
                "provenance": "caller_asserted",
            },
        },
        "artifact_refs": [],
        "evidence_refs": [],
    }


async def _start(app: Application, seed: int) -> StartInternalResult:
    started = await app.start(start_request(seed, title="Evidence read-back", refs=True))
    assert type(started) is StartInternalResult
    return started


async def _publish(
    app: Application,
    started: StartInternalResult,
    seed: int,
    frontier: Frontier,
    drafts: Sequence[dict[str, JsonValue]],
) -> PublishWorkInternalResult:
    body: dict[str, JsonValue] = {
        **request_base(protocol_id("req_", seed)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "expected_frontier": frontier_json(frontier),
        "event_drafts": list(drafts),
    }
    published = await app.publish_work(PublishWorkRequest.model_validate(body))
    assert type(published) is PublishWorkInternalResult
    return published


async def _publish_agent_evidence(
    app: Application, started: StartInternalResult, seed: int
) -> tuple[PublishWorkInternalResult, str, str]:
    published = await _publish(
        app,
        started,
        seed,
        started.frontier,
        (
            _evidence_draft(
                seed + 1,
                kind="artifact",
                subject="source_diff",
                description=_DIFF_DESCRIPTION,
                reference="git show de58a86",
            ),
            _evidence_draft(
                seed + 2,
                kind="test_result",
                subject="test_stdout",
                description=_TEST_DESCRIPTION,
                reference="pytest tests -q",
            ),
        ),
    )
    return published, protocol_id("evd_", seed + 1), protocol_id("evd_", seed + 2)


def _ledger(app: Application, task_id: str) -> tuple[object, object]:
    resources = cast(Mapping[str, tuple[object, object]], getattr(app.runtime, "resources"))
    return resources[task_id]


async def _append(
    app: Application,
    started: StartInternalResult,
    *,
    seed: int,
    expected_frontier: int,
    writer: str,
    author: Actor,
    channel: PublicationChannel,
    drafts: Sequence[tuple[EventDraft, bytes]],
    coverage: Coverage | None = None,
) -> Frontier:
    """Append ledger entries the way a service-side writer does, bypassing ``publish_work``."""

    ledger, objects = _ledger(app, started.task_id)
    now = app.clock.now_utc()
    entries: list[AppendEntry] = []
    for draft, payload_bytes in drafts:
        metadata = ObjectMetadata(
            ObjectKind.EVENT_PAYLOAD, media_type_for(draft.schema.name), started.task_id, now
        )
        staged = await objects.stage(  # type: ignore[attr-defined]
            ObjectSource(data=payload_bytes, declared_size=len(payload_bytes)), metadata
        )
        payload_ref = await objects.finalize(staged)  # type: ignore[attr-defined]
        entries.append(
            AppendEntry(
                draft,
                author,
                payload_ref,
                payload_ref.commitment,
                metadata.media_type,
                payload_ref.plaintext_size,
                channel,
                coverage_for_channel(channel) if coverage is None else coverage,
                "projected",
            )
        )
    result = await ledger.append_batch(  # type: ignore[attr-defined]
        AppendCommand(
            started.task_id,
            started.session_id,
            writer,
            protocol_id("req_", seed),
            OperationKind.PUBLISH_WORK,
            _DIGEST,
            expected_frontier,
            tuple(entries),
            None,
        )
    )
    frontier = result.result_frontier
    assert type(frontier) is Frontier
    return frontier


def _plain_evidence(
    seed: int, description: str, *, strength: EvidenceImmutability
) -> tuple[EventDraft, bytes]:
    snapshot = strength is EvidenceImmutability.IMMUTABLE_SNAPSHOT
    captured = object_id(protocol_id("obj_", seed)) if snapshot else None
    payload = EvidenceRecordedPayload(
        evidence_id(protocol_id("evd_", seed)),
        EvidenceKind.ARTIFACT,
        strength,
        timestamp_from_datetime(datetime(2026, 7, 28, 12, 1, tzinfo=UTC)),
        description=description,
        reference=f"reference for {description}",
        captured_object_id=captured,
        content_digest=("sha256:" + "9" * 64) if snapshot else None,
    )
    draft = EventDraft(
        event_id(protocol_id("evt_", seed)),
        EventSchema("evidence_recorded", "1.0.0"),
        payload.observed_at,
        (),
        payload,
        () if captured is None else (captured,),
        (),
    )
    return draft, canonical_encode(encode_payload(payload))


def _captured_observation(
    task_id: str,
) -> tuple[tuple[tuple[EventDraft, bytes], ...], Coverage]:
    """Materialize the canonical OBS-001 host capture for *task_id*, exactly as the drain does."""

    fixture = cast(
        dict[str, object], load_fixture_json("canonical/OBS-001-captured-evidence.case.json")
    )
    raw_input = cast(dict[str, object], fixture["input"])
    raw = cast(dict[str, object], raw_input["envelope"])
    cursor = cast(dict[str, object], raw["cursor"])
    envelope = ObservationEnvelope(
        session_commitment=cast(str, raw["session_commitment"]),
        event_kind=cast(str, raw["event_kind"]),
        source_identity=cast(str, raw["source_identity"]),
        source=ObservationSource(cast(str, raw["source"])),
        cursor=ObservationCursor(
            cast(int, cursor["hook_seq"]),
            cast(int, cursor["session_stream_pos"]),
            cast(int, cursor["source_ordinal"]),
            cast(str, cursor["last_commitment"]),
            cast(str, cursor["mapping_version"]),
        ),
        receipt_time=Timestamp(cast(str, raw["receipt_time"])),
        structural_payload=JsonObject(cast(dict[str, JsonValue], raw["structural_payload"])),
        content_object_refs=tuple(cast(list[str], raw["content_object_refs"])),
        gap_codes=tuple(cast(list[str], raw["gap_codes"])),
    )
    manifests = tuple(
        ObservationContentManifest(
            object_id=cast(str, item["object_id"]),
            envelope_digest=cast(str, item["envelope_digest"]),
            content_kind=ObservationContentKind(cast(str, item["content_kind"])),
            part_index=cast(int, item["part_index"]),
            part_count=cast(int, item["part_count"]),
            redacted=cast(bool, item["redacted"]),
            content_digest=cast(str, item["content_digest"]),
            content_bytes=cast(int, item["content_bytes"]),
        )
        for item in cast(list[dict[str, object]], raw_input["manifests"])
    )
    batch = materialize_observation_envelope(envelope, task_id=task_id, captured_content=manifests)
    assert batch.channel is PublicationChannel.HOOK_OBSERVED
    return tuple((item.draft, item.payload_bytes) for item in batch.drafts), batch.coverage


async def _status(
    app: Application,
    started: StartInternalResult,
    seed: int,
    *,
    session: str | None = None,
    writer: str | None = None,
    evidence_filter: Mapping[str, JsonValue] | None = None,
    limit: int = 100,
    cursor: str | None = None,
    at_frontier: int | None = None,
) -> tuple[StatusInternalResult, Mapping[str, JsonValue]]:
    body: dict[str, JsonValue] = {
        **request_base(protocol_id("req_", seed)),
        "session_id": session or started.session_id,
        "writer_id": writer or started.writer_id,
        "view": "evidence",
        "limit": str(limit),
    }
    if evidence_filter is not None:
        body["filter"] = dict(evidence_filter)
    if cursor is not None:
        body["cursor"] = cursor
    if at_frontier is not None:
        body["at_frontier"] = str(at_frontier)
    internal = await app.status(StatusRequest.model_validate(body))
    assert type(internal) is StatusInternalResult
    projected = await project_case(
        app, ProjectionCase("status/evidence", ControlMethod.STATUS, body, internal), seed + 1
    )
    return internal, projected


def _rows(projected: Mapping[str, JsonValue]) -> dict[str, Mapping[str, JsonValue]]:
    page = cast(Mapping[str, JsonValue], projected["page"])
    items = cast(Sequence[Mapping[str, JsonValue]], page["items"])
    return {cast(str, item["evidence_id"]): item for item in items}


async def test_immutable_snapshot_filter_lists_native_captures_across_pages() -> None:
    """The guidance's native-capture discovery filter works through the status evidence view."""

    app, _policy = await build_projection_application(seed=9140)
    started = await _start(app, 9141)
    published, diff_id, test_id = await _publish_agent_evidence(app, started, 9150)
    frontier = published.result_frontier
    observation_writer = observation_writer_id(started.task_id, started.session_id)
    captured, captured_coverage = _captured_observation(started.task_id)
    frontier = await _append(
        app,
        started,
        seed=9160,
        expected_frontier=frontier.sequence,
        writer=observation_writer,
        author=observation_author(),
        channel=PublicationChannel.HOOK_OBSERVED,
        drafts=captured,
        coverage=captured_coverage,
    )
    second = tuple(
        _plain_evidence(9170 + offset, f"observed snapshot {offset}", strength=strength)
        for offset, strength in enumerate(
            (EvidenceImmutability.IMMUTABLE_SNAPSHOT, EvidenceImmutability.MUTABLE_REFERENCE)
        )
    )
    await _append(
        app,
        started,
        seed=9175,
        expected_frontier=frontier.sequence,
        writer=observation_writer,
        author=observation_author(),
        channel=PublicationChannel.HOOK_OBSERVED,
        drafts=second,
    )
    snapshot_filter: dict[str, JsonValue] = {"strength": "immutable_snapshot"}

    _first, first_page = await _status(app, started, 9180, evidence_filter=snapshot_filter, limit=1)
    first_rows = _rows(first_page)
    next_cursor = cast(Mapping[str, JsonValue], first_page["page"])["next_cursor"]
    assert type(next_cursor) is str
    _second, second_page = await _status(
        app, started, 9182, evidence_filter=snapshot_filter, limit=1, cursor=next_cursor
    )
    second_rows = _rows(second_page)
    assert cast(Mapping[str, JsonValue], second_page["page"])["next_cursor"] is None

    listed = {**first_rows, **second_rows}
    assert len(listed) == 2
    assert all(row["strength"] == "immutable_snapshot" for row in listed.values())
    assert diff_id not in listed and test_id not in listed
    assert protocol_id("evd_", 9170) in listed
    assert protocol_id("evd_", 9171) not in listed
