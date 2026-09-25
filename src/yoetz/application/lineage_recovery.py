"""Authenticated, retry-safe service evidence for lineage abandonment."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from typing import TYPE_CHECKING, cast

from yoetz.application.lineage import (
    DelegationOperation,
    DelegationPhase,
    LineageCoordinator,
    LineageSnapshot,
)
from yoetz.application.start import provision_lineage_recovery
from yoetz.application.unit_of_work import PreparedMutation, run_prepared_append
from yoetz.domain.coordination import SessionHealth
from yoetz.domain.events import (
    LINEAGE_EVENT_SCHEMA_VERSION,
    OBSERVATION_COORDINATOR_ACTOR_ID,
    AcceptedEvent,
    EventDraft,
    EventSchema,
    WorkAbandonedPayload,
    encode_payload,
    media_type_for,
)
from yoetz.domain.values import Actor, ActorType, actor_id, event_id, timestamp_from_datetime
from yoetz.ports.clock import ClockPort
from yoetz.ports.diagnostics import RuntimeCapability
from yoetz.ports.ledger import AppendCommand, AppendEntry, OperationKind
from yoetz.ports.objects import ObjectKind, ObjectMetadata, ObjectSource
from yoetz.ports.runtime import RouteAccess, RouteCommand, TaskRuntime
from yoetz.ports.start_catalog import SessionBinding, SessionState, TaskRouteState
from yoetz.protocol.canonical import canonical_digest, canonical_encode
from yoetz.protocol.coverage import AuthorshipAssurance, PublicationChannel, coverage_for_channel
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError

if TYPE_CHECKING:
    from yoetz.application.service import Application


async def extend_session_lease(
    start_catalog: object,
    clock: ClockPort,
    *,
    task_id: str,
    session_id: str,
    lease_until: datetime,
) -> None:
    """Hold one session active until ``lease_until`` without shortening a later lease.

    Evidence-anchored renewal and the in-flight hold both end before a lease that a newer
    workflow call or host event already recorded; only the later end is kept.
    """

    record = getattr(start_catalog, "record_session_state", None)
    now = clock.now_utc()
    if not callable(record) or lease_until <= now:
        # Evidence that ran out while this call waited cannot hold the session active now.
        return
    lookup = getattr(start_catalog, "task_session_state", None)
    if callable(lookup):
        current = await cast(Callable[[str], Awaitable[SessionState | None]], lookup)(session_id)
        if (
            current is not None
            and current.health is SessionHealth.ACTIVE
            and current.lease_expires_at is not None
            and current.lease_expires_at >= lease_until
        ):
            return
    await cast(Callable[..., Awaitable[object]], record)(
        task_id,
        session_id,
        health=SessionHealth.ACTIVE,
        changed_at=now,
        lease_expires_at=lease_until,
    )


def observed_activity_renewal(
    start_catalog: object,
    lineage: LineageCoordinator,
    clock: ClockPort,
) -> Callable[[str, str, str, datetime], Awaitable[None]]:
    """Build the observation hook that offers admitted host activity as contact evidence.

    The catalog binding is revalidated first: evidence renews only the exact current session and
    writer the observation was routed to.  ``observed_at`` is the host event's receipt time.
    """

    binding_lookup = getattr(start_catalog, "session_binding", None)

    async def renew(task_id: str, session_id: str, writer_id: str, observed_at: datetime) -> None:
        if not callable(binding_lookup):
            return
        binding = await cast(Callable[[str], Awaitable[SessionBinding | None]], binding_lookup)(
            session_id
        )
        if (
            binding is None
            or binding.task_id != task_id
            or binding.session_id != session_id
            or binding.writer_id != writer_id
        ):
            return

        async def renew_lease(lease_until: datetime) -> None:
            await extend_session_lease(
                start_catalog,
                clock,
                task_id=task_id,
                session_id=session_id,
                lease_until=lease_until,
            )

        await lineage.renew_observed_activity(
            task_id=task_id,
            session_id=session_id,
            renew_lease=renew_lease,
            observed_at=observed_at,
        )

    return renew


async def lineage_recovery_runtime(app: Application, task_id: str) -> TaskRuntime:
    """Open a current route or the inert bundle of one completed reservation."""

    route = await app.start_catalog.task_route(task_id)
    if route is not None and route.state is TaskRouteState.INITIALIZING and app.lineage is not None:
        store = app.lineage.store
        listing = getattr(store, "list_operations", None)
        if callable(listing):
            operations = await cast(
                Callable[[], Awaitable[tuple[DelegationOperation, ...]]], listing
            )()
        else:
            operations = tuple(
                cast(Mapping[str, DelegationOperation], getattr(store, "operations", {})).values()
            )
        for operation in operations:
            if operation.child_task_id == task_id and operation.phase is DelegationPhase.TERMINAL:
                return await provision_lineage_recovery(app, operation)
    if route is None or route.state is not TaskRouteState.ACTIVE:
        raise PublicOperationError(
            PublicErrorCode.SESSION_NOT_FOUND,
            "The task route is unavailable for lifecycle recovery.",
            True,
        )
    binding = await app.start_catalog.session_binding(route.session_id)
    if binding is None or binding.task_id != task_id:
        raise PublicOperationError(
            PublicErrorCode.STORAGE_CORRUPT, "The task recovery identity is inconsistent.", False
        )
    return await app.runtime.route(
        RouteCommand(
            route.session_id,
            binding.writer_id,
            RouteAccess.MAINTENANCE,
            frozenset({RuntimeCapability.WRITE, RuntimeCapability.PAYLOAD_READ}),
        )
    )


async def append_abandonment(
    app: Application, snapshot: LineageSnapshot, reason: str, deadline: datetime
) -> None:
    """Append before catalog mutation, with one stable event identity per task.

    Work has no transition out of abandonment. A task-derived identity therefore survives a
    process failure, changing writer, or a refreshed recovery deadline without duplicating the
    event. Recovery holds the lineage transition lock until the catalog mirrors this evidence.
    """

    runtime = await lineage_recovery_runtime(app, snapshot.task_id)
    writer_id = runtime.writer_id
    assert writer_id is not None  # recovery runtimes are write-capable
    try:
        # The payload read also covers an append committed under a predecessor writer. No new
        # operation or object is created when its authenticated event already exists.
        sessions = await app.start_catalog.task_session_states(snapshot.task_id)
        for session in {runtime.session_id, *(state.session_id for state in sessions)}:
            async for record in runtime.ledger.load_events(session):
                if isinstance(record, AcceptedEvent) and isinstance(
                    record.payload, WorkAbandonedPayload
                ):
                    return
        payload = WorkAbandonedPayload(reason_code=reason)
        digest = canonical_digest({"task_id": snapshot.task_id, "kind": "work_abandoned"})
        raw = bytearray(bytes.fromhex(digest.removeprefix("sha256:")[:32]))
        raw[6] = (raw[6] & 0x0F) | 0x40
        raw[8] = (raw[8] & 0x3F) | 0x80
        identity = str(uuid.UUID(bytes=bytes(raw)))
        operation_id = "req_" + identity
        schema = EventSchema("work_abandoned", LINEAGE_EVENT_SCHEMA_VERSION)
        metadata = ObjectMetadata(
            ObjectKind.EVENT_PAYLOAD, media_type_for(schema.name), snapshot.task_id, deadline
        )
        payload_ref = await runtime.objects.finalize(
            await runtime.objects.stage(
                ObjectSource(data=canonical_encode(encode_payload(payload))), metadata
            )
        )
        draft = EventDraft(
            event_id=event_id("evt_" + identity),
            schema=schema,
            occurred_at=timestamp_from_datetime(deadline),
            causal_parents=(),
            payload=payload,
            artifact_refs=(),
            evidence_refs=(),
        )
        entry = AppendEntry(
            draft=draft,
            author=Actor(
                actor_id(OBSERVATION_COORDINATOR_ACTOR_ID),
                ActorType.HARNESS,
                AuthorshipAssurance.HARNESS_OBSERVED,
            ),
            payload_object=payload_ref,
            payload_commitment=payload_ref.commitment,
            media_type=metadata.media_type,
            plaintext_size=payload_ref.plaintext_size,
            publication_channel=PublicationChannel.ENGINE_DERIVED,
            coverage=coverage_for_channel(PublicationChannel.ENGINE_DERIVED),
            projection_status="projected",
        )
        frontier = await runtime.ledger.load_frontier()
        await run_prepared_append(
            runtime.ledger,
            PreparedMutation(
                writer_id=writer_id,
                operation_id=operation_id,
                request_digest=digest,
                expected_frontier=frontier.sequence,
                finalized_object_refs=(payload_ref,),
                command=AppendCommand(
                    task_id=snapshot.task_id,
                    session_id=runtime.session_id,
                    writer_id=writer_id,
                    operation_id=operation_id,
                    operation_kind=OperationKind.PUBLISH_WORK,
                    request_digest=digest,
                    expected_frontier=frontier.sequence,
                    entries=(entry,),
                ),
            ),
        )
    finally:
        await app.runtime.release(runtime)
