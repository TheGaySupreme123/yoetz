"""Advice-first overlap detection and generation-fenced delivery.

Detectors consume explicit, attributable declarations.  They never inspect a shared workspace
diff, infer a path from prose, or turn a presence/overlap signal into a finding by themselves.
The detector records one identity for a task pair and resource set, then delivers idempotent
advice to each target.  A finding becomes eligible only after the task has explicitly declared or
accepted a coordination obligation; a disposition records that the obligation was addressed and
only a later qualifying check may mark it resolved.
"""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal, Protocol, cast

from yoetz.application.observation_materialize import (
    observation_author,
    observation_writer_id,
    stable_observation_id,
)
from yoetz.application.projects import ProjectApplication
from yoetz.application.unit_of_work import PreparedMutation, run_prepared_append
from yoetz.domain.coordination import (
    COORDINATION_DETAIL_FORMAT,
    CoordinationAdmission,
    CoordinationCoverage,
    CoordinationDetection,
    CoordinationError,
    CoordinationErrorCode,
    CoordinationGapCode,
    CoordinationObligationState,
    OverlapKind,
    ProjectTextRef,
    canonical_resource_identity,
    coordination_detection_identity,
    overlap_resource_commitments,
    project_id,
    relative_resource_identity,
)
from yoetz.domain.events import (
    COORDINATION_EVENT_SCHEMA_VERSION,
    AcceptedEvent,
    ActionKind,
    ActionRecordedPayload,
    CoordinationContextRecordedPayload,
    CoordinationObligationDeclaredPayload,
    EventDraft,
    EventSchema,
    EvidenceRecordedPayload,
    LedgerRecord,
    ObligationPublishedPayload,
    ObligationStatus,
    PlanPublishedPayload,
    PlanRevisedPayload,
    RequestedItemKind,
    ResultRecordedPayload,
    UnknownEvent,
    encode_payload,
    media_type_for,
)
from yoetz.domain.values import (
    JsonObject,
    JsonValue,
    event_id,
    obligation_id,
    task_id,
    timestamp_from_datetime,
    validate_commitment,
    validate_sha256_digest,
)
from yoetz.ports.clock import ClockPort
from yoetz.ports.diagnostics import RuntimeCapability
from yoetz.ports.ledger import AppendCommand, AppendEntry, OperationKind
from yoetz.ports.objects import ObjectKind, ObjectMetadata, ObjectRef, ObjectSource, ObjectStorePort
from yoetz.ports.runtime import BundleRuntimePort, RouteAccess, RouteCommand, TaskRuntime
from yoetz.ports.start_catalog import TaskRoute, TaskRouteState
from yoetz.protocol.canonical import canonical_digest, canonical_encode, strict_json_parse
from yoetz.protocol.coverage import PublicationChannel, coverage_for_channel
from yoetz.protocol.ids import IdKind, validate_id

__all__ = [
    "CoordinationAdvice",
    "CoordinationResourceProjection",
    "CoordinationCoverage",
    "CoordinationDetector",
    "CoordinationDelivery",
    "CoordinationDeliveryStore",
    "CoordinationDetailStore",
    "CoordinationDetailReader",
    "CoordinationContextWriter",
    "EncryptedCoordinationDetailStore",
    "RoutedCoordinationContextWriter",
    "CoordinationObligationState",
    "CoordinationParticipant",
    "DeclaredCoordinationInput",
    "InMemoryCoordinationStore",
    "structured_plan_identity",
    "LedgerCoordinationInputProvider",
    "CoordinationRuntime",
    "build_coordination_delivery_store",
    "build_coordination_detector",
    "build_coordination_input_provider",
    "build_coordination_runtime",
]


type _AwaitableValue[T] = T | Awaitable[T]
type _ResourceProvider = Callable[[str], _AwaitableValue[Sequence[str] | None]]


def _obligation(value: object) -> str:
    if type(value) is not str:
        raise CoordinationError(CoordinationErrorCode.INVALID)
    try:
        return validate_id(IdKind.OBLIGATION, value)
    except (TypeError, ValueError) as exc:
        raise CoordinationError(CoordinationErrorCode.INVALID) from exc


def _positive(value: object) -> int:
    if type(value) is not int or value < 1:
        raise CoordinationError(CoordinationErrorCode.INVALID)
    return value


def _task(value: object) -> str:
    if type(value) is not str:
        raise CoordinationError(CoordinationErrorCode.INVALID)
    try:
        return validate_id(IdKind.TASK, value)
    except (TypeError, ValueError) as exc:
        raise CoordinationError(CoordinationErrorCode.INVALID) from exc


def _commitment(value: object) -> str:
    if type(value) is not str:
        raise CoordinationError(CoordinationErrorCode.INVALID)
    try:
        return validate_commitment(value)
    except (TypeError, ValueError) as exc:
        raise CoordinationError(CoordinationErrorCode.INVALID) from exc


def _project(value: object) -> str:
    try:
        return project_id(value)
    except (TypeError, ValueError) as exc:
        raise CoordinationError(CoordinationErrorCode.INVALID) from exc


def _awaitable[T](value: _AwaitableValue[T]) -> Awaitable[T]:
    if inspect.isawaitable(value):
        return cast(Awaitable[T], value)

    async def immediate() -> T:
        return value

    return immediate()


def _is_mapping(value: object) -> bool:
    return isinstance(value, Mapping)


@dataclass(frozen=True, slots=True, repr=False)
class DeclaredCoordinationInput:
    """Attributable detector input from one task.

    ``resources`` and ``structured_items`` remain private to the detector.  ``as_wire`` emits
    only repository-bound commitments and digests, so caller controlled paths and plan prose never
    enter structural advice or findings.
    """

    task_id: str
    project_id: str
    repository_commitment: str
    workspace_commitment: str
    route_generation: int
    resources: tuple[str, ...] = ()
    structured_items: tuple[Mapping[str, JsonValue], ...] = ()
    case_sensitive: bool = True
    source_has_attributable_paths: bool = True
    obligation_ids: tuple[str, ...] = ()
    coordination_declarations: tuple[CoordinationObligationDeclaredPayload, ...] = ()
    # Internal provenance only.  It is deliberately absent from ``as_wire``: route identity is
    # used to derive a successor detection id, while the released coordination input schema stays
    # byte-compatible.
    route_identity_digest: str | None = None

    def __post_init__(self) -> None:
        _task(self.task_id)
        try:
            project_id(self.project_id)
        except ValueError as exc:
            raise CoordinationError(CoordinationErrorCode.INVALID) from exc
        _commitment(self.repository_commitment)
        _commitment(self.workspace_commitment)
        _positive(self.route_generation)
        if self.route_identity_digest is not None:
            try:
                validate_sha256_digest(self.route_identity_digest)
            except (TypeError, ValueError) as exc:
                raise CoordinationError(CoordinationErrorCode.INVALID) from exc
        if type(self.resources) is not tuple or len(self.resources) > 256:
            raise CoordinationError(CoordinationErrorCode.INVALID)
        # Validate each declaration and reject duplicate/sorted ambiguity at the boundary.  The
        # detector may then safely canonicalize identities without guessing missing paths.
        normalized = tuple(relative_resource_identity(value) for value in self.resources)
        if len(set(normalized)) != len(normalized):
            raise CoordinationError(CoordinationErrorCode.INVALID)
        if normalized != tuple(sorted(normalized, key=str.encode)):
            raise CoordinationError(CoordinationErrorCode.INVALID)
        if type(self.structured_items) is not tuple or len(self.structured_items) > 256:
            raise CoordinationError(CoordinationErrorCode.INVALID)
        if any(not _is_mapping(item) for item in self.structured_items):
            raise CoordinationError(CoordinationErrorCode.INVALID)
        if (
            type(self.case_sensitive) is not bool
            or type(self.source_has_attributable_paths) is not bool
        ):
            raise CoordinationError(CoordinationErrorCode.INVALID)
        if type(self.obligation_ids) is not tuple or len(self.obligation_ids) > 256:
            raise CoordinationError(CoordinationErrorCode.INVALID)
        obligation_ids = tuple(_obligation(value) for value in self.obligation_ids)
        if obligation_ids != tuple(sorted(set(obligation_ids), key=str.encode)):
            raise CoordinationError(CoordinationErrorCode.INVALID)
        if (
            type(self.coordination_declarations) is not tuple
            or len(self.coordination_declarations) > 64
        ):
            raise CoordinationError(CoordinationErrorCode.INVALID)
        if any(
            type(item) is not CoordinationObligationDeclaredPayload
            for item in self.coordination_declarations
        ):
            raise CoordinationError(CoordinationErrorCode.INVALID)
        if any(
            item.project_id != self.project_id or item.recipient_task_id != self.task_id
            for item in self.coordination_declarations
        ):
            raise CoordinationError(CoordinationErrorCode.INVALID)

    def resource_identities(self) -> tuple[str, ...]:
        return tuple(
            canonical_resource_identity(
                value,
                repository_commitment=self.repository_commitment,
                case_sensitive=self.case_sensitive,
            )
            for value in self.resources
        )

    def plan_identities(self) -> tuple[str, ...]:
        identities = [
            structured_plan_identity(
                item,
                repository_commitment=self.repository_commitment,
            )
            for item in self.structured_items
        ]
        return tuple(sorted({item for item in identities if item is not None}, key=str.encode))

    def as_wire(self) -> JsonObject:
        values: dict[str, JsonValue] = {
            "task_id": self.task_id,
            "project_id": self.project_id,
            "repository_commitment": self.repository_commitment,
            "workspace_commitment": self.workspace_commitment,
            "route_generation": str(self.route_generation),
            "resource_identities": cast(JsonValue, list(self.resource_identities())),
            "plan_identities": cast(JsonValue, list(self.plan_identities())),
            "case_sensitive": self.case_sensitive,
            "source_has_attributable_paths": self.source_has_attributable_paths,
            "obligation_ids": cast(JsonValue, list(self.obligation_ids)),
            "coordination_declaration_ids": cast(
                JsonValue,
                [item.detection_id for item in self.coordination_declarations],
            ),
        }
        return JsonObject(values)


