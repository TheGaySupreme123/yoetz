"""In-memory reference implementation of the pre-writer start catalog."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import Enum
from types import TracebackType
from typing import Final, Protocol

from yoetz.domain.privacy import LocalDisclosureSink
from yoetz.domain.values import format_rfc3339_millis, validate_commitment, validate_sha256_digest
from yoetz.ports.clock import ClockPort
from yoetz.ports.ids import IdPort
from yoetz.ports.keys import MacKeyHandle
from yoetz.ports.publish_response_catalog import PublishResponseKey, StoredPublishResponse
from yoetz.ports.runtime import StartCompletionEvidence, StartMilestone
from yoetz.ports.start_catalog import (
    EXTERNAL_REF_DOMAIN,
    START_TITLE_DOMAIN,
    WORKSPACE_REF_DOMAIN,
    EncryptedResultRef,
    SafeReason,
    StartAllocation,
    StartCommand,
    StartIdentityCommitments,
    StartIdentityInput,
    StartMode,
    StartOperationLease,
    StartPhase,
    TaskRoute,
    TaskRouteState,
)
from yoetz.protocol.canonical import JsonValue, canonical_digest, canonical_encode
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError
from yoetz.protocol.ids import IdKind, validate_id

__all__ = [
    "MemoryStartCatalogAdapter",
    "MemoryStartCatalogPolicy",
    "MemoryStartCatalogState",
]

_PHASE_SUCCESSOR: Final = {
    StartPhase.ROUTE_RESERVED: StartPhase.BUNDLE_READY,
    StartPhase.BUNDLE_READY: StartPhase.LIFECYCLE_COMMITTED,
    StartPhase.LIFECYCLE_COMMITTED: StartPhase.RESULT_PUBLISHED,
}


class _AsyncLock(Protocol):
    async def __aenter__(self) -> object: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None: ...


@dataclass(frozen=True, slots=True, repr=False)
class _RouteRecord:
    task_id: str
    workspace_ref_commitment: str | None
    external_ref_commitment: str | None
    active_session_id: str
    bundle_relpath: str
    route_generation: int
    route_identity_digest: str
    state: TaskRouteState
    quarantine_code: str | None
    created_at: datetime
    updated_at: datetime
    repository_privacy_commitment: str | None = None

    def __repr__(self) -> str:
        return "_RouteRecord(<redacted>)"


class _OperationState(str, Enum):  # noqa: UP042 - mirrors the durable text vocabulary
    PENDING = "pending"
    COMPLETE = "complete"
    QUARANTINED = "quarantined"


@dataclass(frozen=True, slots=True, repr=False)
class _OperationRecord:
    installation_id: str
    operation_id: str
    request_digest: str
    requested_mode: StartMode
    route_action: str
    state: _OperationState
    phase: StartPhase
    task_id: str
    session_id: str
    writer_id: str
    lifecycle_event_id: str
    route_generation: int
    route_identity_digest: str
    owner_generation: int | None
    lease_owner_id: str | None
    lease_generation: int | None
    lease_expires_at: datetime | None
    response_object_id: str | None
    response_envelope_digest: str | None
    terminal_result_canonical: bytes | None
    terminal_result_digest: str | None
    quarantine_code: str | None
    terminal_at: datetime | None
    created_at: datetime
    updated_at: datetime

    def __repr__(self) -> str:
        return "_OperationRecord(<redacted>)"


@dataclass(slots=True, repr=False)
class MemoryStartCatalogState:
    """Process-local structural catalog state shared by reference adapters."""

    owner_generation: int = 1
    revision: int = 0
    routes: dict[str, _RouteRecord] = field(default_factory=lambda: {})
    operations: dict[tuple[str, str], _OperationRecord] = field(default_factory=lambda: {})
    session_index: dict[str, str] = field(default_factory=lambda: {})
    attachment_index: dict[tuple[str, str], str] = field(default_factory=lambda: {})
    publish_responses: dict[tuple[str, str, LocalDisclosureSink], StoredPublishResponse] = field(
        default_factory=lambda: {}
    )

    def __post_init__(self) -> None:
        if type(self.owner_generation) is not int or self.owner_generation <= 0:
            raise ValueError("catalog_owner_generation_invalid")
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("catalog_revision_invalid")

    def __repr__(self) -> str:
        return "MemoryStartCatalogState(<redacted>)"


@dataclass(frozen=True, slots=True)
class MemoryStartCatalogPolicy:
    lease_seconds: int = 60

    def __post_init__(self) -> None:
        if self.lease_seconds != 60:
            raise ValueError("start_lease_policy_invalid")


def _error(code: PublicErrorCode, *, retryable: bool = False) -> PublicOperationError:
    messages = {
        PublicErrorCode.INVALID_REQUEST: "The start request is invalid.",
        PublicErrorCode.IDEMPOTENCY_CONFLICT: "The request ID was already used.",
        PublicErrorCode.OPERATION_PENDING: "The start operation is still pending.",
        PublicErrorCode.SESSION_CONFLICT: "The requested task attachment conflicts.",
        PublicErrorCode.SESSION_NOT_FOUND: "The requested task attachment was not found.",
        PublicErrorCode.BUNDLE_BUSY: "The task is temporarily busy.",
        PublicErrorCode.STORAGE_CORRUPT: "The local catalog is inconsistent.",
        PublicErrorCode.INTERNAL_ERROR: "The start state is inconsistent.",
    }
    return PublicOperationError(code, messages[code], retryable)


def _commitment(lookup: MacKeyHandle, domain: bytes, value: str) -> str:
    result = lookup.mac(domain, canonical_encode(value))
    if type(result) is not str:
        raise _error(PublicErrorCode.INVALID_REQUEST)
    return result


def _route_value(record: _RouteRecord) -> TaskRoute:
    try:
        return TaskRoute(
            task_id=record.task_id,
            session_id=record.active_session_id,
            bundle_relpath=record.bundle_relpath,
            route_generation=record.route_generation,
            state=record.state,
            route_identity_digest=record.route_identity_digest,
            repository_privacy_commitment=record.repository_privacy_commitment,
        )
    except (TypeError, ValueError) as exc:
        raise _error(PublicErrorCode.STORAGE_CORRUPT) from exc


def _lease(record: _OperationRecord) -> StartOperationLease | None:
    if record.state is not _OperationState.PENDING:
        return None
    if (
        record.owner_generation is None
        or record.lease_owner_id is None
        or record.lease_generation is None
        or record.lease_expires_at is None
    ):
        raise _error(PublicErrorCode.STORAGE_CORRUPT)
    try:
        return StartOperationLease(
            owner_generation=record.owner_generation,
            lease_owner_id=record.lease_owner_id,
            lease_generation=record.lease_generation,
            lease_expires_at=record.lease_expires_at,
        )
    except (TypeError, ValueError) as exc:
        raise _error(PublicErrorCode.STORAGE_CORRUPT) from exc


def _allocation(record: _OperationRecord, outcome: str) -> StartAllocation:
    replayed = record.terminal_result_canonical if outcome == "replayed" else None
    expose_response = record.state is _OperationState.COMPLETE or (
        record.state is _OperationState.PENDING and record.phase is StartPhase.RESULT_PUBLISHED
    )
    try:
        return StartAllocation(
            outcome=outcome,  # type: ignore[arg-type]
            route_action=record.route_action,  # type: ignore[arg-type]
            task_id=record.task_id,
            session_id=record.session_id,
            writer_id=record.writer_id,
            lifecycle_event_id=record.lifecycle_event_id,
            bundle_relpath=f"tasks/{record.task_id}",
            route_generation=record.route_generation,
            route_identity_digest=record.route_identity_digest,
            phase=record.phase,
            response_object_id=record.response_object_id if expose_response else None,
            response_envelope_digest=(record.response_envelope_digest if expose_response else None),
            response_result_canonical=(
                record.terminal_result_canonical if expose_response else None
            ),
            response_result_digest=record.terminal_result_digest if expose_response else None,
            lease=_lease(record),
            replayed_result=replayed,
        )
    except (TypeError, ValueError) as exc:
        raise _error(PublicErrorCode.STORAGE_CORRUPT) from exc


def _same_allocation(record: _OperationRecord, allocation: StartAllocation) -> bool:
    return (
        record.task_id == allocation.task_id
        and record.session_id == allocation.session_id
        and record.writer_id == allocation.writer_id
        and record.lifecycle_event_id == allocation.lifecycle_event_id
        and record.route_generation == allocation.route_generation
        and hmac.compare_digest(record.route_identity_digest, allocation.route_identity_digest)
        and record.response_object_id == allocation.response_object_id
        and record.response_envelope_digest == allocation.response_envelope_digest
        and record.terminal_result_canonical == allocation.response_result_canonical
        and record.terminal_result_digest == allocation.response_result_digest
    )


def _evidence_value(evidence: StartCompletionEvidence) -> dict[str, JsonValue]:
    frontier: JsonValue = None
    if evidence.lifecycle_frontier is not None:
        frontier = dict(evidence.lifecycle_frontier.as_wire())
    return {
        "lifecycle_event_id": evidence.lifecycle_event_id,
        "lifecycle_frontier": frontier,
        "milestone": evidence.milestone.value,
        "owner_generation": evidence.owner_generation,
        "response_object_id": evidence.response_object_id,
        "response_envelope_digest": evidence.response_envelope_digest,
        "result_digest": evidence.result_digest,
        "route_generation": evidence.route_generation,
        "route_identity_digest": evidence.route_identity_digest,
        "session_id": evidence.session_id,
        "task_id": evidence.task_id,
        "writer_id": evidence.writer_id,
    }


def _validate_completion_evidence(
    record: _OperationRecord,
    result: EncryptedResultRef,
    evidence: StartCompletionEvidence,
) -> None:
    if (
        evidence.milestone is not StartMilestone.RESULT_PUBLISHED
        or evidence.owner_generation != record.owner_generation
        or evidence.task_id != record.task_id
        or evidence.session_id != record.session_id
        or evidence.writer_id != record.writer_id
        or evidence.lifecycle_event_id != record.lifecycle_event_id
        or evidence.route_generation != record.route_generation
        or not hmac.compare_digest(evidence.route_identity_digest, record.route_identity_digest)
        or evidence.response_object_id != result.response_object_id
        or evidence.response_envelope_digest != result.envelope_digest
        or evidence.result_digest != result.result_digest
        or not hmac.compare_digest(
            evidence.evidence_digest, canonical_digest(_evidence_value(evidence))
        )
    ):
        raise _error(PublicErrorCode.INTERNAL_ERROR)


def _quarantine_envelope(record: _OperationRecord, reason: SafeReason) -> bytes:
    return canonical_encode(
        {
            "lifecycle_event_id": record.lifecycle_event_id,
            "quarantine_code": reason.code,
            "route_identity_digest": record.route_identity_digest,
            "session_id": record.session_id,
            "task_id": record.task_id,
            "writer_id": record.writer_id,
        }
    )


class MemoryStartCatalogAdapter:
    """Executable reference state machine for ``StartCatalogPort``."""

    def __init__(
        self,
        *,
        installation_id: str,
        lookup: MacKeyHandle,
        state: MemoryStartCatalogState,
        transaction_lock: _AsyncLock,
        clock: ClockPort,
        ids: IdPort,
        policy: MemoryStartCatalogPolicy = MemoryStartCatalogPolicy(),
    ) -> None:
        self._installation_id = validate_id(IdKind.INSTALLATION, installation_id)
        self._lookup = lookup
        self._state = state
        self._lock = transaction_lock
        self._clock = clock
        self._ids = ids
        self._policy = policy
        self._lease_owner_id = ids.new(IdKind.SERVICE_INSTANCE)
        validate_id(IdKind.SERVICE_INSTANCE, self._lease_owner_id)

    async def commit_identity(self, value: StartIdentityInput) -> StartIdentityCommitments:
        if type(value) is not StartIdentityInput:
            raise _error(PublicErrorCode.INVALID_REQUEST)
        title = _commitment(self._lookup, START_TITLE_DOMAIN, value.task_title)
        workspace = None
        external = None
        if value.workspace_ref is not None and value.external_ref is not None:
            workspace = _commitment(self._lookup, WORKSPACE_REF_DOMAIN, value.workspace_ref)
            external = _commitment(self._lookup, EXTERNAL_REF_DOMAIN, value.external_ref)
        return StartIdentityCommitments(title, workspace, external)

    async def resolve_route(self, session_id: str) -> TaskRoute | None:
        try:
            session = validate_id(IdKind.SESSION, session_id)
        except (TypeError, ValueError) as exc:
            raise _error(PublicErrorCode.INVALID_REQUEST) from exc
        async with self._lock:
            task_id = self._state.session_index.get(session)
            if task_id is None:
                return None
            record = self._state.routes.get(task_id)
            if record is None or record.active_session_id != session:
                raise _error(PublicErrorCode.STORAGE_CORRUPT)
        return _route_value(record)

    async def list_workspace_task_ids(self, workspace_ref_commitment: str) -> tuple[str, ...]:
        try:
            validate_commitment(workspace_ref_commitment)
        except (TypeError, ValueError) as exc:
            raise _error(PublicErrorCode.INVALID_REQUEST) from exc
        async with self._lock:
            task_ids = sorted(
                record.task_id
                for record in self._state.routes.values()
                if record.workspace_ref_commitment == workspace_ref_commitment
                and record.state is not TaskRouteState.QUARANTINED
            )
        return tuple(task_ids)

    async def bind_repository_privacy(
        self,
        task_id: str,
        route_identity_digest: str,
        repository_privacy_commitment: str,
    ) -> TaskRoute:
        try:
            task = validate_id(IdKind.TASK, task_id)
            validate_sha256_digest(route_identity_digest)
            validate_commitment(repository_privacy_commitment)
        except (TypeError, ValueError) as exc:
            raise _error(PublicErrorCode.INVALID_REQUEST) from exc
        async with self._lock:
            record = self._state.routes.get(task)
            if record is None:
                raise _error(PublicErrorCode.SESSION_NOT_FOUND)
            if record.route_identity_digest != route_identity_digest:
                raise _error(PublicErrorCode.SESSION_CONFLICT)
            if record.repository_privacy_commitment not in {
                None,
                repository_privacy_commitment,
            }:
                raise _error(PublicErrorCode.SESSION_CONFLICT)
            if record.repository_privacy_commitment is None:
                record = replace(
                    record,
                    repository_privacy_commitment=repository_privacy_commitment,
                    updated_at=self._clock.now_utc(),
                )
                self._state.routes[task] = record
                self._state.revision += 1
        return _route_value(record)

    async def lookup(self, key: PublishResponseKey) -> StoredPublishResponse | None:
        if type(key) is not PublishResponseKey:
            raise _error(PublicErrorCode.INVALID_REQUEST)
        identity = (key.writer_id, key.request_id, key.sink)
        async with self._lock:
            existing = self._state.publish_responses.get(identity)
            if existing is None:
                return None
            if type(existing) is not StoredPublishResponse or existing.key != key:
                raise _error(PublicErrorCode.STORAGE_CORRUPT)
            return existing

    async def put_if_absent(self, value: StoredPublishResponse) -> StoredPublishResponse:
        if type(value) is not StoredPublishResponse:
            raise _error(PublicErrorCode.INVALID_REQUEST)
        key = value.key
        identity = (key.writer_id, key.request_id, key.sink)
        async with self._lock:
            existing = self._state.publish_responses.get(identity)
            if existing is None:
                self._state.publish_responses[identity] = value
                self._state.revision += 1
                return value
            if type(existing) is not StoredPublishResponse or existing.key != key:
                raise _error(PublicErrorCode.STORAGE_CORRUPT)
            return existing

    async def reserve_or_resume(self, request: StartCommand) -> StartAllocation:
        if type(request) is not StartCommand:
            raise _error(PublicErrorCode.INVALID_REQUEST)
        recomputed = await self.commit_identity(request.identity_input)
        if not self._commitments_match(recomputed, request.identity_commitments):
            raise _error(PublicErrorCode.INVALID_REQUEST)
        now = self._clock.now_utc()
        format_rfc3339_millis(now)
        proposed = {
            IdKind.TASK: self._ids.new(IdKind.TASK),
            IdKind.SESSION: self._ids.new(IdKind.SESSION),
            IdKind.WRITER: self._ids.new(IdKind.WRITER),
            IdKind.EVENT: self._ids.new(IdKind.EVENT),
        }
        for kind, candidate in proposed.items():
            validate_id(kind, candidate)
        async with self._lock:
            key = (self._installation_id, request.operation_id)
            existing = self._state.operations.get(key)
            if existing is not None:
                return self._resume_existing(existing, request, now)

            route = self._resolve_requested_route(request)
            if request.mode is StartMode.CREATE and route is not None:
                raise _error(PublicErrorCode.SESSION_CONFLICT)
            if request.mode is StartMode.ATTACH and route is None:
                raise _error(PublicErrorCode.SESSION_NOT_FOUND)

            created = route is None
            if created:
                task_id = proposed[IdKind.TASK]
                session_id = proposed[IdKind.SESSION]
                bundle_relpath = f"tasks/{task_id}"
                route_digest = canonical_digest(
                    {
                        "bundle_relpath": bundle_relpath,
                        "route_generation": 1,
                        "task_id": task_id,
                    }
                )
                route = _RouteRecord(
                    task_id=task_id,
                    workspace_ref_commitment=request.identity_commitments.workspace_ref_commitment,
                    external_ref_commitment=request.identity_commitments.external_ref_commitment,
                    active_session_id=session_id,
                    bundle_relpath=bundle_relpath,
                    route_generation=1,
                    route_identity_digest=route_digest,
                    state=TaskRouteState.INITIALIZING,
                    quarantine_code=None,
                    created_at=now,
                    updated_at=now,
                )
                self._install_route(route)
            else:
                session_id = proposed[IdKind.SESSION]

            expires = now + timedelta(seconds=self._policy.lease_seconds)
            record = _OperationRecord(
                installation_id=self._installation_id,
                operation_id=request.operation_id,
                request_digest=request.request_digest,
                requested_mode=request.mode,
                route_action="created" if created else "attached",
                state=_OperationState.PENDING,
                phase=StartPhase.ROUTE_RESERVED,
                task_id=route.task_id,
                session_id=session_id,
                writer_id=proposed[IdKind.WRITER],
                lifecycle_event_id=proposed[IdKind.EVENT],
                route_generation=route.route_generation,
                route_identity_digest=route.route_identity_digest,
                owner_generation=self._state.owner_generation,
                lease_owner_id=self._lease_owner_id,
                lease_generation=1,
                lease_expires_at=expires,
                response_object_id=None,
                response_envelope_digest=None,
                terminal_result_canonical=None,
                terminal_result_digest=None,
                quarantine_code=None,
                terminal_at=None,
                created_at=now,
                updated_at=now,
            )
            self._state.operations[key] = record
            self._state.revision += 1
            return _allocation(record, "reserved")

    async def advance_phase(
        self,
        allocation: StartAllocation,
        phase: StartPhase,
        result: EncryptedResultRef | None = None,
    ) -> StartAllocation:
        if type(allocation) is not StartAllocation or type(phase) is not StartPhase:
            raise _error(PublicErrorCode.INVALID_REQUEST)
        if phase is StartPhase.RESULT_PUBLISHED:
            if type(result) is not EncryptedResultRef:
                raise _error(PublicErrorCode.INTERNAL_ERROR)
        elif result is not None:
            raise _error(PublicErrorCode.INTERNAL_ERROR)
        now = self._clock.now_utc()
        format_rfc3339_millis(now)
        async with self._lock:
            key, record = self._operation_for(allocation)
            self._require_lease(record, allocation, now)
            if record.phase is phase:
                if result is not None and (
                    record.response_object_id != result.response_object_id
                    or record.response_envelope_digest != result.envelope_digest
                    or record.terminal_result_canonical != result.result_canonical
                    or record.terminal_result_digest != result.result_digest
                ):
                    raise _error(PublicErrorCode.INTERNAL_ERROR)
                return _allocation(record, allocation.outcome)
            if _PHASE_SUCCESSOR.get(record.phase) is not phase:
                raise _error(PublicErrorCode.INTERNAL_ERROR)
            updated = replace(
                record,
                phase=phase,
                response_object_id=(result.response_object_id if result is not None else None),
                response_envelope_digest=(result.envelope_digest if result is not None else None),
                terminal_result_canonical=(result.result_canonical if result is not None else None),
                terminal_result_digest=(result.result_digest if result is not None else None),
                updated_at=now,
            )
            self._state.operations[key] = updated
            self._state.revision += 1
            return _allocation(updated, allocation.outcome)

    async def complete(
        self,
        allocation: StartAllocation,
        result: EncryptedResultRef,
        evidence: StartCompletionEvidence,
    ) -> None:
        if (
            type(allocation) is not StartAllocation
            or type(result) is not EncryptedResultRef
            or type(evidence) is not StartCompletionEvidence
        ):
            raise _error(PublicErrorCode.INVALID_REQUEST)
        now = self._clock.now_utc()
        format_rfc3339_millis(now)
        async with self._lock:
            key, record = self._operation_for(allocation)
            self._require_lease(record, allocation, now)
            if record.phase is not StartPhase.RESULT_PUBLISHED:
                raise _error(PublicErrorCode.INTERNAL_ERROR)
            if (
                record.response_object_id != result.response_object_id
                or record.response_envelope_digest != result.envelope_digest
                or record.terminal_result_canonical != result.result_canonical
                or record.terminal_result_digest != result.result_digest
            ):
                raise _error(PublicErrorCode.INTERNAL_ERROR)
            _validate_completion_evidence(record, result, evidence)
            route = self._require_current_route(record)
            completed = replace(
                record,
                state=_OperationState.COMPLETE,
                phase=StartPhase.TERMINAL,
                owner_generation=None,
                lease_owner_id=None,
                lease_generation=None,
                lease_expires_at=None,
                terminal_result_canonical=result.result_canonical,
                terminal_result_digest=result.result_digest,
                terminal_at=now,
                updated_at=now,
            )
            self._state.operations[key] = completed
            self._activate_route(route, record.session_id, now)
            self._state.revision += 1

    async def quarantine(self, allocation: StartAllocation, reason: SafeReason) -> None:
        if type(allocation) is not StartAllocation or type(reason) is not SafeReason:
            raise _error(PublicErrorCode.INVALID_REQUEST)
        now = self._clock.now_utc()
        format_rfc3339_millis(now)
        async with self._lock:
            key, record = self._operation_for(allocation)
            self._require_lease(record, allocation, now)
            route = self._require_current_route(record)
            terminal = _quarantine_envelope(record, reason)
            quarantined = replace(
                record,
                state=_OperationState.QUARANTINED,
                phase=StartPhase.TERMINAL,
                owner_generation=None,
                lease_owner_id=None,
                lease_generation=None,
                lease_expires_at=None,
                terminal_result_canonical=terminal,
                terminal_result_digest=f"sha256:{hashlib.sha256(terminal).hexdigest()}",
                quarantine_code=reason.code,
                terminal_at=now,
                updated_at=now,
            )
            self._state.operations[key] = quarantined
            if record.route_action == "created" and route.state is TaskRouteState.INITIALIZING:
                self._state.routes[route.task_id] = replace(
                    route,
                    state=TaskRouteState.QUARANTINED,
                    quarantine_code=reason.code,
                    updated_at=now,
                )
            self._state.revision += 1

    @staticmethod
    def _commitments_match(
        actual: StartIdentityCommitments,
        expected: StartIdentityCommitments,
    ) -> bool:
        pairs = (
            (actual.title_commitment, expected.title_commitment),
            (actual.workspace_ref_commitment, expected.workspace_ref_commitment),
            (actual.external_ref_commitment, expected.external_ref_commitment),
        )
        return all(
            left is None
            and right is None
            or left is not None
            and right is not None
            and hmac.compare_digest(left, right)
            for left, right in pairs
        )

    def _resolve_requested_route(self, request: StartCommand) -> _RouteRecord | None:
        by_commitment: _RouteRecord | None = None
        workspace = request.identity_commitments.workspace_ref_commitment
        external = request.identity_commitments.external_ref_commitment
        if workspace is not None and external is not None:
            task_id = self._state.attachment_index.get((workspace, external))
            if task_id is not None:
                by_commitment = self._state.routes.get(task_id)
                if by_commitment is None:
                    raise _error(PublicErrorCode.STORAGE_CORRUPT)
        by_session: _RouteRecord | None = None
        if request.session_id is not None:
            task_id = self._state.session_index.get(request.session_id)
            if task_id is not None:
                by_session = self._state.routes.get(task_id)
                if by_session is None:
                    raise _error(PublicErrorCode.STORAGE_CORRUPT)
        if by_commitment is not None and by_session is not None:
            if by_commitment.task_id != by_session.task_id:
                raise _error(PublicErrorCode.SESSION_CONFLICT)
            return by_commitment
        if request.session_id is not None and (
            (by_session is None and by_commitment is not None)
            or (by_session is not None and workspace is not None and by_commitment is None)
        ):
            raise _error(PublicErrorCode.SESSION_CONFLICT)
        return by_commitment or by_session

    def _resume_existing(
        self,
        record: _OperationRecord,
        request: StartCommand,
        now: datetime,
    ) -> StartAllocation:
        if not hmac.compare_digest(record.request_digest, request.request_digest):
            raise _error(PublicErrorCode.IDEMPOTENCY_CONFLICT)
        if record.state is not _OperationState.PENDING:
            if record.terminal_result_canonical is None:
                raise _error(PublicErrorCode.STORAGE_CORRUPT)
            return _allocation(record, "replayed")
        if (
            record.owner_generation == self._state.owner_generation
            and record.lease_expires_at is not None
            and record.lease_expires_at > now
        ):
            raise _error(PublicErrorCode.OPERATION_PENDING, retryable=True)
        if record.lease_generation is None:
            raise _error(PublicErrorCode.STORAGE_CORRUPT)
        reclaimed = replace(
            record,
            owner_generation=self._state.owner_generation,
            lease_owner_id=self._lease_owner_id,
            lease_generation=record.lease_generation + 1,
            lease_expires_at=now + timedelta(seconds=self._policy.lease_seconds),
            updated_at=now,
        )
        self._state.operations[(record.installation_id, record.operation_id)] = reclaimed
        self._state.revision += 1
        return _allocation(reclaimed, "resumed")

    def _install_route(self, route: _RouteRecord) -> None:
        if (
            route.task_id in self._state.routes
            or route.active_session_id in self._state.session_index
        ):
            raise _error(PublicErrorCode.STORAGE_CORRUPT)
        pair: tuple[str, str] | None = None
        if route.workspace_ref_commitment is not None and route.external_ref_commitment is not None:
            pair = (route.workspace_ref_commitment, route.external_ref_commitment)
            if pair in self._state.attachment_index:
                raise _error(PublicErrorCode.SESSION_CONFLICT)
        self._state.routes[route.task_id] = route
        self._state.session_index[route.active_session_id] = route.task_id
        if pair is not None:
            self._state.attachment_index[pair] = route.task_id

    def _operation_for(
        self, allocation: StartAllocation
    ) -> tuple[tuple[str, str], _OperationRecord]:
        matches = [
            (key, record)
            for key, record in self._state.operations.items()
            if _same_allocation(record, allocation)
        ]
        if len(matches) != 1:
            raise _error(PublicErrorCode.STORAGE_CORRUPT)
        return matches[0]

    def _require_lease(
        self,
        record: _OperationRecord,
        allocation: StartAllocation,
        now: datetime,
    ) -> None:
        supplied = allocation.lease
        if (
            record.state is not _OperationState.PENDING
            or supplied is None
            or record.owner_generation != self._state.owner_generation
            or record.owner_generation != supplied.owner_generation
            or record.lease_owner_id != supplied.lease_owner_id
            or record.lease_generation != supplied.lease_generation
            or record.lease_expires_at != supplied.lease_expires_at
            or record.lease_expires_at is None
            or record.lease_expires_at <= now
        ):
            raise _error(PublicErrorCode.OPERATION_PENDING, retryable=True)

    def _require_current_route(self, record: _OperationRecord) -> _RouteRecord:
        route = self._state.routes.get(record.task_id)
        if (
            route is None
            or route.route_generation != record.route_generation
            or not hmac.compare_digest(route.route_identity_digest, record.route_identity_digest)
            or route.bundle_relpath != f"tasks/{record.task_id}"
        ):
            raise _error(PublicErrorCode.STORAGE_CORRUPT)
        return route

    def _activate_route(self, route: _RouteRecord, session_id: str, now: datetime) -> None:
        old_session = route.active_session_id
        existing = self._state.session_index.get(session_id)
        if existing is not None and existing != route.task_id:
            raise _error(PublicErrorCode.STORAGE_CORRUPT)
        if self._state.session_index.get(old_session) != route.task_id:
            raise _error(PublicErrorCode.STORAGE_CORRUPT)
        del self._state.session_index[old_session]
        self._state.session_index[session_id] = route.task_id
        self._state.routes[route.task_id] = replace(
            route,
            active_session_id=session_id,
            state=TaskRouteState.ACTIVE,
            quarantine_code=None,
            updated_at=now,
        )
