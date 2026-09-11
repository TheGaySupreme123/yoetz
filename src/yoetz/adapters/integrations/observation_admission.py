"""Pure, bounded preparation of observation admission before optional capture.

The owning local store commits this state and every resulting outbox row in one
batch. These functions do no IO and never acknowledge a source cursor themselves.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Final, Literal, cast

from yoetz.domain.observation import (
    ROUTINE_READ_SUMMARY_CONTENT_SCOPE,
    ROUTINE_READ_SUMMARY_MAPPING_VERSION,
    ROUTINE_READ_SUMMARY_PROVENANCE,
    ROUTINE_READ_SUMMARY_SCHEMA,
    ObservationCursor,
    ObservationEnvelope,
    observation_cursor_to_json,
    observation_envelope_from_json,
    observation_envelope_to_json,
    observation_selection_route,
    routine_read_summary_identity,
)
from yoetz.domain.values import JsonObject, JsonValue, validate_sha256_digest
from yoetz.protocol.canonical import canonical_digest
from yoetz.protocol.errors import ProtocolValueError

SUMMARY_MAX_CALLS: Final = 16
SUMMARY_MAX_INPUTS: Final = 32
SUMMARY_FLUSH_MS: Final = 2_000
PENDING_ATTEMPT_FLUSH_MS: Final = 5_000
MAX_BUFFERED_INPUTS: Final = 256
SUMMARY_EVENT_KIND: Final = "RoutineReadSummary"

SummaryBuilder = Callable[[tuple[ObservationEnvelope, ...], str], ObservationEnvelope]


@dataclass(frozen=True, slots=True)
class BufferedInput:
    host_session: str
    fence: str
    envelope: ObservationEnvelope
    kind: Literal["pending", "success"]
    buffered_at_ms: int

    @property
    def lane(self) -> tuple[str, str]:
        return (self.envelope.source.value, self.envelope.session_commitment)


@dataclass(frozen=True, slots=True)
class AdmissionBuffer:
    inputs: tuple[BufferedInput, ...] = ()

    @property
    def pending_attempt_count(self) -> int:
        return sum(item.kind == "pending" for item in self.inputs)

    @property
    def summarized_call_count(self) -> int:
        return sum(
            item.kind == "success" and item.envelope.event_kind == "PostToolUse"
            for item in self.inputs
        )


@dataclass(frozen=True, slots=True)
class AdmissionPlan:
    buffer: AdmissionBuffer
    deliveries: tuple[tuple[str, ObservationEnvelope], ...]
    deferred: bool


def _call_identity(envelope: ObservationEnvelope) -> object:
    return envelope.structural_payload.get("tool_call_id") or envelope.structural_payload.get(
        "correlation_id"
    )


_ROUTINE_SUCCESS_STATUSES: Final = frozenset({"success", "succeeded", "ok", "completed", "passed"})
_ROUTINE_FAILURE_STATUSES: Final = frozenset(
    {
        "failure",
        "failed",
        "error",
        "errored",
        "denied",
        "aborted",
        "cancelled",
        "canceled",
        "timeout",
        "timed_out",
        "interrupted",
        "partial",
        "partially_completed",
    }
)
_ROUTINE_SUMMARY_ALLOWED_GAPS: Final = frozenset({"content_unselected", "observation_input_loss"})


def _proven_routine_success(envelope: ObservationEnvelope) -> bool:
    """Apply the same fail-closed outcome precedence as materialization."""

    structural = envelope.structural_payload
    status = structural.get("result_status")
    lowered = status.lower() if type(status) is str else None
    exit_status = structural.get("exit_status")
    if (
        (type(exit_status) is int and not isinstance(exit_status, bool) and exit_status != 0)
        or structural.get("denied") is True
        or structural.get("success") is False
        or lowered in _ROUTINE_FAILURE_STATUSES
    ):
        return False
    return (
        (type(exit_status) is int and not isinstance(exit_status, bool) and exit_status == 0)
        or structural.get("success") is True
        or lowered in _ROUTINE_SUCCESS_STATUSES
    )


def build_routine_read_summary(
    inputs: tuple[ObservationEnvelope, ...],
    fence: str,
) -> ObservationEnvelope:
    """Build one bounded summary envelope from already-proven successes.

    The admission planner calls this only after the post outcome has been
    classified as a successful routine read.  This function still repeats the
    structural checks because the summary is a durable accounting boundary:
    it must not turn a malformed or cross-lane tuple into an apparently
    complete source cursor advance.
    """

    if type(inputs) is not tuple or not 1 <= len(inputs) <= SUMMARY_MAX_INPUTS:
        raise ProtocolValueError("invalid_event_value_type")
    if type(fence) is not str or not fence.startswith("sha256:") or len(fence) != 71:
        raise ProtocolValueError("invalid_event_value_type")
    validate_sha256_digest(fence)
    first = inputs[0]
    if type(first) is not ObservationEnvelope:
        raise ProtocolValueError("invalid_event_value_type")
    source = first.source
    session = first.session_commitment
    generation = first.cursor.source_generation
    selection_route = observation_selection_route(first.structural_payload)
    if selection_route is None:
        raise ProtocolValueError("invalid_event_value_type")
    (
        selection_task_id,
        selection_session_id,
        selection_writer_id,
        selection_authority_generation,
    ) = selection_route
    members: list[JsonObject] = []
    seen: set[tuple[str, int, int, int, str]] = set()
    post_source_ids: set[str] = set()
    open_pre: dict[str, list[ObservationCursor]] = {}
    coverage_gaps: set[str] = set()
    previous_cursor = first.cursor

    for index, envelope in enumerate(inputs):
        if type(envelope) is not ObservationEnvelope:
            raise ProtocolValueError("invalid_event_value_type")
        if (
            envelope.source is not source
            or envelope.session_commitment != session
            or envelope.cursor.source_generation != generation
            or envelope.content_object_refs
        ):
            raise ProtocolValueError("invalid_event_value_type")
        if observation_selection_route(envelope.structural_payload) != selection_route:
            raise ProtocolValueError("invalid_event_value_type")
        if any(gap not in _ROUTINE_SUMMARY_ALLOWED_GAPS for gap in envelope.gap_codes):
            raise ProtocolValueError("invalid_event_value_type")
        coverage_gaps.update(envelope.gap_codes)
        if envelope.structural_payload.get("action") != "routine_read":
            raise ProtocolValueError("invalid_event_value_type")
        if index and envelope.cursor.is_stale_relative_to(previous_cursor):
            raise ProtocolValueError("invalid_event_value_type")
        previous_cursor = envelope.cursor
        if envelope.event_kind == "PreToolUse":
            phase = "pre"
        elif envelope.event_kind == "PostToolUse":
            phase = "post"
        else:
            raise ProtocolValueError("invalid_event_value_type")
        raw_call_id = _call_identity(envelope)
        call_id = raw_call_id if type(raw_call_id) is str and raw_call_id else None
        raw_subject_state = envelope.structural_payload.get("subject_state_digest")
        if raw_subject_state is not None:
            if type(raw_subject_state) is not str:
                raise ProtocolValueError("invalid_event_value_type")
            validate_sha256_digest(raw_subject_state)
        identity = (
            envelope.source_identity,
            envelope.cursor.source_generation,
            envelope.cursor.byte_position,
            envelope.cursor.event_position,
            phase,
        )
        if identity in seen:
            raise ProtocolValueError("invalid_event_value_type")
        seen.add(identity)
        if phase == "pre":
            # A pre identity must be durable until a later post with the exact
            # native call id completes it.  An unidentifiable pre cannot safely
            # enter a successful summary.
            if call_id is None:
                raise ProtocolValueError("invalid_event_value_type")
            open_pre.setdefault(call_id, []).append(envelope.cursor)
        else:
            if not _proven_routine_success(envelope):
                raise ProtocolValueError("invalid_event_value_type")
            if envelope.source_identity in post_source_ids:
                raise ProtocolValueError("invalid_event_value_type")
            post_source_ids.add(envelope.source_identity)
            if call_id is not None and call_id in open_pre:
                pre_cursor = open_pre[call_id][-1]
                # The post must be a later native source position.  Equal
                # positions cannot prove ordering and would let a forged
                # pre/post pair advance the summary cursor ambiguously.
                if not pre_cursor < envelope.cursor:
                    raise ProtocolValueError("invalid_event_value_type")
                open_pre[call_id].pop()
                if not open_pre[call_id]:
                    del open_pre[call_id]
        members.append(
            JsonObject(
                {
                    "source_identity": envelope.source_identity,
                    "cursor": observation_cursor_to_json(envelope.cursor),
                    "tool_call_id": call_id,
                    "phase": phase,
                    "receipt_time": envelope.receipt_time.wire,
                    "subject_state_digest": raw_subject_state,
                }
            )
        )

    if open_pre or not post_source_ids or len(post_source_ids) > SUMMARY_MAX_CALLS:
        raise ProtocolValueError("invalid_event_value_type")
    member_values = tuple(members)
    member_digest = canonical_digest(JsonObject({"members": member_values}))
    identity = routine_read_summary_identity(
        source=source,
        session_commitment=session,
        source_generation=generation,
        selection_policy_version=ROUTINE_READ_SUMMARY_MAPPING_VERSION,
        fence=fence,
        member_digest=member_digest,
        selection_task_id=selection_task_id,
        selection_session_id=selection_session_id,
        selection_writer_id=selection_writer_id,
        selection_authority_generation=selection_authority_generation,
    )
    payload = JsonObject(
        {
            "action": "routine_read_summary",
            "summary_count": len(post_source_ids),
            "input_count": len(member_values),
            "member_digest": member_digest,
            "fence": fence,
            "provenance": ROUTINE_READ_SUMMARY_PROVENANCE,
            "summary_schema": ROUTINE_READ_SUMMARY_SCHEMA,
            "selection_policy_version": ROUTINE_READ_SUMMARY_MAPPING_VERSION,
            "content_scope": ROUTINE_READ_SUMMARY_CONTENT_SCOPE,
            "coverage_gaps": tuple(sorted(coverage_gaps, key=str.encode)),
            "members": member_values,
            "selection_task_id": selection_task_id,
            "selection_session_id": selection_session_id,
            "selection_writer_id": selection_writer_id,
            "selection_authority_generation": selection_authority_generation,
        }
    )
    return ObservationEnvelope(
        session_commitment=session,
        event_kind=SUMMARY_EVENT_KIND,
        source_identity=identity,
        source=source,
        cursor=previous_cursor,
        receipt_time=inputs[-1].receipt_time,
        structural_payload=payload,
        content_object_refs=(),
        gap_codes=(),
    )


def _flush_lane(
    inputs: tuple[BufferedInput, ...], summary_builder: SummaryBuilder
) -> tuple[tuple[str, ObservationEnvelope], ...]:
    """Flush source order; incomplete attempts never enter a success summary."""

    deliveries: list[tuple[str, ObservationEnvelope]] = []
    successes: list[ObservationEnvelope] = []
    previous: BufferedInput | None = None

    def flush_successes() -> None:
        if successes and previous is not None:
            deliveries.append(
                (previous.host_session, summary_builder(tuple(successes), previous.fence))
            )
            successes.clear()

    for item in inputs:
        if previous is not None and (
            item.fence != previous.fence or item.host_session != previous.host_session
        ):
            flush_successes()
        if item.kind == "pending":
            flush_successes()
            # This is still the original native attempt. Its outcome remains
            # pending; flushing never fabricates success or failure.
            deliveries.append((item.host_session, item.envelope))
        else:
            successes.append(item.envelope)
        previous = item
    flush_successes()
    return tuple(deliveries)


def plan_admission(
    buffer: AdmissionBuffer,
    envelope: ObservationEnvelope,
    *,
    host_session: str,
    fence: str,
    focused: bool,
    routine_candidate: bool,
    proven_routine_success: bool,
    now_ms: int,
    summary_builder: SummaryBuilder,
) -> AdmissionPlan:
    """Prepare an atomic transition without mutating accepted pending rows.

    The caller supplies adapter-derived classification and a fence binding the
    actual route, authority generation and subject-state boundary. Unknown
    fences must use individual admission. Successful summaries are prepared
    only before outbox admission, never by compacting accepted outbox rows.
    """

    lane = (envelope.source.value, envelope.session_commitment)
    same_lane = tuple(item for item in buffer.inputs if item.lane == lane)
    other_lanes = tuple(item for item in buffer.inputs if item.lane != lane)
    deliveries: list[tuple[str, ObservationEnvelope]] = []
    if same_lane and any(item.fence != fence for item in same_lane):
        deliveries.extend(_flush_lane(same_lane, summary_builder))
        same_lane = ()
    pending = tuple(item for item in same_lane if item.kind == "pending")
    is_pre = envelope.event_kind == "PreToolUse"
    is_success = envelope.event_kind == "PostToolUse" and proven_routine_success
    eligible = focused and bool(fence) and (routine_candidate if is_pre else is_success)
    if envelope.content_object_refs or any(
        gap not in _ROUTINE_SUMMARY_ALLOWED_GAPS for gap in envelope.gap_codes
    ):
        eligible = False
    if not eligible:
        deliveries.extend(_flush_lane(same_lane, summary_builder))
        deliveries.append((host_session, envelope))
        return AdmissionPlan(AdmissionBuffer(other_lanes), tuple(deliveries), False)

    if pending:
        pre = pending[-1]
        matches = (
            not is_pre
            and _call_identity(pre.envelope) is not None
            and _call_identity(pre.envelope) == _call_identity(envelope)
            and pre.envelope.cursor.source_generation == envelope.cursor.source_generation
        )
        if matches:
            same_lane = tuple(
                replace(item, kind="success") if item is pre else item for item in same_lane
            )
        else:
            # An interleaved operation must not pass an unresolved attempt in
            # source order. Publish the earlier pending attempt first.
            deliveries.extend(_flush_lane(same_lane, summary_builder))
            same_lane = ()

    same_lane += (
        BufferedInput(
            host_session,
            fence,
            envelope,
            "pending" if is_pre else "success",
            now_ms,
        ),
    )
    completed_calls = sum(
        item.kind == "success" and item.envelope.event_kind == "PostToolUse" for item in same_lane
    )
    if (
        completed_calls >= SUMMARY_MAX_CALLS
        or len(same_lane) >= SUMMARY_MAX_INPUTS
        or len(other_lanes) + len(same_lane) > MAX_BUFFERED_INPUTS
    ):
        deliveries.extend(_flush_lane(same_lane, summary_builder))
        return AdmissionPlan(AdmissionBuffer(other_lanes), tuple(deliveries), False)
    return AdmissionPlan(AdmissionBuffer(other_lanes + same_lane), tuple(deliveries), True)


def flush_admission(
    buffer: AdmissionBuffer,
    *,
    now_ms: int,
    summary_builder: SummaryBuilder,
    force: bool = False,
    host_session: str | None = None,
) -> AdmissionPlan:
    """Prepare due summaries/attempts without requiring another host hook.

    A regressed clock flushes conservatively instead of extending retention.
    Force is used for restart, source/authority boundaries and closure.
    """

    lanes: dict[tuple[str, str], list[BufferedInput]] = {}
    for item in buffer.inputs:
        lanes.setdefault(item.lane, []).append(item)
    keep: list[BufferedInput] = []
    deliveries: list[tuple[str, ObservationEnvelope]] = []
    for lane in sorted(lanes):
        entries = lanes[lane]
        selected = host_session is None or any(
            item.host_session == host_session for item in entries
        )
        due = any(
            now_ms < item.buffered_at_ms
            or now_ms - item.buffered_at_ms
            >= (PENDING_ATTEMPT_FLUSH_MS if item.kind == "pending" else SUMMARY_FLUSH_MS)
            for item in entries
        )
        if selected and (force or due):
            deliveries.extend(_flush_lane(tuple(entries), summary_builder))
        else:
            keep.extend(entries)
    return AdmissionPlan(AdmissionBuffer(tuple(keep)), tuple(deliveries), bool(keep))


def admission_buffer_to_json(buffer: AdmissionBuffer) -> JsonValue:
    return tuple(
        JsonObject(
            {
                "host_session": item.host_session,
                "fence": item.fence,
                "envelope": observation_envelope_to_json(item.envelope),
                "kind": item.kind,
                "buffered_at_ms": item.buffered_at_ms,
            }
        )
        for item in buffer.inputs
    )


def admission_buffer_from_json(raw: JsonValue) -> AdmissionBuffer:
    if raw is None:
        return AdmissionBuffer()
    if not isinstance(raw, (tuple, list)) or len(raw) > MAX_BUFFERED_INPUTS:
        raise ProtocolValueError("invalid_event_value_type")
    items: list[BufferedInput] = []
    for value in raw:
        if not isinstance(value, Mapping):
            raise ProtocolValueError("invalid_event_value_type")
        host = value.get("host_session")
        fence = value.get("fence")
        kind = value.get("kind")
        timestamp = value.get("buffered_at_ms")
        envelope = value.get("envelope")
        if (
            type(host) is not str
            or not 1 <= len(host) <= 256
            or type(fence) is not str
            or not fence.startswith("sha256:")
            or len(fence) != 71
            or kind not in {"pending", "success"}
            or type(timestamp) is not int
            or timestamp < 0
            or not isinstance(envelope, Mapping)
        ):
            raise ProtocolValueError("invalid_event_value_type")
        items.append(
            BufferedInput(
                host,
                fence,
                observation_envelope_from_json(JsonObject(envelope)),
                cast(Literal["pending", "success"], kind),
                timestamp,
            )
        )
    return AdmissionBuffer(tuple(items))