@dataclass(frozen=True, slots=True)
class CoordinationAdvice:
    """One structural delivery to one task."""

    detection_id: str
    target_task_id: str
    counterpart_task_id: str
    project_id: str
    membership_generation: int
    overlap_kind: OverlapKind
    resource_identities: tuple[str, ...]
    resource_count: int
    coverage: Literal["complete", "unobservable", "truncated"] = "complete"

    def __post_init__(self) -> None:
        try:
            validate_id(IdKind.EVENT, self.detection_id)
        except (TypeError, ValueError) as exc:
            raise CoordinationError(CoordinationErrorCode.INVALID) from exc
        _task(self.target_task_id)
        _task(self.counterpart_task_id)
        if self.target_task_id == self.counterpart_task_id:
            raise CoordinationError(CoordinationErrorCode.INVALID)
        try:
            project_id(self.project_id)
        except ValueError as exc:
            raise CoordinationError(CoordinationErrorCode.INVALID) from exc
        _positive(self.membership_generation)
        if type(self.overlap_kind) is not OverlapKind:
            raise CoordinationError(CoordinationErrorCode.INVALID)
        if type(self.resource_identities) is not tuple or not self.resource_identities:
            raise CoordinationError(CoordinationErrorCode.INVALID)
        try:
            resources = tuple(sorted(self.resource_identities, key=str.encode))
            if resources != self.resource_identities or len(set(resources)) != len(resources):
                raise ValueError("coordination_resources_invalid")
            for resource in resources:
                validate_sha256_digest(resource)
        except (TypeError, UnicodeEncodeError, ValueError) as exc:
            raise CoordinationError(CoordinationErrorCode.INVALID) from exc
        if type(self.resource_count) is not int or not 1 <= self.resource_count <= 256:
            raise CoordinationError(CoordinationErrorCode.INVALID)
        if self.resource_count < len(self.resource_identities):
            raise CoordinationError(CoordinationErrorCode.INVALID)
        if self.coverage not in {"complete", "unobservable", "truncated"}:
            raise CoordinationError(CoordinationErrorCode.INVALID)

    def as_wire(self) -> JsonObject:
        return JsonObject(
            {
                "detection_id": self.detection_id,
                "target_task_id": self.target_task_id,
                "counterpart_task_id": self.counterpart_task_id,
                "project_id": self.project_id,
                "membership_generation": str(self.membership_generation),
                "overlap_kind": self.overlap_kind.value,
                "resource_identities": cast(JsonValue, list(self.resource_identities)),
                "resource_count": self.resource_count,
                "coverage": self.coverage,
            }
        )


@dataclass(frozen=True, slots=True)
class CoordinationResourceProjection:
    """Recipient-scoped resource detail hydrated from one encrypted detection object.

    ``resource_paths`` is intentionally absent from every structural coordination row.  A value
    is produced only after the application re-admits both participants and the source-owner
    disclosure gate.  ``None`` means the detail was withheld or did not contain a revealable
    repository-relative overlap; callers must render that as an explicit omission.
    """

    detection_id: str
    project_id: str
    membership_generation: int
    counterpart_task_id: str
    resource_paths: tuple[str, ...] | None
    source_disclosure_permitted: bool

    def __post_init__(self) -> None:
        try:
            validate_id(IdKind.EVENT, self.detection_id)
            project_id(self.project_id)
            validate_id(IdKind.TASK, self.counterpart_task_id)
        except (TypeError, ValueError) as exc:
            raise CoordinationError(CoordinationErrorCode.INVALID) from exc
        _positive(self.membership_generation)
        if type(self.source_disclosure_permitted) is not bool:
            raise CoordinationError(CoordinationErrorCode.INVALID)
        if self.resource_paths is not None:
            if type(self.resource_paths) is not tuple or not self.resource_paths:
                raise CoordinationError(CoordinationErrorCode.INVALID)
            try:
                normalized = tuple(relative_resource_identity(item) for item in self.resource_paths)
            except (TypeError, ValueError) as exc:
                raise CoordinationError(CoordinationErrorCode.INVALID) from exc
            if normalized != self.resource_paths or len(set(normalized)) != len(normalized):
                raise CoordinationError(CoordinationErrorCode.INVALID)
            if len(normalized) > 256:
                raise CoordinationError(CoordinationErrorCode.INVALID)


@dataclass(frozen=True, slots=True)
class CoordinationDelivery:
    detection_id: str
    target_task_id: str
    outcome: Literal["delivered", "duplicate", "refused"]
    expected_generation: int
    observed_generation: int
    reason_code: str | None = None

    def __post_init__(self) -> None:
        try:
            validate_id(IdKind.EVENT, self.detection_id)
        except (TypeError, ValueError) as exc:
            raise CoordinationError(CoordinationErrorCode.INVALID) from exc
        _task(self.target_task_id)
        _positive(self.expected_generation)
        _positive(self.observed_generation)
        if self.outcome not in {"delivered", "duplicate", "refused"}:
            raise CoordinationError(CoordinationErrorCode.INVALID)
        if self.outcome == "refused" and not self.reason_code:
            raise CoordinationError(CoordinationErrorCode.INVALID)
        if self.reason_code is not None:
            if (
                type(self.reason_code) is not str
                or not self.reason_code
                or len(self.reason_code.encode("utf-8")) > 128
                or any(
                    char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:-"
                    for char in self.reason_code
                )
            ):
                raise CoordinationError(CoordinationErrorCode.INVALID)

    def as_wire(self) -> JsonObject:
        values: dict[str, JsonValue] = {
            "detection_id": self.detection_id,
            "target_task_id": self.target_task_id,
            "outcome": self.outcome,
            "expected_generation": str(self.expected_generation),
            "observed_generation": str(self.observed_generation),
        }
        if self.reason_code is not None:
            values["reason_code"] = self.reason_code
        return JsonObject(values)


@dataclass(frozen=True, slots=True)
class CoordinationParticipant:
    """Durable structural admission facts needed to retry a delivery.

    Raw resource names and plan material stay in the encrypted detail object.  Persisting only
    these commitments makes a crash-safe retry possible without putting paths or prose into the
    catalog/delivery tables.
    """

    task_id: str
    project_id: str
    repository_commitment: str
    workspace_commitment: str
    route_generation: int
    source_has_attributable_paths: bool = True

    def __post_init__(self) -> None:
        _task(self.task_id)
        try:
            project_id(self.project_id)
        except ValueError as exc:
            raise CoordinationError(CoordinationErrorCode.INVALID) from exc
        _commitment(self.repository_commitment)
        _commitment(self.workspace_commitment)
        _positive(self.route_generation)
        if type(self.source_has_attributable_paths) is not bool:
            raise CoordinationError(CoordinationErrorCode.INVALID)


class CoordinationDetailStore(Protocol):
    """Encrypted detail sink for raw path names and structured plan excerpts."""

    async def put_details(
        self,
        detection_id: str,
        details: JsonObject,
        *,
        owner_task_id: str,
        route_generation: int,
    ) -> ProjectTextRef: ...

    async def read_details(self, reference: ProjectTextRef) -> JsonObject: ...


class CoordinationDetailReader(Protocol):
    """Read one generation-bound encrypted coordination detail object."""

    async def read_details(self, reference: ProjectTextRef) -> JsonObject: ...


class CoordinationContextWriter(Protocol):
    """Append service-stamped coordination context to the recipient's own task ledger."""

    async def record_context(
        self,
        detection: CoordinationDetection,
        recipient: CoordinationParticipant,
        source: CoordinationParticipant,
    ) -> str: ...


