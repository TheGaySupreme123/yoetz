"""Service-side capture, storage and recovery of the check-time change (ADR-031).

The pure case builder never touches Git or the object store. This module is the narrow service
step between them: it records the task-start base when a task is created, captures the check-time
change through ``ChangeCapturePort`` for one check, applies the same capture-time redaction native
observation content receives, stores the result as one encrypted ``change_capture`` object, and
reloads exactly that object when a durable AI-powered review job is recovered. The composition
persists ``CheckChangeOutcome.binding_json()`` in the job's semantic-case object so a replay
rebuilds a byte-identical case instead of re-reading a working tree that has since changed.
"""

from __future__ import annotations

import asyncio
import hmac
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Final, cast

from yoetz.application.semantic_case import CheckTimeChange
from yoetz.domain.privacy import ReviewSelectionPolicy
from yoetz.observability.logging import (
    record_bounded_event_without_raising,
    record_unexpected_exception_without_raising,
)
from yoetz.observability.privacy import redact_sensitive_content
from yoetz.ports.change_capture import (
    CHECK_CHANGE_MEDIA_TYPE,
    MAX_CHECK_CHANGE_TEXT_BYTES,
    TASK_CHANGE_BASE_MEDIA_TYPE,
    ChangeCapturePort,
    ChangeCaptureUnavailable,
    CheckChangeCapture,
    CheckWorkspaceSource,
    TaskChangeBase,
    decode_check_change,
    decode_task_change_base,
    encode_check_change,
    encode_task_change_base,
)
from yoetz.ports.clock import ClockPort
from yoetz.ports.objects import ObjectKind, ObjectMetadata, ObjectRef, ObjectSource
from yoetz.ports.runtime import TaskRuntime
from yoetz.protocol.canonical import JsonValue

__all__ = [
    "CheckChangeOutcome",
    "capture_check_time_change",
    "check_time_change_selected",
    "record_task_change_base",
    "recover_check_time_change",
]

_COMPONENT: Final = "semantic_composition"
_MAX_REDACTION_PASSES: Final = 64


@dataclass(frozen=True, slots=True)
class CheckChangeOutcome:
    """What the service could provide for one check: the change, its absence, or neither.

    ``NOT_OFFERED`` (no change and not unavailable) means no trusted workspace source was bound,
    as for an embedded application without a control session; the case then carries neither the
    change nor a gap, exactly as before ADR-031.
    """

    change: CheckTimeChange | None = None
    unavailable: bool = False

    def __post_init__(self) -> None:
        if self.change is not None and type(self.change) is not CheckTimeChange:
            raise TypeError("check_change_outcome_invalid")
        if type(self.unavailable) is not bool or (self.unavailable and self.change is not None):
            raise ValueError("check_change_outcome_invalid")

    @property
    def offered(self) -> bool:
        return self.change is not None or self.unavailable

    def binding_json(self) -> JsonValue:
        """The durable pointer a recovered job reloads; never content."""

        ref = None if self.change is None else self.change.object_ref
        return cast(
            JsonValue,
            {
                "object": None
                if ref is None
                else {
                    "commitment": ref.commitment,
                    "envelope_digest": ref.envelope_digest,
                    "object_id": ref.object_id,
                },
                "schema": "yoetz.check-change-binding/1",
                "unavailable": self.unavailable,
            },
        )


def check_time_change_selected(selection: ReviewSelectionPolicy) -> bool:
    """Whether this review recipe would carry a check-time change at all."""

    return selection.carries_check_time_change


async def _read(runtime: TaskRuntime, ref: ObjectRef) -> bytes:
    return b"".join([chunk async for chunk in runtime.objects.open_verified(ref)])


async def _store(runtime: TaskRuntime, data: bytes, media_type: str, clock: ClockPort) -> ObjectRef:
    staged = await runtime.objects.stage(
        ObjectSource(data=data, declared_size=len(data)),
        ObjectMetadata(ObjectKind.CHANGE_CAPTURE, media_type, runtime.task_id, clock.now_utc()),
    )
    return await runtime.objects.finalize(staged)


