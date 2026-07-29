"""Memory and SQLite durable append project the same obligation-resolution error."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from builders.ledger_adapters import append_command, memory_adapter, sqlite_adapter
from yoetz.domain.events import (
    EventDraft,
    EventSchema,
    ObligationPublishedPayload,
    ObligationStatus,
    RequestedItem,
    RequestedItemKind,
    encode_payload,
    media_type_for,
)
from yoetz.domain.values import (
    Actor,
    ActorType,
    actor_id,
    event_id,
    evidence_id,
    obligation_id,
    timestamp_from_string,
)
from yoetz.ports.ledger import AppendCommand, AppendEntry, OperationKind
from yoetz.ports.objects import ObjectKind, ObjectMetadata, ObjectSource
from yoetz.protocol.canonical import canonical_encode
from yoetz.protocol.coverage import AuthorshipAssurance, PublicationChannel, coverage_for_channel
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError

pytestmark = pytest.mark.anyio

_OBL = obligation_id("obl_00000000-0000-4000-8000-000000000801")
_EVD = evidence_id("evd_00000000-0000-4000-8000-000000000802")
_NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)


def _open_payload() -> ObligationPublishedPayload:
    return ObligationPublishedPayload(
        obligation_id=_OBL,
        description="Close the loop with exact evidence.",
        evidence_expectation="A named test run at the claimed state.",
        status=ObligationStatus.OPEN,
        acceptance_criteria="The focused slice is green.",
        requested_items=(RequestedItem(RequestedItemKind.COMMAND, "pytest -q"),),
    )


def _bad_resolution() -> ObligationPublishedPayload:
    return ObligationPublishedPayload(
        obligation_id=_OBL,
        description="Close the loop with exact evidence.",
        evidence_expectation="Shortened.",
        status=ObligationStatus.RESOLVED,
        resolution_evidence_refs=(_EVD,),
    )


async def _append_payload(
    ledger: object,
    payload: ObligationPublishedPayload,
    *,
    request_tail: int,
    event_tail: int,
) -> None:
    author = Actor(
        actor_id("agt_fixture"),
        ActorType.LOGICAL_AGENT,
        AuthorshipAssurance.SELF_ASSERTED,
    )
    draft = EventDraft(
        event_id(f"evt_00000000-0000-4000-8000-{event_tail:012d}"),
        EventSchema("obligation_published", "1.0.0"),
        timestamp_from_string("2026-07-19T12:00:00.000Z"),
        (),
        payload,
        (),
        (),
    )
    objects = ledger._objects  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
    seed = append_command()
    media = media_type_for("obligation_published")
    payload_bytes = canonical_encode(encode_payload(payload))
    staged = await objects.stage(
        ObjectSource(data=payload_bytes, declared_size=len(payload_bytes)),
        ObjectMetadata(ObjectKind.EVENT_PAYLOAD, media, seed.task_id, _NOW),
    )
    ref = await objects.finalize(staged)
    entry = AppendEntry(
        draft,
        author,
        ref,
        ref.commitment,
        media,
        ref.plaintext_size,
        PublicationChannel.LOCAL_CLI,
        coverage_for_channel(PublicationChannel.LOCAL_CLI),
        "projected",
    )
    frontier = await ledger.load_frontier()  # pyright: ignore[reportAttributeAccessIssue]
    digest_hex = f"{request_tail:064x}"[-64:]
    command = AppendCommand(
        seed.task_id,
        seed.session_id,
        seed.writer_id,
        f"req_00000000-0000-4000-8000-{request_tail:012d}",
        OperationKind.PUBLISH_WORK,
        "sha256:" + digest_hex,
        frontier.sequence,
        (entry,),
    )
    await ledger.append_batch(command)  # pyright: ignore[reportAttributeAccessIssue]


@pytest.mark.parametrize("adapter_name", ("memory", "sqlite"))
async def test_obligation_resolution_mismatch_on_durable_append(adapter_name: str) -> None:
    seed = append_command()
    ledger = memory_adapter(seed) if adapter_name == "memory" else sqlite_adapter(seed)
    await _append_payload(ledger, _open_payload(), request_tail=801, event_tail=802)
    with pytest.raises(PublicOperationError) as caught:
        await _append_payload(ledger, _bad_resolution(), request_tail=803, event_tail=804)
    error = caught.value
    assert error.code is PublicErrorCode.EVENT_INVALID
    assert error.safe_details["reason_code"] == "obligation_resolution_mismatch"
    assert error.safe_details["field"] == "/event_drafts/0/payload"
    assert "meaning_fields_must_repeat" in error.message
    assert "acceptance_criteria" in error.message
    assert "evidence_expectation" in error.message
    assert "Shortened" not in error.message


async def test_memory_and_sqlite_emit_identical_public_error_contract() -> None:
    failures: list[PublicOperationError] = []
    for factory in (memory_adapter, sqlite_adapter):
        seed = append_command()
        ledger = factory(seed)
        await _append_payload(ledger, _open_payload(), request_tail=811, event_tail=812)
        with pytest.raises(PublicOperationError) as caught:
            await _append_payload(ledger, _bad_resolution(), request_tail=813, event_tail=814)
        failures.append(caught.value)
    assert failures[0].code is failures[1].code is PublicErrorCode.EVENT_INVALID
    assert dict(failures[0].safe_details) == dict(failures[1].safe_details)
    assert failures[0].message == failures[1].message