class RoutedCoordinationContextWriter:
    """Write generation-fenced coordination context through the recipient task route.

    The context append is the receipt that makes a delivery real.  It re-admits both sides just
    before staging the payload, so a revoke or route rotation between detection and delivery can
    never leave a terminal advice row without a recipient-ledger event.
    """

    def __init__(
        self,
        projects: ProjectApplication,
        runtime: BundleRuntimePort,
        *,
        clock: ClockPort | None = None,
    ) -> None:
        if not callable(getattr(projects, "admit", None)):
            raise TypeError("coordination_context_projects_invalid")
        if not callable(getattr(runtime, "route", None)):
            raise TypeError("coordination_context_runtime_invalid")
        self.projects = projects
        self.runtime = runtime
        self.clock = clock

    async def record_context(
        self,
        detection: CoordinationDetection,
        recipient: CoordinationParticipant,
        source: CoordinationParticipant,
    ) -> str:
        if type(detection) is not CoordinationDetection:
            raise CoordinationError(CoordinationErrorCode.INVALID)
        if (
            type(recipient) is not CoordinationParticipant
            or type(source) is not CoordinationParticipant
        ):
            raise CoordinationError(CoordinationErrorCode.INVALID)
        if (
            recipient.task_id == source.task_id
            or {recipient.task_id, source.task_id}
            != {detection.left_task_id, detection.right_task_id}
            or recipient.project_id != detection.project_id
            or source.project_id != detection.project_id
        ):
            raise CoordinationError(CoordinationErrorCode.SELECTOR_CONFLICT)

        # Both source and recipient are admitted at the write boundary.  The source check is
        # essential: a target may still have consent while the source grant was revoked.
        await self.projects.admit(
            source_task_id=recipient.task_id,
            source_workspace_commitment=recipient.workspace_commitment,
            project=recipient.project_id,
            expected_generation=detection.membership_generation,
            expected_route_generation=recipient.route_generation,
            expected_repository_commitment=recipient.repository_commitment,
            cross_repository=recipient.repository_commitment != source.repository_commitment,
        )
        await self.projects.admit(
            source_task_id=source.task_id,
            source_workspace_commitment=source.workspace_commitment,
            project=source.project_id,
            expected_generation=detection.membership_generation,
            expected_route_generation=source.route_generation,
            expected_repository_commitment=source.repository_commitment,
            cross_repository=recipient.repository_commitment != source.repository_commitment,
        )
        route = await self.projects.catalog.task_route(recipient.task_id)
        if (
            type(route) is not TaskRoute
            or route.task_id != recipient.task_id
            or route.state is not TaskRouteState.ACTIVE
            or route.route_generation != recipient.route_generation
        ):
            raise CoordinationError(CoordinationErrorCode.GENERATION_MISMATCH)
        writer = observation_writer_id(recipient.task_id, route.session_id)
        task_runtime = await self.runtime.route(
            RouteCommand(
                route.session_id,
                writer,
                RouteAccess.WRITE,
                frozenset({RuntimeCapability.WRITE, RuntimeCapability.PAYLOAD_READ}),
            )
        )
        if type(task_runtime) is not TaskRuntime or task_runtime.task_id != recipient.task_id:
            if type(task_runtime) is TaskRuntime:
                await self.runtime.release(task_runtime)
            raise CoordinationError(CoordinationErrorCode.INVALID)
        try:
            records: list[LedgerRecord] = []
            async for record in task_runtime.ledger.load_events(task_runtime.session_id):
                if type(record) in {AcceptedEvent, UnknownEvent}:
                    records.append(record)
            prior = records[-1] if records else None
            source_is_owner = (
                detection.detail_ref is not None
                and detection.detail_ref.owner_task_id == source.task_id
            )
            gap_codes = (
                (CoordinationGapCode.NOT_OBSERVABLE,)
                if not source.source_has_attributable_paths
                else ()
            )
            context_material: JsonObject = JsonObject(
                {
                    "detection_id": detection.detection_id,
                    "project_id": detection.project_id,
                    "membership_generation": str(detection.membership_generation),
                    "recipient_task_id": recipient.task_id,
                    "counterpart_task_id": source.task_id,
                    "source_task_id": source.task_id,
                    "overlap_kind": detection.overlap_kind.value,
                    "resource_identities": list(detection.resource_identities),
                    "source_repository_commitment": source.repository_commitment,
                    "source_workspace_commitment": source.workspace_commitment,
                    "source_route_generation": str(source.route_generation),
                    "source_attributable_paths": source.source_has_attributable_paths,
                    "gap_codes": [item.value for item in gap_codes],
                }
            )
            context_digest = canonical_digest(context_material)
            authority_revision = canonical_digest(
                {
                    "project_id": detection.project_id,
                    "membership_generation": str(detection.membership_generation),
                    "recipient_task_id": recipient.task_id,
                    "source_task_id": source.task_id,
                    "source_route_generation": str(source.route_generation),
                }
            )
            payload = CoordinationContextRecordedPayload(
                detection_id=event_id(detection.detection_id),
                project_id=detection.project_id,
                membership_generation=detection.membership_generation,
                left_task_id=task_id(min(detection.left_task_id, detection.right_task_id)),
                right_task_id=task_id(max(detection.left_task_id, detection.right_task_id)),
                recipient_task_id=task_id(recipient.task_id),
                counterpart_task_id=task_id(source.task_id),
                source_task_id=task_id(source.task_id),
                overlap_kind=detection.overlap_kind,
                resource_identities=detection.resource_identities,
                resource_count=len(detection.resource_identities),
                source_repository_commitment=source.repository_commitment,
                source_workspace_commitment=source.workspace_commitment,
                source_route_generation=source.route_generation,
                source_attributable_paths=source.source_has_attributable_paths,
                context_digest=context_digest,
                detail_ref=detection.detail_ref if source_is_owner else None,
                gap_codes=gap_codes,
                recorded_authority_revision=authority_revision,
            )
            event_id_value = stable_observation_id(
                kind=IdKind.EVENT,
                task_id=recipient.task_id,
                source_identity=f"{detection.detection_id}:{source.task_id}:{context_digest}",
                mapping_version="coordination-context/1.0.0",
                role="context",
            )
            operation_id = stable_observation_id(
                kind=IdKind.REQUEST,
                task_id=recipient.task_id,
                source_identity=f"{detection.detection_id}:{source.task_id}:{context_digest}",
                mapping_version="coordination-context/1.0.0",
                role="append",
            )
            draft = EventDraft(
                event_id(event_id_value),
                EventSchema("coordination_context_recorded", COORDINATION_EVENT_SCHEMA_VERSION),
                timestamp_from_datetime(
                    self.clock.now_utc() if self.clock is not None else datetime.now(UTC)
                ),
                () if prior is None else (prior.event_id,),
                payload,
                (),
                (),
            )
            payload_bytes = canonical_encode(encode_payload(payload))
            metadata = ObjectMetadata(
                ObjectKind.EVENT_PAYLOAD,
                media_type_for("coordination_context_recorded"),
                recipient.task_id,
                self.clock.now_utc() if self.clock is not None else datetime.now(UTC),
            )
            staged = await task_runtime.objects.stage(
                ObjectSource(data=payload_bytes, declared_size=len(payload_bytes)),
                metadata,
            )
            try:
                reference = await task_runtime.objects.finalize(staged)
            except BaseException:
                await task_runtime.objects.abandon(staged)
                raise
            coverage = coverage_for_channel(PublicationChannel.ENGINE_DERIVED)
            entry = AppendEntry(
                draft,
                observation_author(),
                reference,
                reference.commitment,
                metadata.media_type,
                reference.plaintext_size,
                PublicationChannel.ENGINE_DERIVED,
                coverage,
                "projected",
            )
            request_digest_value = canonical_digest(
                {
                    "domain": "yoetz/coordination-context-append/v1",
                    "task_id": recipient.task_id,
                    "detection_id": detection.detection_id,
                    "source_task_id": source.task_id,
                    "context_digest": context_digest,
                }
            )
            command = AppendCommand(
                recipient.task_id,
                route.session_id,
                writer,
                operation_id,
                OperationKind.PUBLISH_WORK,
                request_digest_value,
                None if prior is None else prior.ledger.ingestion_sequence,
                (entry,),
            )
            prepared = PreparedMutation(
                command.writer_id,
                command.operation_id,
                command.request_digest,
                command.expected_frontier,
                (reference,),
                command,
            )
            await run_prepared_append(task_runtime.ledger, prepared)
            return event_id_value
        finally:
            await self.runtime.release(task_runtime)


class EncryptedCoordinationDetailStore:
    """Persist detector details in the owning task bundle's encrypted object store."""

    def __init__(self, objects: ObjectStorePort, *, clock: ClockPort | None = None) -> None:
        self.objects = objects
        self.clock = clock

    async def put_details(
        self,
        detection_id: str,
        details: JsonObject,
        *,
        owner_task_id: str,
        route_generation: int,
    ) -> ProjectTextRef:
        try:
            validate_id(IdKind.EVENT, detection_id)
        except (TypeError, ValueError) as exc:
            raise CoordinationError(CoordinationErrorCode.INVALID) from exc
        _task(owner_task_id)
        _positive(route_generation)
        try:
            data = canonical_encode(details)
        except (TypeError, ValueError) as exc:
            raise CoordinationError(CoordinationErrorCode.INVALID) from exc
        metadata = ObjectMetadata(
            ObjectKind.PROJECT_TEXT,
            "application/json",
            owner_task_id,
            self.clock.now_utc() if self.clock is not None else datetime.now(UTC),
        )
        staged = await self.objects.stage(ObjectSource(data=data), metadata)
        try:
            reference = await self.objects.finalize(staged)
        except BaseException:
            await self.objects.abandon(staged)
            raise
        return ProjectTextRef(
            reference.object_id,
            "sha256:" + hashlib.sha256(data).hexdigest(),
            len(data),
            owner_task_id,
            route_generation,
            reference.envelope_digest,
        )

    async def read_details(self, reference: ProjectTextRef) -> JsonObject:
        """Read and authenticate one detector-owned encrypted detail object.

        The object route is supplied by the caller's generation-bound lease.  This method only
        verifies the object envelope and canonical detail format; it never exposes the mapping
        through a structural catalog or a generic text reader.
        """

        if type(reference) is not ProjectTextRef or reference.envelope_digest is None:
            raise CoordinationError(CoordinationErrorCode.INVALID)
        resolved = await self.objects.resolve_verified(
            reference.object_id, reference.envelope_digest
        )
        if (
            type(resolved) is not ObjectRef
            or resolved.metadata.kind is not ObjectKind.PROJECT_TEXT
            or resolved.metadata.task_id != reference.owner_task_id
            or resolved.plaintext_size != reference.plaintext_size
        ):
            raise CoordinationError(CoordinationErrorCode.INVALID)
        data = bytearray()
        async for chunk in self.objects.open_verified(resolved):
            if type(chunk) is not bytes:
                raise CoordinationError(CoordinationErrorCode.INVALID)
            data.extend(chunk)
        payload = bytes(data)
        if "sha256:" + hashlib.sha256(payload).hexdigest() != reference.content_digest:
            raise CoordinationError(CoordinationErrorCode.INVALID)
        try:
            parsed = strict_json_parse(payload)
        except (TypeError, ValueError, UnicodeDecodeError) as exc:
            raise CoordinationError(CoordinationErrorCode.INVALID) from exc
        if not isinstance(parsed, Mapping):
            raise CoordinationError(CoordinationErrorCode.INVALID)
        try:
            details = JsonObject(parsed)
        except (TypeError, ValueError) as exc:
            raise CoordinationError(CoordinationErrorCode.INVALID) from exc
        if details.get("format") != COORDINATION_DETAIL_FORMAT:
            raise CoordinationError(CoordinationErrorCode.INVALID)
        return details


class CoordinationDeliveryStore(Protocol):
    async def put_detection(self, detection: CoordinationDetection) -> CoordinationDetection: ...

    async def get_detection(self, detection_id: str) -> CoordinationDetection | None: ...

    async def list_detections(self, project_id: str) -> tuple[CoordinationDetection, ...]: ...

    async def put_advice(self, advice: CoordinationAdvice) -> CoordinationDelivery: ...

    async def put_delivery(self, delivery: CoordinationDelivery) -> CoordinationDelivery: ...

    async def deliveries(self, detection_id: str) -> tuple[CoordinationDelivery, ...]: ...

    async def advice_for(
        self, detection_id: str, target_task_id: str
    ) -> CoordinationAdvice | None: ...

    async def obligation(
        self, detection_id: str, task_id: str
    ) -> CoordinationObligationState | None: ...

    async def set_obligation(
        self, state: CoordinationObligationState
    ) -> CoordinationObligationState: ...

    async def replace_detection(
        self, detection: CoordinationDetection
    ) -> CoordinationDetection: ...

    async def put_participants(
        self,
        detection_id: str,
        participants: tuple[CoordinationParticipant, CoordinationParticipant],
    ) -> None: ...

    async def participants(
        self, detection_id: str
    ) -> tuple[CoordinationParticipant, CoordinationParticipant] | None: ...

    async def put_coverage(self, coverage: CoordinationCoverage) -> CoordinationCoverage: ...

    async def coverage_for(
        self, project_id: str, membership_generation: int
    ) -> tuple[CoordinationCoverage, ...]: ...