async def _resolve(
    runtime: TaskRuntime, object_id: str, envelope_digest: str, media_type: str
) -> ObjectRef:
    ref = await runtime.objects.resolve_verified(object_id, envelope_digest)
    if (
        type(ref) is not ObjectRef
        or ref.metadata.kind is not ObjectKind.CHANGE_CAPTURE
        or ref.metadata.media_type != media_type
        or ref.metadata.task_id != runtime.task_id
    ):
        raise ValueError("check_change_object_invalid")
    return ref


async def _load_task_base(runtime: TaskRuntime, request_id: str) -> TaskChangeBase | None:
    loader = getattr(runtime.ledger, "load_task_change_base", None)
    if not callable(loader):
        return None
    try:
        ref = await cast(Callable[[], Awaitable[ObjectRef | None]], loader)()
        if ref is None:
            return None
        if (
            ref.metadata.kind is not ObjectKind.CHANGE_CAPTURE
            or ref.metadata.media_type != TASK_CHANGE_BASE_MEDIA_TYPE
            or ref.metadata.task_id != runtime.task_id
        ):
            raise ValueError("task_change_base_invalid")
        return decode_task_change_base(await _read(runtime, ref))
    except Exception as exc:
        # An unreadable base degrades to the HEAD base, which the case discloses as
        # ``check_time_change_base_unavailable``; it never blocks the review.
        record_unexpected_exception_without_raising(
            exc,
            component=_COMPONENT,
            operation="check_time_change_base_unreadable",
            request_id=request_id,
        )
        return None


def _redacted(capture: CheckChangeCapture) -> CheckChangeCapture:
    """Apply native capture's redaction; the egress never-send scan still runs on every part.

    One scanner call reports a bounded number of findings, and source code such as a parser can
    hold more credential-shaped assignments (``token = ...``) than that. Scanning again until a
    pass finds nothing redacts every span; text that still has findings after the pass bound is
    withheld whole, never offered partly redacted.
    """

    text = capture.text
    redacted = False
    for _ in range(_MAX_REDACTION_PASSES):
        replaced, detected = redact_sensitive_content(text)
        if not detected:
            break
        text, redacted = replaced, True
    else:
        raise ChangeCaptureUnavailable("redaction_incomplete")
    if not redacted:
        return capture
    text = text.decode("utf-8", errors="replace").encode("utf-8")
    truncated = capture.truncated
    if len(text) > MAX_CHECK_CHANGE_TEXT_BYTES:
        text = text[:MAX_CHECK_CHANGE_TEXT_BYTES].decode("utf-8", errors="ignore").encode("utf-8")
        truncated = True
    return replace(capture, text=text, redacted=True, truncated=truncated)


def _capture_redacted(
    port: ChangeCapturePort, workspace: str, base: TaskChangeBase | None
) -> CheckChangeCapture:
    capture = port.capture(workspace, base)
    if type(capture) is not CheckChangeCapture:
        raise TypeError("check_change_capture_invalid")
    return _redacted(capture)