class InMemoryCoordinationStore:
    """Deterministic delivery store with idempotent two-target writes."""

    def __init__(self) -> None:
        self.detections: dict[str, CoordinationDetection] = {}
        self._advice_rows: dict[tuple[str, str], CoordinationAdvice] = {}
        self.delivery_rows: dict[tuple[str, str], CoordinationDelivery] = {}
        self.obligations: dict[tuple[str, str], CoordinationObligationState] = {}
        self.coverage_rows: dict[tuple[str, str, int], CoordinationCoverage] = {}
        self.participant_rows: dict[
            str, tuple[CoordinationParticipant, CoordinationParticipant]
        ] = {}

    @property
    def advice_rows(self) -> Mapping[tuple[str, str], CoordinationAdvice]:
        """Inspection-only advice rows; delivery lookup remains the ``deliveries`` method."""

        return self._advice_rows

    async def put_detection(self, detection: CoordinationDetection) -> CoordinationDetection:
        existing = self.detections.get(detection.detection_id)
        if existing is not None:
            immutable = (
                "project_id",
                "membership_generation",
                "left_task_id",
                "right_task_id",
                "overlap_kind",
                "resource_identities",
                "counterpart_task_id",
            )
            if any(getattr(existing, name) != getattr(detection, name) for name in immutable):
                raise CoordinationError(CoordinationErrorCode.SELECTOR_CONFLICT)
            return existing
        self.detections[detection.detection_id] = detection
        return detection

    async def get_detection(self, detection_id: str) -> CoordinationDetection | None:
        return self.detections.get(detection_id)

    async def list_detections(self, project_id: str) -> tuple[CoordinationDetection, ...]:
        return tuple(
            item
            for item in sorted(
                self.detections.values(), key=lambda value: value.detection_id.encode()
            )
            if item.project_id == project_id
        )

    async def put_advice(self, advice: CoordinationAdvice) -> CoordinationDelivery:
        detection = self.detections.get(advice.detection_id)
        if detection is None:
            raise CoordinationError(CoordinationErrorCode.PROJECT_NOT_FOUND)
        if (
            advice.project_id != detection.project_id
            or advice.membership_generation != detection.membership_generation
            or advice.overlap_kind is not detection.overlap_kind
            or advice.resource_identities != detection.resource_identities
            or advice.resource_count != len(detection.resource_identities)
            or advice.target_task_id not in {detection.left_task_id, detection.right_task_id}
        ):
            raise CoordinationError(CoordinationErrorCode.SELECTOR_CONFLICT)
        participants = self.participant_rows.get(advice.detection_id)
        if participants is None or advice.counterpart_task_id not in {
            participants[0].task_id,
            participants[1].task_id,
        }:
            raise CoordinationError(CoordinationErrorCode.INVALID)
        key = advice.detection_id, advice.target_task_id
        existing = self.delivery_rows.get(key)
        if existing is not None:
            prior = self._advice_rows.get(key)
            if prior is not None and prior != advice:
                raise CoordinationError(CoordinationErrorCode.SELECTOR_CONFLICT)
            if existing.outcome == "refused":
                return existing
            return CoordinationDelivery(
                advice.detection_id,
                advice.target_task_id,
                "duplicate",
                advice.membership_generation,
                existing.observed_generation,
            )
        self._advice_rows[key] = advice
        delivery = CoordinationDelivery(
            advice.detection_id,
            advice.target_task_id,
            "delivered",
            advice.membership_generation,
            advice.membership_generation,
        )
        self.delivery_rows[key] = delivery
        return delivery

    async def put_delivery(self, delivery: CoordinationDelivery) -> CoordinationDelivery:
        detection = self.detections.get(delivery.detection_id)
        participants = self.participant_rows.get(delivery.detection_id)
        if detection is None:
            raise CoordinationError(CoordinationErrorCode.PROJECT_NOT_FOUND)
        if (
            participants is None
            or delivery.target_task_id not in {participants[0].task_id, participants[1].task_id}
            or delivery.expected_generation != detection.membership_generation
        ):
            raise CoordinationError(CoordinationErrorCode.INVALID)
        key = delivery.detection_id, delivery.target_task_id
        existing = self.delivery_rows.get(key)
        if existing is not None:
            # A prior context append remains an immutable historical delivery.  A later
            # consent refusal fences the detection, but must not rewrite that append into a
            # conflicting terminal row while the invalidation is being recorded.
            if delivery.outcome == "refused" and existing.outcome in {"delivered", "duplicate"}:
                return existing
            if existing != delivery:
                raise CoordinationError(CoordinationErrorCode.SELECTOR_CONFLICT)
            return existing
        self.delivery_rows[key] = delivery
        return delivery

    async def deliveries(self, detection_id: str) -> tuple[CoordinationDelivery, ...]:
        return tuple(
            value
            for (item_id, _), value in sorted(self.delivery_rows.items())
            if item_id == detection_id
        )

    async def advice_for(self, detection_id: str, target_task_id: str) -> CoordinationAdvice | None:
        return self._advice_rows.get((detection_id, target_task_id))

    async def obligation(
        self, detection_id: str, task_id: str
    ) -> CoordinationObligationState | None:
        return self.obligations.get((detection_id, task_id))

    async def set_obligation(
        self, state: CoordinationObligationState
    ) -> CoordinationObligationState:
        existing = self.obligations.get((state.detection_id, state.task_id))
        if existing is not None:
            if existing.obligation_id != state.obligation_id:
                raise CoordinationError(CoordinationErrorCode.SELECTOR_CONFLICT)
            if (
                existing.declared
                and not state.declared
                or existing.addressed
                and not state.addressed
                or existing.resolved
                and not state.resolved
            ):
                raise CoordinationError(CoordinationErrorCode.SELECTOR_CONFLICT)
            if existing == state:
                return existing
        self.obligations[(state.detection_id, state.task_id)] = state
        return state

    async def replace_detection(self, detection: CoordinationDetection) -> CoordinationDetection:
        existing = self.detections.get(detection.detection_id)
        if existing is None:
            raise CoordinationError(CoordinationErrorCode.PROJECT_NOT_FOUND)
        immutable = (
            "project_id",
            "membership_generation",
            "left_task_id",
            "right_task_id",
            "overlap_kind",
            "resource_identities",
            "counterpart_task_id",
        )
        if any(getattr(existing, name) != getattr(detection, name) for name in immutable):
            raise CoordinationError(CoordinationErrorCode.SELECTOR_CONFLICT)
        if (
            (existing.obligation_declared and not detection.obligation_declared)
            or (existing.addressed and not detection.addressed)
            or (existing.resolved and not detection.resolved)
            or (not existing.generation_valid and detection.generation_valid)
            or (not existing.advice_only and detection.advice_only)
        ):
            raise CoordinationError(CoordinationErrorCode.SELECTOR_CONFLICT)
        self.detections[detection.detection_id] = detection
        return detection

    async def put_participants(
        self,
        detection_id: str,
        participants: tuple[CoordinationParticipant, CoordinationParticipant],
    ) -> None:
        if (
            type(participants) is not tuple
            or len(participants) != 2
            or any(type(item) is not CoordinationParticipant for item in participants)
        ):
            raise CoordinationError(CoordinationErrorCode.INVALID)
        detection = self.detections.get(detection_id)
        if detection is None:
            raise CoordinationError(CoordinationErrorCode.PROJECT_NOT_FOUND)
        if {item.task_id for item in participants} != {
            detection.left_task_id,
            detection.right_task_id,
        } or any(item.project_id != detection.project_id for item in participants):
            raise CoordinationError(CoordinationErrorCode.SELECTOR_CONFLICT)
        normalized = cast(
            tuple[CoordinationParticipant, CoordinationParticipant],
            tuple(sorted(participants, key=lambda item: item.task_id.encode("ascii"))),
        )
        existing = self.participant_rows.get(detection_id)
        if existing is not None and existing != normalized:
            raise CoordinationError(CoordinationErrorCode.SELECTOR_CONFLICT)
        self.participant_rows.setdefault(detection_id, normalized)

    async def participants(
        self, detection_id: str
    ) -> tuple[CoordinationParticipant, CoordinationParticipant] | None:
        return self.participant_rows.get(detection_id)

    async def put_coverage(self, coverage: CoordinationCoverage) -> CoordinationCoverage:
        if type(coverage) is not CoordinationCoverage:
            raise CoordinationError(CoordinationErrorCode.INVALID)
        key = (coverage.project_id, coverage.task_id, coverage.membership_generation)
        existing = self.coverage_rows.get(key)
        if existing is not None and existing != coverage:
            raise CoordinationError(CoordinationErrorCode.SELECTOR_CONFLICT)
        self.coverage_rows.setdefault(key, coverage)
        return self.coverage_rows[key]

    async def coverage_for(
        self, project_id: str, membership_generation: int
    ) -> tuple[CoordinationCoverage, ...]:
        project = _project(project_id)
        _positive(membership_generation)
        return tuple(
            sorted(
                (
                    item
                    for item in self.coverage_rows.values()
                    if item.project_id == project
                    and item.membership_generation == membership_generation
                ),
                key=lambda item: item.task_id.encode("ascii"),
            )
        )


def structured_plan_identity(
    item: Mapping[str, JsonValue], *, repository_commitment: str | None = None
) -> str | None:
    """Digest only declared structural plan keys; prose and guessed paths are ignored."""

    allowed = {
        "resource_identity",
        "resource_commitment",
        "resource_kind",
        "operation_kind",
        "obligation_key",
        "subject_digest",
    }
    selected: dict[str, JsonValue] = {}
    for key in sorted(item, key=str.encode):
        if key not in allowed:
            continue
        value = item[key]
        if type(value) is str and value:
            selected[key] = value
        elif type(value) in {bool, int}:
            selected[key] = value
    if not selected:
        return None
    try:
        if repository_commitment is not None:
            repository = validate_commitment(repository_commitment)
            resource = selected.get("resource_identity")
            if resource is not None:
                if type(resource) is not str:
                    return None
                selected["resource_identity"] = canonical_resource_identity(
                    resource,
                    repository_commitment=repository,
                    case_sensitive=True,
                )
            # A structural plan item with no explicit path still belongs to the source
            # repository. Binding the commitment prevents an identical raw plan label from
            # becoming a cross-repository overlap.
            selected["repository_commitment"] = repository
        return canonical_digest(selected)
    except CoordinationError, TypeError, ValueError:
        return None


def _resource_from_explicit_value(value: str) -> str | None:
    """Return a normalized path only for a typed resource declaration."""

    try:
        return relative_resource_identity(value)
    except CoordinationError:
        # Event payloads may contain bounded labels in an item slot.  Such a value is not an
        # attributable path and must not become a guessed path in a coordination input.
        return None


def _event_input_material(
    record: AcceptedEvent,
) -> tuple[set[str], list[Mapping[str, JsonValue]], set[str]]:
    """Extract explicit resource/subject identities from one task-owned event.

    This function intentionally ignores summaries, descriptions, commands, and evidence prose.
    Only typed file/source requested items, explicit attempted item labels that normalize as
    repository-relative paths, obligation identities, and structural subject-state digests enter
    the detector input.
    """

    payload = record.payload
    resources: set[str] = set()
    structured: list[Mapping[str, JsonValue]] = []
    obligations: set[str] = set()
    if isinstance(payload, ObligationPublishedPayload):
        if payload.status is ObligationStatus.OPEN:
            obligations.add(payload.obligation_id)
        for item in payload.requested_items:
            if item.item_kind not in {RequestedItemKind.FILE, RequestedItemKind.SOURCE}:
                continue
            normalized = _resource_from_explicit_value(item.value)
            if normalized is None:
                continue
            resources.add(normalized)
            structured.append(
                JsonObject(
                    {
                        "resource_identity": normalized,
                        "resource_kind": item.item_kind.value,
                    }
                )
            )
    elif isinstance(payload, ActionRecordedPayload) and payload.action_kind is ActionKind.EDIT:
        for value in payload.attempted_items:
            normalized = _resource_from_explicit_value(value)
            if normalized is None:
                continue
            resources.add(normalized)
            structured.append(
                JsonObject({"resource_identity": normalized, "resource_kind": "action"})
            )

    if isinstance(payload, PlanPublishedPayload):
        for obligation in payload.obligation_refs:
            structured.append(JsonObject({"obligation_key": obligation}))
    elif isinstance(payload, PlanRevisedPayload):
        for change in payload.obligation_changes:
            structured.append(JsonObject({"obligation_key": change.obligation_id}))

    subject_state = getattr(payload, "subject_state", None)
    if subject_state is not None:
        if subject_state.tree_digest is not None:
            structured.append(JsonObject({"subject_digest": subject_state.tree_digest}))
        if subject_state.diff_digest is not None:
            structured.append(JsonObject({"subject_digest": subject_state.diff_digest}))
    if isinstance(payload, EvidenceRecordedPayload) and payload.content_digest is not None:
        # Evidence content is already represented by a digest.  It is a structural input only;
        # its reference/description never enters coordination.
        structured.append(JsonObject({"subject_digest": payload.content_digest}))
    if isinstance(payload, ResultRecordedPayload):
        # Result subject state is handled above; outcome text is deliberately ignored.
        del payload
    return resources, structured, obligations


class LedgerCoordinationInputProvider:
    """Build attributable coordination inputs from an authenticated task runtime.

    ``resource_provider`` is the host-specific hook for a declared publish-work scope or an
    attributable edit signal.  When it is absent, only typed task-ledger scopes are considered;
    no shared workspace diff is inspected.  A task without a revealable path is returned with
    ``source_has_attributable_paths=False`` so the detector can report bounded coverage.
    """

    def __init__(
        self,
        projects: ProjectApplication,
        runtime: BundleRuntimePort,
        *,
        resource_provider: _ResourceProvider | None = None,
        case_sensitive: bool = True,
    ) -> None:
        if not callable(
            getattr(getattr(projects, "catalog", None), "task_source_provenance", None)
        ):
            raise TypeError("coordination_projects_invalid")
        if not callable(getattr(runtime, "route", None)) or not callable(
            getattr(runtime, "release", None)
        ):
            raise TypeError("coordination_runtime_invalid")
        if resource_provider is not None and not callable(resource_provider):
            raise TypeError("coordination_resource_provider_invalid")
        if type(case_sensitive) is not bool:
            raise TypeError("coordination_case_sensitivity_invalid")
        self.projects = projects
        self.catalog = projects.catalog
        self.runtime = runtime
        self.resource_provider = resource_provider
        self.case_sensitive = case_sensitive

    async def _load_ledger_material(
        self, route: TaskRoute
    ) -> tuple[
        set[str],
        list[Mapping[str, JsonValue]],
        set[str],
        tuple[CoordinationObligationDeclaredPayload, ...],
    ]:
        runtime = await self.runtime.route(
            RouteCommand(
                route.session_id,
                None,
                RouteAccess.STRUCTURAL_READ,
                frozenset({RuntimeCapability.STRUCTURAL_READ}),
            )
        )
        if type(runtime) is not TaskRuntime or runtime.task_id != route.task_id:
            if type(runtime) is TaskRuntime:
                await self.runtime.release(runtime)
            raise CoordinationError(CoordinationErrorCode.INVALID)
        resources: set[str] = set()
        structured: list[Mapping[str, JsonValue]] = []
        obligations: set[str] = set()
        declarations: list[CoordinationObligationDeclaredPayload] = []
        try:
            async for record in runtime.ledger.load_events(runtime.session_id):
                if type(record) is not AcceptedEvent:
                    continue
                if record.schema.name.startswith("coordination_"):
                    # Coordination context/disposition facts are advice and resolution history,
                    # not detector input.  The ordinary typed declaration is the one exception:
                    # it is the durable binding that a later sweep may carry into the detector.
                    if isinstance(record.payload, CoordinationObligationDeclaredPayload):
                        declarations.append(record.payload)
                    continue
                event_resources, event_structured, event_obligations = _event_input_material(record)
                resources.update(event_resources)
                structured.extend(event_structured)
                obligations.update(event_obligations)
                if isinstance(record.payload, ObligationPublishedPayload):
                    # Obligation IDs are durable identities, while their status is a
                    # generation-local projection.  A later resolved restatement must remove
                    # the ID from current detector input; scanning for any historical open row
                    # would let a declaration bind a closed obligation.
                    if record.payload.status is ObligationStatus.RESOLVED:
                        obligations.discard(record.payload.obligation_id)
                    else:
                        obligations.add(record.payload.obligation_id)
        finally:
            await self.runtime.release(runtime)
        return resources, structured, obligations, tuple(declarations)

    async def owns_obligation(self, task_id: str, obligation_id_value: str) -> bool:
        """Return whether the task ledger contains the named open obligation.

        Coordination state must be tied to a participant's own published obligation.  This read
        is intentionally task scoped and consumes only typed ledger payloads; it never treats a
        plan reference, prose label, or detector argument as an obligation declaration.
        """

        task = _task(task_id)
        expected = _obligation(obligation_id_value)
        route = await self.catalog.task_route(task)
        if (
            type(route) is not TaskRoute
            or route.task_id != task
            or route.state is not TaskRouteState.ACTIVE
        ):
            return False
        runtime = await self.runtime.route(
            RouteCommand(
                route.session_id,
                None,
                RouteAccess.STRUCTURAL_READ,
                frozenset({RuntimeCapability.STRUCTURAL_READ}),
            )
        )
        if type(runtime) is not TaskRuntime or runtime.task_id != task:
            if type(runtime) is TaskRuntime:
                await self.runtime.release(runtime)
            return False
        latest_status: ObligationStatus | None = None
        try:
            async for record in runtime.ledger.load_events(runtime.session_id):
                if (
                    type(record) is AcceptedEvent
                    and isinstance(record.payload, ObligationPublishedPayload)
                    and record.payload.obligation_id == expected
                ):
                    latest_status = record.payload.status
        finally:
            await self.runtime.release(runtime)
        return latest_status is ObligationStatus.OPEN

    async def input_for(
        self,
        task_id: str,
        project_id_value: str | None = None,
        *,
        resources: Sequence[str] | None = None,
        structured_items: Sequence[Mapping[str, JsonValue]] = (),
        source_has_attributable_paths: bool | None = None,
        case_sensitive: bool | None = None,
    ) -> DeclaredCoordinationInput | None:
        task = _task(task_id)
        project_value = None if project_id_value is None else _project(project_id_value)
        provenance = await self.catalog.task_source_provenance(task)
        route = await self.catalog.task_route(task)
        if (
            provenance is None
            or provenance.workspace_ref_commitment is None
            or provenance.repository_privacy_commitment is None
            or type(route) is not TaskRoute
            or route.task_id != task
            or route.state is not TaskRouteState.ACTIVE
            or route.route_generation != provenance.route_generation
            or route.route_identity_digest != provenance.route_identity_digest
        ):
            return None
        if project_value is None:
            project_ids = await self.catalog.list_task_project_ids(task)
            if len(project_ids) != 1:
                return None
            project_value = _project(project_ids[0])
        if resources is None:
            (
                derived_resources,
                derived_structured,
                derived_obligations,
                declarations,
            ) = await self._load_ledger_material(route)
            if self.resource_provider is not None:
                supplied = await _awaitable(self.resource_provider(task))
                if supplied is not None:
                    derived_resources = {relative_resource_identity(value) for value in supplied}
                    attributable = bool(derived_resources)
                else:
                    attributable = False
            else:
                attributable = bool(derived_resources)
            selected_resources = tuple(sorted(derived_resources, key=str.encode))
            selected_structured = tuple((*derived_structured, *structured_items))
            selected_obligations = tuple(sorted(derived_obligations, key=str.encode))
            selected_declarations = tuple(
                item
                for item in declarations
                if item.project_id == project_value
                and item.recipient_task_id == task
                and item.obligation_id in derived_obligations
            )
        else:
            selected_resources = tuple(
                sorted({relative_resource_identity(value) for value in resources}, key=str.encode)
            )
            selected_structured = tuple(structured_items)
            selected_obligations = ()
            selected_declarations = ()
            attributable = bool(selected_resources)
        if source_has_attributable_paths is not None:
            if type(source_has_attributable_paths) is not bool:
                raise CoordinationError(CoordinationErrorCode.INVALID)
            attributable = source_has_attributable_paths
        selected_case = self.case_sensitive if case_sensitive is None else case_sensitive
        if type(selected_case) is not bool:
            raise CoordinationError(CoordinationErrorCode.INVALID)
        return DeclaredCoordinationInput(
            task,
            project_value,
            provenance.repository_privacy_commitment,
            provenance.workspace_ref_commitment,
            route.route_generation,
            selected_resources,
            selected_structured,
            selected_case,
            attributable,
            selected_obligations,
            selected_declarations,
            provenance.route_identity_digest,
        )

    async def for_task(
        self,
        task_id: str,
        project_id_value: str | None = None,
        *,
        resources: Sequence[str] | None = None,
        structured_items: Sequence[Mapping[str, JsonValue]] = (),
        source_has_attributable_paths: bool | None = None,
        case_sensitive: bool | None = None,
    ) -> DeclaredCoordinationInput | None:
        """Alias used by service compositions that describe this provider as a task source."""

        return await self.input_for(
            task_id,
            project_id_value,
            resources=resources,
            structured_items=structured_items,
            source_has_attributable_paths=source_has_attributable_paths,
            case_sensitive=case_sensitive,
        )