async def capture_check_time_change(
    *,
    runtime: TaskRuntime,
    source: CheckWorkspaceSource,
    route_repository_commitment: str,
    port: ChangeCapturePort,
    clock: ClockPort,
    request_id: str,
) -> CheckChangeOutcome:
    """Capture, redact and store one check's change; every failure is a disclosed absence."""

    if type(source) is not CheckWorkspaceSource:
        raise TypeError("check_workspace_source_invalid")
    if not hmac.compare_digest(
        source.repository_commitment.encode("ascii"),
        route_repository_commitment.encode("ascii"),
    ):
        # The connection's workspace is not the task's repository. Never read it for this task.
        record_bounded_event_without_raising(
            component=_COMPONENT,
            operation="check_time_change_unavailable",
            reason="repository_mismatch",
            request_id=request_id,
        )
        return CheckChangeOutcome(unavailable=True)
    base = await _load_task_base(runtime, request_id)
    try:
        # Capture and every redaction pass run off the event loop: a parser-sized change needs
        # dozens of full scans, which would otherwise stall every other connection for seconds.
        capture = await asyncio.to_thread(_capture_redacted, port, source.workspace, base)
        ref = await _store(runtime, encode_check_change(capture), CHECK_CHANGE_MEDIA_TYPE, clock)
        return CheckChangeOutcome(CheckTimeChange(ref, capture))
    except ChangeCaptureUnavailable as exc:
        record_bounded_event_without_raising(
            component=_COMPONENT,
            operation="check_time_change_unavailable",
            reason=exc.reason,
            request_id=request_id,
        )
    except Exception as exc:
        record_unexpected_exception_without_raising(
            exc,
            component=_COMPONENT,
            operation="check_time_change_capture_failed",
            request_id=request_id,
        )
    return CheckChangeOutcome(unavailable=True)


async def recover_check_time_change(runtime: TaskRuntime, binding: object) -> CheckChangeOutcome:
    """Reload the exact outcome a durable job froze; raise when it cannot be authenticated."""

    if type(binding) is not dict:
        raise ValueError("check_change_binding_invalid")
    source = cast(dict[str, object], binding)
    if set(source) != {"object", "schema", "unavailable"} or (
        source["schema"] != "yoetz.check-change-binding/1"
    ):
        raise ValueError("check_change_binding_invalid")
    unavailable = source["unavailable"]
    pointer = source["object"]
    if type(unavailable) is not bool:
        raise ValueError("check_change_binding_invalid")
    if pointer is None:
        return CheckChangeOutcome(unavailable=unavailable)
    if unavailable or type(pointer) is not dict:
        raise ValueError("check_change_binding_invalid")
    fields = cast(dict[str, object], pointer)
    if set(fields) != {"commitment", "envelope_digest", "object_id"}:
        raise ValueError("check_change_binding_invalid")
    ref = await _resolve(
        runtime,
        cast(str, fields["object_id"]),
        cast(str, fields["envelope_digest"]),
        CHECK_CHANGE_MEDIA_TYPE,
    )
    if ref.commitment != fields["commitment"]:
        raise ValueError("check_change_binding_invalid")
    return CheckChangeOutcome(CheckTimeChange(ref, decode_check_change(await _read(runtime, ref))))


async def record_task_change_base(
    *,
    runtime: TaskRuntime,
    port: ChangeCapturePort,
    workspace: str,
    clock: ClockPort,
    request_id: str | None = None,
) -> bool:
    """Record HEAD as the task-start base once; ``False`` when kept already or not possible.

    Called right after a task is created, before the creating request returns, so no agent edit
    or commit can precede it. A failure leaves the task without a base; its checks then show the
    change against HEAD and disclose ``check_time_change_base_unavailable``.
    """

    recorder = getattr(runtime.ledger, "record_task_change_base", None)
    loader = getattr(runtime.ledger, "load_task_change_base", None)
    if not callable(recorder) or not callable(loader):
        return False
    try:
        if await cast(Callable[[], Awaitable[ObjectRef | None]], loader)() is not None:
            return False
        base = await asyncio.to_thread(port.read_task_base, workspace)
        if type(base) is not TaskChangeBase:
            raise TypeError("task_change_base_invalid")
        ref = await _store(
            runtime, encode_task_change_base(base), TASK_CHANGE_BASE_MEDIA_TYPE, clock
        )
        return await cast(Callable[[ObjectRef], Awaitable[bool]], recorder)(ref)
    except ChangeCaptureUnavailable as exc:
        record_bounded_event_without_raising(
            component="start",
            operation="task_change_base_unavailable",
            reason=exc.reason,
            request_id=request_id,
        )
    except Exception as exc:
        record_unexpected_exception_without_raising(
            exc,
            component="start",
            operation="task_change_base_record_failed",
            request_id=request_id,
        )
    return False