class CoordinationRuntime:
    """Production project flow: load admitted task inputs, detect, and durably deliver pairs."""

    def __init__(
        self,
        projects: ProjectApplication,
        detector: CoordinationDetector,
        inputs: LedgerCoordinationInputProvider,
    ) -> None:
        if (
            type(detector) is not CoordinationDetector
            or type(inputs) is not LedgerCoordinationInputProvider
        ):
            raise TypeError("coordination_runtime_components_invalid")
        self.projects = projects
        self.detector = detector
        self.inputs = inputs

    async def record_unobservable_coverage(
        self,
        inputs: Sequence[DeclaredCoordinationInput],
        *,
        expected_generation: int | None,
    ) -> None:
        """Persist per-task coverage gaps without inventing a pair detection.

        A task with no revealable attributable paths still contributes a useful bounded coverage
        fact.  The source must pass the same current project admission as delivery; otherwise a
        stale or unconsented task cannot leave a public coverage row behind.  No resource,
        counterpart, or finding identity is created by this path.
        """

        put_coverage = getattr(self.detector.store, "put_coverage", None)
        if not callable(put_coverage):
            return
        for item in inputs:
            if item.source_has_attributable_paths:
                continue
            try:
                admission = await self.projects.admit(
                    source_task_id=item.task_id,
                    source_workspace_commitment=item.workspace_commitment,
                    project=item.project_id,
                    expected_generation=expected_generation,
                )
            except CoordinationError:
                # Coverage is scoped to a live, consented project.  A refusal is intentionally
                # silent here: exposing even a task-shaped row would disclose a non-admitted
                # source to a requester.
                continue
            coverage = CoordinationCoverage(
                stable_observation_id(
                    kind=IdKind.EVENT,
                    task_id=item.task_id,
                    source_identity=(
                        f"{item.project_id}:{admission.membership_generation}:{item.task_id}"
                    ),
                    mapping_version="coordination-coverage/1.0.0",
                    role="unobservable",
                ),
                item.project_id,
                item.task_id,
                admission.membership_generation,
            )
            result = put_coverage(coverage)
            if inspect.isawaitable(result):
                await result

    async def detect(
        self,
        project_id_value: str,
        *,
        task_ids: Sequence[str] | None = None,
        inputs: Mapping[str, DeclaredCoordinationInput] | None = None,
        resources_by_task: Mapping[str, Sequence[str]] | None = None,
        structured_items_by_task: Mapping[str, Sequence[Mapping[str, JsonValue]]] | None = None,
        expected_generation: int | None = None,
    ) -> tuple[CoordinationAdvice, ...]:
        project = _project(project_id_value)
        if inputs is None:
            selected_ids = (
                tuple(_task(item) for item in task_ids)
                if task_ids is not None
                else await self.projects.catalog.list_project_task_ids(project)
            )
            loaded: dict[str, DeclaredCoordinationInput] = {}
            for task in sorted(set(selected_ids), key=str.encode):
                item = await self.inputs.input_for(
                    task,
                    project,
                    resources=None if resources_by_task is None else resources_by_task.get(task),
                    structured_items=()
                    if structured_items_by_task is None
                    else structured_items_by_task.get(task, ()),
                )
                if item is not None:
                    loaded[task] = item
            selected = tuple(loaded.values())
        else:
            if any(type(item) is not DeclaredCoordinationInput for item in inputs.values()):
                raise CoordinationError(CoordinationErrorCode.INVALID)
            selected = tuple(item for item in inputs.values() if item.project_id == project)
        ordered = tuple(sorted(selected, key=lambda item: item.task_id.encode()))
        await self.record_unobservable_coverage(
            ordered,
            expected_generation=expected_generation,
        )
        declarations_by_task: dict[str, tuple[CoordinationObligationDeclaredPayload, ...]] = {
            item.task_id: item.coordination_declarations for item in ordered
        }
        for task, declarations in declarations_by_task.items():
            for declaration in declarations:
                if (
                    declaration.project_id != project
                    or declaration.recipient_task_id != task
                    or not await self.inputs.owns_obligation(task, str(declaration.obligation_id))
                ):
                    raise CoordinationError(CoordinationErrorCode.INVALID)
        outputs: list[CoordinationAdvice] = []
        for index, left in enumerate(ordered):
            for right in ordered[index + 1 :]:
                outputs.extend(
                    await self.detector.detect(
                        left,
                        right,
                        expected_generation=expected_generation,
                        coordination_declarations=tuple(
                            declaration
                            for task in (left.task_id, right.task_id)
                            for declaration in declarations_by_task.get(task, ())
                        ),
                    )
                )
        return tuple(outputs)

    async def sweep(
        self,
        project_id_value: str | None = None,
        *,
        task_id: str | None = None,
        task_ids: Sequence[str] | None = None,
        inputs: Mapping[str, DeclaredCoordinationInput] | None = None,
        resources_by_task: Mapping[str, Sequence[str]] | None = None,
        structured_items_by_task: Mapping[str, Sequence[Mapping[str, JsonValue]]] | None = None,
        expected_generation: int | None = None,
    ) -> tuple[CoordinationAdvice, ...]:
        """Run the public coordination sweep after a durable task event is published.

        Callers may identify a project directly or provide one task whose current project set is
        resolved by the catalog.  Inputs are rebuilt from current task ledgers at sweep time, so a
        stale declaration cannot silently deliver after a route or project generation change.
        """

        if project_id_value is not None:
            projects = (_project(project_id_value),)
        elif task_id is not None:
            task = _task(task_id)
            projects = tuple(
                _project(value) for value in await self.projects.catalog.list_task_project_ids(task)
            )
        else:
            raise CoordinationError(CoordinationErrorCode.INVALID)
        selected_tasks = task_ids
        outputs: list[CoordinationAdvice] = []
        for project in sorted(set(projects), key=str.encode):
            outputs.extend(
                await self.detect(
                    project,
                    task_ids=selected_tasks,
                    inputs=inputs,
                    resources_by_task=resources_by_task,
                    structured_items_by_task=structured_items_by_task,
                    expected_generation=expected_generation,
                )
            )
        return tuple(outputs)

    async def detect_project(
        self,
        project_id_value: str,
        *,
        task_ids: Sequence[str] | None = None,
        inputs: Mapping[str, DeclaredCoordinationInput] | None = None,
        resources_by_task: Mapping[str, Sequence[str]] | None = None,
        structured_items_by_task: Mapping[str, Sequence[Mapping[str, JsonValue]]] | None = None,
        expected_generation: int | None = None,
    ) -> tuple[CoordinationAdvice, ...]:
        return await self.detect(
            project_id_value,
            task_ids=task_ids,
            inputs=inputs,
            resources_by_task=resources_by_task,
            structured_items_by_task=structured_items_by_task,
            expected_generation=expected_generation,
        )

    async def assessments_for_check(
        self,
        task_id: str,
        case: object,
        *,
        scope_roots: frozenset[str] = frozenset(),
        whole_case: bool = True,
    ) -> tuple[object, ...]:
        """Derive coordination findings exclusively from a frozen recipient projection.

        The detector/store is deliberately absent from this method.  Once a check freezes its
        case, live catalog state cannot add or remove a finding.  Context events are durable
        advice facts for both recipients; only a matching typed declaration binding can purchase
        finding eligibility, and a typed disposition with admissible evidence suppresses the next
        finding so the normal check reducer resolves the prior one.
        """

        from yoetz.domain.events import ObligationStatus
        from yoetz.domain.findings import (
            FINDING_KIND_TRAITS,
            CandidateFinding,
            FindingKind,
            FindingOrigin,
        )
        from yoetz.domain.values import SubjectStateRelation, event_id, evidence_id, result_id
        from yoetz.kernel.deterministic_checks import (
            DeterministicAssessment,
            DeterministicCase,
            FindingBasis,
            FindingFact,
            FrozenSourceAvailability,
            case_coverage,
            render_deterministic_finding_text,
        )
        from yoetz.protocol.coverage import LedgerFreshness

        if type(task_id) is not str or type(case) is not DeterministicCase:
            raise CoordinationError(CoordinationErrorCode.INVALID)
        projection = case.projection
        contexts = tuple(
            sorted(
                projection.coordination_contexts.values(),
                key=lambda item: (
                    item.source_frontier,
                    item.source_event_id.encode("ascii"),
                ),
                reverse=True,
            )
        )
        dispositions = tuple(projection.coordination_dispositions.values())
        declarations = tuple(projection.coordination_declarations.values())
        output: list[object] = []
        seen: set[tuple[str, str]] = set()
        for record in contexts:
            payload = record.payload
            if payload is None or payload.recipient_task_id != task_id:
                continue
            if not whole_case and not (
                str(record.source_event_id) in scope_roots
                or str(payload.detection_id) in scope_roots
            ):
                continue
            identity = (str(payload.detection_id), str(payload.recipient_task_id))
            if identity in seen:
                continue
            seen.add(identity)
            declaration = next(
                (
                    item.payload
                    for item in sorted(
                        declarations,
                        key=lambda item: (
                            item.source_frontier,
                            item.source_event_id.encode("ascii"),
                        ),
                        reverse=True,
                    )
                    if item.payload is not None
                    and item.payload.detection_id == payload.detection_id
                    and item.payload.project_id == payload.project_id
                    and item.payload.membership_generation == payload.membership_generation
                    and item.payload.recipient_task_id == payload.recipient_task_id
                ),
                None,
            )
            if declaration is None:
                # A normal open obligation or a context/advice event does not purchase finding
                # eligibility.  Only the explicit typed binding in this frozen recipient
                # projection can do that.
                continue
            obligation_record = projection.obligations.get(declaration.obligation_id)
            if (
                obligation_record is None
                or obligation_record.payload is None
                or obligation_record.payload.status is not ObligationStatus.OPEN
            ):
                continue
            if (
                not payload.source_attributable_paths
                and payload.overlap_kind is not OverlapKind.PLAN
            ):
                # A bounded advice context without attributable source paths is not evidence of
                # a finding.  Its event remains in the frozen case and contributes its coverage.
                continue
            addressed = False
            for disposition_record in dispositions:
                disposition = disposition_record.payload
                if disposition is None:
                    continue
                if (
                    disposition.detection_id != payload.detection_id
                    or disposition.project_id != payload.project_id
                    or disposition.membership_generation != payload.membership_generation
                    or disposition.recipient_task_id != payload.recipient_task_id
                    or disposition.obligation_id != declaration.obligation_id
                    or disposition_record.source_frontier <= record.source_frontier
                ):
                    continue
                if (
                    disposition.context_digest is not None
                    and disposition.context_digest != payload.context_digest
                ):
                    continue
                if any(ref not in case.allowed_ids for ref in disposition.evidence_refs):
                    continue
                if any(
                    (
                        case.projection.evidence.get(evidence_id(ref)) is None
                        or case.projection.evidence[evidence_id(ref)].payload is None
                        or case.projection.evidence[evidence_id(ref)].redacted
                        or case.projection.evidence[evidence_id(ref)].source_frontier
                        >= disposition_record.source_frontier
                    )
                    if str(ref).startswith("evd_")
                    else (
                        case.projection.results.get(result_id(ref)) is None
                        or case.projection.results[result_id(ref)].payload is None
                        or case.projection.results[result_id(ref)].redacted
                        or case.projection.results[result_id(ref)].source_frontier
                        >= disposition_record.source_frontier
                    )
                    for ref in disposition.evidence_refs
                ):
                    continue
                addressed = True
                break
            if addressed:
                continue
            subject = (event_id(record.source_event_id),)
            coverage = case.coverage_by_ref.get(record.source_event_id, case_coverage(case))
            facts = (FindingFact("coordination_overlap_context", subject),)
            gaps = tuple(sorted(item.value for item in payload.gap_codes))
            if gaps:
                coverage = replace(
                    coverage,
                    ledger_freshness=(
                        LedgerFreshness.PARTIAL
                        if coverage.ledger_freshness is LedgerFreshness.CURRENT
                        else coverage.ledger_freshness
                    ),
                    known_gaps=tuple(sorted({*coverage.known_gaps, *gaps}, key=str.encode)),
                )
            basis = FindingBasis(
                "coordination/coordination_overlap",
                facts,
                (),
                SubjectStateRelation.UNKNOWN,
                FrozenSourceAvailability.AVAILABLE,
                gaps,
                subject,
            )
            summary, detail = render_deterministic_finding_text(
                FindingKind.COORDINATION_OVERLAP,
                subject,
                gaps,
                facts,
            )
            candidate = CandidateFinding(
                FindingKind.COORDINATION_OVERLAP,
                FindingOrigin.DETERMINISTIC,
                FINDING_KIND_TRAITS[FindingKind.COORDINATION_OVERLAP][0],
                summary,
                detail,
                subject,
                "coordination",
                "0.1.0",
                case.frontier,
                coverage,
            )
            output.append(DeterministicAssessment(candidate, basis))
        return tuple(output)


class CoordinationDetector:
    """Compare two explicitly declared task inputs and deliver bounded advice."""

    def __init__(
        self,
        projects: ProjectApplication,
        store: CoordinationDeliveryStore,
        *,
        clock: ClockPort | None = None,
        detail_store: CoordinationDetailStore | None = None,
        context_writer: CoordinationContextWriter | None = None,
    ) -> None:
        if not callable(getattr(projects, "admit", None)):
            raise TypeError("coordination_project_application_invalid")
        if not callable(getattr(projects, "current_route_generation", None)):
            raise TypeError("coordination_project_application_invalid")
        if not callable(getattr(store, "put_detection", None)):
            raise TypeError("coordination_delivery_store_invalid")
        self.projects = projects
        self.store = store
        self.clock = clock
        self.detail_store = detail_store
        self.context_writer = context_writer
        self._participants: dict[str, tuple[CoordinationParticipant, CoordinationParticipant]] = {}

    async def _admit_pair(
        self,
        left: DeclaredCoordinationInput,
        right: DeclaredCoordinationInput,
        generation: int | None,
    ) -> tuple[CoordinationAdmission, CoordinationAdmission]:
        cross_repository = left.repository_commitment != right.repository_commitment
        if cross_repository:
            # A detection is project-scoped; cross-repository links always need the generation
            # grant.  The call still evaluates each source workspace's own consent independently.
            left_admission = await self.projects.admit(
                source_task_id=left.task_id,
                source_workspace_commitment=left.workspace_commitment,
                project=left.project_id,
                expected_generation=generation,
                expected_route_generation=left.route_generation,
                expected_route_identity_digest=left.route_identity_digest,
                expected_repository_commitment=left.repository_commitment,
                cross_repository=True,
            )
            right_admission = await self.projects.admit(
                source_task_id=right.task_id,
                source_workspace_commitment=right.workspace_commitment,
                project=right.project_id,
                expected_generation=generation,
                expected_route_generation=right.route_generation,
                expected_route_identity_digest=right.route_identity_digest,
                expected_repository_commitment=right.repository_commitment,
                cross_repository=True,
            )
            return left_admission, right_admission
        return (
            await self.projects.admit(
                source_task_id=left.task_id,
                source_workspace_commitment=left.workspace_commitment,
                project=left.project_id,
                expected_generation=generation,
                expected_route_generation=left.route_generation,
                expected_route_identity_digest=left.route_identity_digest,
                expected_repository_commitment=left.repository_commitment,
            ),
            await self.projects.admit(
                source_task_id=right.task_id,
                source_workspace_commitment=right.workspace_commitment,
                project=right.project_id,
                expected_generation=generation,
                expected_route_generation=right.route_generation,
                expected_route_identity_digest=right.route_identity_digest,
                expected_repository_commitment=right.repository_commitment,
            ),
        )

    async def _validate_route_generations(
        self,
        left: DeclaredCoordinationInput,
        right: DeclaredCoordinationInput,
    ) -> None:
        """Reject declarations captured from an older task route before they enter a detection."""

        left_current = await self.projects.current_route_generation(left.task_id)
        right_current = await self.projects.current_route_generation(right.task_id)
        if left.route_generation != left_current or right.route_generation != right_current:
            raise CoordinationError(CoordinationErrorCode.GENERATION_MISMATCH)
        for declared in (left, right):
            if declared.route_identity_digest is None:
                continue
            provenance = await self.projects.catalog.task_source_provenance(declared.task_id)
            if (
                provenance is None
                or provenance.route_identity_digest != declared.route_identity_digest
            ):
                raise CoordinationError(CoordinationErrorCode.GENERATION_MISMATCH)

    async def detect(
        self,
        left: DeclaredCoordinationInput,
        right: DeclaredCoordinationInput,
        *,
        expected_generation: int | None = None,
        coordination_declarations: Sequence[CoordinationObligationDeclaredPayload] = (),
    ) -> tuple[CoordinationAdvice, ...]:
        if (
            type(left) is not DeclaredCoordinationInput
            or type(right) is not DeclaredCoordinationInput
        ):
            raise CoordinationError(CoordinationErrorCode.INVALID)
        if left.project_id != right.project_id:
            return ()
        if left.task_id == right.task_id:
            return ()
        await self._validate_route_generations(left, right)
        left_admission, right_admission = await self._admit_pair(left, right, expected_generation)
        if left_admission.membership_generation != right_admission.membership_generation:
            raise CoordinationError(CoordinationErrorCode.GENERATION_MISMATCH)
        generation = left_admission.membership_generation
        overlap_kind: OverlapKind | None = None
        resource_ids: tuple[str, ...] = ()
        if (
            left.source_has_attributable_paths
            and right.source_has_attributable_paths
            and left.repository_commitment == right.repository_commitment
        ):
            resource_ids = overlap_resource_commitments(
                left.resources,
                right.resources,
                repository_commitment=left.repository_commitment,
                case_sensitive=left.case_sensitive and right.case_sensitive,
            )
            if resource_ids:
                overlap_kind = (
                    OverlapKind.PHYSICAL
                    if left.workspace_commitment == right.workspace_commitment
                    else OverlapKind.INTEGRATION
                )
        # Structured plan overlap is independent from path overlap.  Keep one detection with the
        # union of resource identities, never one finding per resource.
        plan_ids = tuple(
            sorted(set(left.plan_identities()) & set(right.plan_identities()), key=str.encode)
        )
        if plan_ids and overlap_kind is None:
            overlap_kind = OverlapKind.PLAN
            resource_ids = plan_ids
        if overlap_kind is None:
            return ()
        ordered_left, ordered_right = sorted((left.task_id, right.task_id), key=str.encode)
        ordered_left_input = left if left.task_id == ordered_left else right
        ordered_right_input = right if right.task_id == ordered_right else left
        route_identity_digests = (
            ordered_left_input.route_identity_digest,
            ordered_right_input.route_identity_digest,
        )
        include_route_identity = all(value is not None for value in route_identity_digests)
        detection_id = coordination_detection_identity(
            project_id_value=left.project_id,
            membership_generation=generation,
            left_task_id=ordered_left,
            right_task_id=ordered_right,
            resource_identities=resource_ids,
            left_route_generation=ordered_left_input.route_generation,
            right_route_generation=ordered_right_input.route_generation,
            left_route_identity_digest=(
                route_identity_digests[0] if include_route_identity else None
            ),
            right_route_identity_digest=(
                route_identity_digests[1] if include_route_identity else None
            ),
        )
        # Detections written by the pre-route-bound implementation remain durable and must be
        # retried in place while their participant snapshots still match.  A rotated route has a
        # different route-bound id and therefore gets a successor row instead of colliding with
        # the immutable old participant rows.
        legacy_detection_id = coordination_detection_identity(
            project_id_value=left.project_id,
            membership_generation=generation,
            left_task_id=ordered_left,
            right_task_id=ordered_right,
            resource_identities=resource_ids,
        )
        participants = cast(
            tuple[CoordinationParticipant, CoordinationParticipant],
            tuple(
                sorted(
                    (
                        CoordinationParticipant(
                            left.task_id,
                            left.project_id,
                            left.repository_commitment,
                            left.workspace_commitment,
                            left.route_generation,
                            left.source_has_attributable_paths,
                        ),
                        CoordinationParticipant(
                            right.task_id,
                            right.project_id,
                            right.repository_commitment,
                            right.workspace_commitment,
                            right.route_generation,
                            right.source_has_attributable_paths,
                        ),
                    ),
                    key=lambda item: item.task_id.encode("ascii"),
                )
            ),
        )
        if detection_id != legacy_detection_id:
            legacy = await self.store.get_detection(legacy_detection_id)
            if legacy is not None:
                prior_participants = await self.store.participants(legacy_detection_id)
                if prior_participants == participants:
                    detection_id = legacy_detection_id
        declared_ids: dict[str, str] = {}
        if type(coordination_declarations) not in {tuple, list}:
            raise CoordinationError(CoordinationErrorCode.INVALID)
        for declaration in coordination_declarations:
            if type(declaration) is not CoordinationObligationDeclaredPayload:
                raise CoordinationError(CoordinationErrorCode.INVALID)
            if declaration.project_id != left.project_id or declaration.recipient_task_id not in {
                left.task_id,
                right.task_id,
            }:
                raise CoordinationError(CoordinationErrorCode.INVALID)
            # A declaration for an older resource/generation is retained in the ledger but is
            # not allowed to purchase finding eligibility for this successor detection.
            if (
                declaration.detection_id != detection_id
                or declaration.membership_generation != generation
            ):
                continue
            recipient = declaration.recipient_task_id
            selected = _obligation(declaration.obligation_id)
            prior = declared_ids.get(recipient)
            if prior is not None and prior != selected:
                raise CoordinationError(CoordinationErrorCode.SELECTOR_CONFLICT)
            declared_ids[recipient] = selected
        declared_tasks = set(declared_ids)
        detail_ref: ProjectTextRef | None = None
        if self.detail_store is not None:
            owner_route_generation = (
                left.route_generation if left.task_id == ordered_left else right.route_generation
            )
            details = JsonObject(
                {
                    "format": COORDINATION_DETAIL_FORMAT,
                    "left_resources": list(left.resources),
                    "right_resources": list(right.resources),
                    "plan_items": list(plan_ids),
                    "case_sensitive": left.case_sensitive and right.case_sensitive,
                }
            )
            detail_ref = await self.detail_store.put_details(
                detection_id,
                details,
                owner_task_id=ordered_left,
                route_generation=owner_route_generation,
            )
        detection = CoordinationDetection(
            detection_id,
            left.project_id,
            generation,
            ordered_left,
            ordered_right,
            overlap_kind,
            tuple(resource_ids),
            right.task_id if left.task_id == ordered_left else left.task_id,
            advice_only=not bool(declared_tasks),
            obligation_declared=bool(declared_tasks),
            addressed=False,
            generation_valid=True,
            detail_ref=detail_ref,
        )
        stored_detection = await self.store.put_detection(detection)
        if stored_detection != detection:
            # A retry may add an explicit obligation to an existing advice-only identity.  Merge
            # only forward state; an old retry can never reopen a disposition, restore a revoked
            # generation, or replace a durable detail pointer.
            merged = replace(
                stored_detection,
                advice_only=stored_detection.advice_only and detection.advice_only,
                obligation_declared=stored_detection.obligation_declared
                or detection.obligation_declared,
            )
            if merged != stored_detection:
                stored_detection = await self.store.replace_detection(merged)
            detection = stored_detection
        await self.store.put_participants(detection_id, participants)
        self._participants[detection_id] = participants
        for task in sorted(declared_tasks, key=str.encode):
            await self.store.set_obligation(
                CoordinationObligationState(
                    detection_id,
                    task,
                    True,
                    obligation_id=obligation_id(declared_ids[task]),
                )
            )
        return await self._deliver(detection)

    async def _deliver(self, detection: CoordinationDetection) -> tuple[CoordinationAdvice, ...]:
        participants = self._participants.get(detection.detection_id)
        if participants is None:
            participants = await self.store.participants(detection.detection_id)
        if participants is None:
            return ()
        left, right = participants
        outputs: list[CoordinationAdvice] = []
        generation_invalidated = False
        for target, counterpart in ((left, right), (right, left)):
            try:
                admission = await self.projects.admit(
                    source_task_id=target.task_id,
                    source_workspace_commitment=target.workspace_commitment,
                    project=target.project_id,
                    expected_generation=detection.membership_generation,
                    expected_route_generation=target.route_generation,
                    expected_repository_commitment=target.repository_commitment,
                    cross_repository=target.repository_commitment
                    != counterpart.repository_commitment,
                )
            except CoordinationError as error:
                # The catalog has already recorded the generation/consent refusal; persist a
                # bounded delivery row so status can explain why this target did not receive the
                # advice.  A retry after a fresh grant/detection can deliver both idempotently.
                current = detection.membership_generation
                try:
                    current = await self.projects.current_generation(detection.project_id)
                except CoordinationError:
                    pass
                await self.store.put_delivery(
                    CoordinationDelivery(
                        detection.detection_id,
                        target.task_id,
                        "refused",
                        detection.membership_generation,
                        current,
                        error.code.value,
                    )
                )
                if error.code in {
                    CoordinationErrorCode.GRANT_REVOKED,
                    CoordinationErrorCode.GENERATION_MISMATCH,
                    CoordinationErrorCode.CONSENT_REQUIRED,
                }:
                    generation_invalidated = True
                continue
            resource_ids = detection.resource_identities
            advice = CoordinationAdvice(
                detection.detection_id,
                target.task_id,
                counterpart.task_id,
                detection.project_id,
                admission.membership_generation,
                detection.overlap_kind,
                resource_ids,
                len(resource_ids),
                "complete" if target.source_has_attributable_paths else "unobservable",
            )
            if self.context_writer is not None:
                try:
                    # Context is a durable advice fact for both participants.  Finding eligibility
                    # is separately gated by the frozen declaration binding; ``put_advice`` below
                    # is intentionally unreachable when this append is refused or ambiguous.
                    await self.context_writer.record_context(detection, target, counterpart)
                except CoordinationError as error:
                    current = detection.membership_generation
                    try:
                        current = await self.projects.current_generation(detection.project_id)
                    except CoordinationError:
                        pass
                    await self.store.put_delivery(
                        CoordinationDelivery(
                            detection.detection_id,
                            target.task_id,
                            "refused",
                            detection.membership_generation,
                            current,
                            error.code.value,
                        )
                    )
                    if error.code in {
                        CoordinationErrorCode.GRANT_REVOKED,
                        CoordinationErrorCode.GENERATION_MISMATCH,
                        CoordinationErrorCode.CONSENT_REQUIRED,
                    }:
                        generation_invalidated = True
                    continue
                try:
                    # A revoke may race the recipient-ledger append.  Re-admit both sides once
                    # more before the queue row becomes terminal; the already appended context
                    # remains an honest historical receipt, while advice delivery is refused.
                    await self.projects.admit(
                        source_task_id=target.task_id,
                        source_workspace_commitment=target.workspace_commitment,
                        project=target.project_id,
                        expected_generation=detection.membership_generation,
                        expected_route_generation=target.route_generation,
                        expected_repository_commitment=target.repository_commitment,
                        cross_repository=target.repository_commitment
                        != counterpart.repository_commitment,
                    )
                    await self.projects.admit(
                        source_task_id=counterpart.task_id,
                        source_workspace_commitment=counterpart.workspace_commitment,
                        project=counterpart.project_id,
                        expected_generation=detection.membership_generation,
                        expected_route_generation=counterpart.route_generation,
                        expected_repository_commitment=counterpart.repository_commitment,
                        cross_repository=target.repository_commitment
                        != counterpart.repository_commitment,
                    )
                except CoordinationError as error:
                    current = detection.membership_generation
                    try:
                        current = await self.projects.current_generation(detection.project_id)
                    except CoordinationError:
                        pass
                    await self.store.put_delivery(
                        CoordinationDelivery(
                            detection.detection_id,
                            target.task_id,
                            "refused",
                            detection.membership_generation,
                            current,
                            error.code.value,
                        )
                    )
                    if error.code in {
                        CoordinationErrorCode.GRANT_REVOKED,
                        CoordinationErrorCode.GENERATION_MISMATCH,
                        CoordinationErrorCode.CONSENT_REQUIRED,
                    }:
                        generation_invalidated = True
                    continue
            delivery = await self.store.put_advice(advice)
            if delivery.outcome in {"delivered", "duplicate"}:
                outputs.append(advice)
        if generation_invalidated and detection.generation_valid:
            await self.store.replace_detection(replace(detection, generation_valid=False))
        return tuple(outputs)

    async def redeliver(self, detection_id: str) -> tuple[CoordinationAdvice, ...]:
        detection = await self.store.get_detection(detection_id)
        if detection is None:
            raise CoordinationError(CoordinationErrorCode.PROJECT_NOT_FOUND)
        if not detection.generation_valid:
            return ()
        return await self._deliver(detection)

    async def disposition(
        self,
        detection_id: str,
        task_id: str,
        *,
        disposition: Literal["shared_work", "sequencing", "scope_revision"],
    ) -> CoordinationObligationState:
        """Mirror an already recorded typed disposition into retry/status state.

        The catalog flag is deliberately not the proof used by deterministic checks.  Production
        callers invoke this only after the recipient ledger accepted a
        ``coordination_disposition_recorded/1.0.0`` payload with its explicit obligation and
        evidence references; frozen checks read that payload from the recipient projection.
        """
        if disposition not in {"shared_work", "sequencing", "scope_revision"}:
            raise CoordinationError(CoordinationErrorCode.INVALID)
        current = await self.store.obligation(detection_id, _task(task_id))
        if current is None or not current.declared:
            raise CoordinationError(CoordinationErrorCode.INVALID)
        state = replace(current, addressed=True)
        await self.store.set_obligation(state)
        detection = await self.store.get_detection(detection_id)
        if detection is not None:
            states: list[CoordinationObligationState | None] = []
            for task in (detection.left_task_id, detection.right_task_id):
                states.append(await self.store.obligation(detection_id, task))
            await self.store.replace_detection(
                replace(
                    detection, addressed=all(item is not None and item.addressed for item in states)
                )
            )
        return state

    async def qualify_resolution(
        self,
        detection_id: str,
        task_id: str,
        *,
        qualifying_check: bool,
        overlap_cleared: bool,
    ) -> CoordinationObligationState:
        current = await self.store.obligation(detection_id, _task(task_id))
        if current is None or not current.declared:
            raise CoordinationError(CoordinationErrorCode.INVALID)
        if qualifying_check and current.addressed:
            state = replace(current, resolved=True)
            await self.store.set_obligation(state)
            detection = await self.store.get_detection(detection_id)
            if detection is not None:
                states: list[CoordinationObligationState | None] = []
                for task in (detection.left_task_id, detection.right_task_id):
                    states.append(await self.store.obligation(detection_id, task))
                all_addressed = all(item is not None and item.addressed for item in states)
                all_resolved = all(item is not None and item.resolved for item in states)
                await self.store.replace_detection(
                    replace(
                        detection,
                        addressed=all_addressed,
                        resolved=all_resolved,
                    )
                )
            return state
        return current


def build_coordination_delivery_store(*, catalog_db: object) -> CoordinationDeliveryStore:
    """Create the durable SQLite coordination store used by detector and status projection."""

    import apsw

    from yoetz.adapters.sqlite.project_coordination import SqliteCoordinationStore

    return SqliteCoordinationStore(cast(apsw.Connection, catalog_db))


def build_coordination_input_provider(
    *,
    projects: ProjectApplication,
    runtime: BundleRuntimePort,
    resource_provider: _ResourceProvider | None = None,
    case_sensitive: bool = True,
) -> LedgerCoordinationInputProvider:
    """Create the runtime backed input producer for attributable task scopes."""

    return LedgerCoordinationInputProvider(
        projects,
        runtime,
        resource_provider=resource_provider,
        case_sensitive=case_sensitive,
    )


def build_coordination_detector(
    *,
    projects: ProjectApplication,
    catalog_db: object,
    runtime: BundleRuntimePort | None = None,
    clock: ClockPort | None = None,
    detail_store: CoordinationDetailStore | None = None,
) -> CoordinationDetector:
    """Compose the detector with the durable SQLite delivery store.

    The caller supplies the ready service's catalog connection and, when details are enabled, a
    routed encrypted detail store.  This factory intentionally has no in-memory fallback: a
    ready production service must make detection and the two delivery records durable.
    """

    store = build_coordination_delivery_store(catalog_db=catalog_db)
    # Status projection reads through the project application.  Attach the same durable store so
    # a composition that uses this factory cannot accidentally deliver detections while exposing
    # an empty project-status list.
    if projects.detection_store is None:
        projects.detection_store = store
    context_writer = (
        None if runtime is None else RoutedCoordinationContextWriter(projects, runtime, clock=clock)
    )
    return CoordinationDetector(
        projects,
        store,
        clock=clock,
        detail_store=detail_store,
        context_writer=context_writer,
    )


def build_coordination_runtime(
    *,
    projects: ProjectApplication,
    runtime: BundleRuntimePort,
    catalog_db: object,
    clock: ClockPort | None = None,
    detail_store: CoordinationDetailStore | None = None,
    resource_provider: _ResourceProvider | None = None,
    case_sensitive: bool = True,
) -> CoordinationRuntime:
    """Compose production input loading, detection, and durable pair delivery."""

    detector = build_coordination_detector(
        projects=projects,
        runtime=runtime,
        catalog_db=catalog_db,
        clock=clock,
        detail_store=detail_store,
    )
    inputs = build_coordination_input_provider(
        projects=projects,
        runtime=runtime,
        resource_provider=resource_provider,
        case_sensitive=case_sensitive,
    )
    return CoordinationRuntime(projects, detector, inputs)
