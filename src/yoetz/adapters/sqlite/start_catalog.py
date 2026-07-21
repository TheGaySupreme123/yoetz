"""Generation-fenced start catalog state machine over the catalog database."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from types import TracebackType
from typing import Final, Literal, cast

import apsw

from yoetz.domain.values import format_rfc3339_millis, parse_rfc3339_millis
from yoetz.ports.clock import ClockPort
from yoetz.ports.ids import IdPort
from yoetz.ports.keys import MacKeyHandle
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
    "CATALOG_SCHEMA_VERSION",
    "SqliteStartCatalog",
    "StartQuarantineCode",
    "external_ref_commitment",
    "workspace_ref_commitment",
]

CATALOG_SCHEMA_VERSION: Final = 1
_LEASE_SECONDS: Final = 60
_PHASE_SUCCESSOR: Final = {
    StartPhase.ROUTE_RESERVED: StartPhase.BUNDLE_READY,
    StartPhase.BUNDLE_READY: StartPhase.LIFECYCLE_COMMITTED,
    StartPhase.LIFECYCLE_COMMITTED: StartPhase.RESULT_PUBLISHED,
}


class StartQuarantineCode(str, Enum):  # noqa: UP042 - mirrors the durable text vocabulary
    START_ALLOCATION_AMBIGUOUS = "start_allocation_ambiguous"
    START_BUNDLE_INVALID = "start_bundle_invalid"
    START_CATALOG_INTEGRITY = "start_catalog_integrity"
    START_LIFECYCLE_CONTRADICTION = "start_lifecycle_contradiction"
    START_RESULT_OBJECT_MISSING = "start_result_object_missing"
    START_ROUTE_CONTRADICTION = "start_route_contradiction"


@dataclass(frozen=True, slots=True)
class _RouteRow:
    task_id: str
    workspace_ref_commitment: str | None
    external_ref_commitment: str | None
    active_session_id: str
    bundle_relpath: str
    route_generation: int
    route_identity_digest: str
    state: TaskRouteState


@dataclass(frozen=True, slots=True)
class _OperationRow:
    installation_id: str
    operation_id: str
    request_digest: str
    requested_mode: StartMode
    route_action: str
    state: str
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


class _Transaction:
    def __init__(self, db: apsw.Connection) -> None:
        self._db = db

    def __enter__(self) -> None:
        try:
            self._db.execute("BEGIN IMMEDIATE")
        except apsw.BusyError as exc:
            raise _error(PublicErrorCode.BUNDLE_BUSY, retryable=True) from exc

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        if exc_type is None:
            self._db.execute("COMMIT")
        else:
            self._db.execute("ROLLBACK")
        return False


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


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str:
        raise _error(PublicErrorCode.STORAGE_CORRUPT)
    return value


def _text(value: object) -> str:
    result = _optional_text(value)
    if result is None:
        raise _error(PublicErrorCode.STORAGE_CORRUPT)
    return result


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise _error(PublicErrorCode.STORAGE_CORRUPT)
    return value


def _integer(value: object) -> int:
    result = _optional_int(value)
    if result is None:
        raise _error(PublicErrorCode.STORAGE_CORRUPT)
    return result


def _optional_bytes(value: object) -> bytes | None:
    if value is None:
        return None
    if type(value) is not bytes:
        raise _error(PublicErrorCode.STORAGE_CORRUPT)
    return value


def _commitment(lookup: MacKeyHandle, domain: bytes, value: JsonValue) -> str:
    result = lookup.mac(domain, canonical_encode(value))
    if type(result) is not str:
        raise _error(PublicErrorCode.INVALID_REQUEST)
    return result


def workspace_ref_commitment(lookup: MacKeyHandle, workspace_ref: JsonValue) -> str:
    return _commitment(lookup, WORKSPACE_REF_DOMAIN, workspace_ref)


def external_ref_commitment(lookup: MacKeyHandle, external_ref: JsonValue) -> str:
    return _commitment(lookup, EXTERNAL_REF_DOMAIN, external_ref)


def _route_from_row(row: tuple[object, ...]) -> _RouteRow:
    if len(row) != 8:
        raise _error(PublicErrorCode.STORAGE_CORRUPT)
    try:
        return _RouteRow(
            task_id=_text(row[0]),
            workspace_ref_commitment=_optional_text(row[1]),
            external_ref_commitment=_optional_text(row[2]),
            active_session_id=_text(row[3]),
            bundle_relpath=_text(row[4]),
            route_generation=_integer(row[5]),
            route_identity_digest=_text(row[6]),
            state=TaskRouteState(_text(row[7])),
        )
    except ValueError as exc:
        raise _error(PublicErrorCode.STORAGE_CORRUPT) from exc


def _route_value(row: _RouteRow) -> TaskRoute:
    try:
        return TaskRoute(
            task_id=row.task_id,
            session_id=row.active_session_id,
            bundle_relpath=row.bundle_relpath,
            route_generation=row.route_generation,
            state=row.state,
            route_identity_digest=row.route_identity_digest,
        )
    except (TypeError, ValueError) as exc:
        raise _error(PublicErrorCode.STORAGE_CORRUPT) from exc


def _operation_from_row(row: tuple[object, ...]) -> _OperationRow:
    if len(row) != 22:
        raise _error(PublicErrorCode.STORAGE_CORRUPT)
    owner_generation_text = _optional_text(row[13])
    try:
        owner_generation = None if owner_generation_text is None else int(owner_generation_text, 10)
        lease_expires_at = None if row[16] is None else parse_rfc3339_millis(_text(row[16]))
        return _OperationRow(
            installation_id=_text(row[0]),
            operation_id=_text(row[1]),
            request_digest=_text(row[2]),
            requested_mode=StartMode(_text(row[3])),
            route_action=_text(row[4]),
            state=_text(row[5]),
            phase=StartPhase(_text(row[6])),
            task_id=_text(row[7]),
            session_id=_text(row[8]),
            writer_id=_text(row[9]),
            lifecycle_event_id=_text(row[10]),
            route_generation=_integer(row[11]),
            route_identity_digest=_text(row[12]),
            owner_generation=owner_generation,
            lease_owner_id=_optional_text(row[14]),
            lease_generation=_optional_int(row[15]),
            lease_expires_at=lease_expires_at,
            response_object_id=_optional_text(row[17]),
            response_envelope_digest=_optional_text(row[18]),
            terminal_result_canonical=_optional_bytes(row[19]),
            terminal_result_digest=_optional_text(row[20]),
            quarantine_code=_optional_text(row[21]),
        )
    except (TypeError, ValueError) as exc:
        raise _error(PublicErrorCode.STORAGE_CORRUPT) from exc


def _lease(row: _OperationRow) -> StartOperationLease | None:
    if row.state != "pending":
        return None
    if (
        row.owner_generation is None
        or row.lease_owner_id is None
        or row.lease_generation is None
        or row.lease_expires_at is None
    ):
        raise _error(PublicErrorCode.STORAGE_CORRUPT)
    try:
        return StartOperationLease(
            row.owner_generation,
            row.lease_owner_id,
            row.lease_generation,
            row.lease_expires_at,
        )
    except (TypeError, ValueError) as exc:
        raise _error(PublicErrorCode.STORAGE_CORRUPT) from exc


def _allocation(row: _OperationRow, outcome: str) -> StartAllocation:
    replayed = row.terminal_result_canonical if outcome == "replayed" else None
    expose_response = row.state == "complete" or (
        row.state == "pending" and row.phase is StartPhase.RESULT_PUBLISHED
    )
    try:
        return StartAllocation(
            outcome=outcome,  # type: ignore[arg-type]
            route_action=row.route_action,  # type: ignore[arg-type]
            task_id=row.task_id,
            session_id=row.session_id,
            writer_id=row.writer_id,
            lifecycle_event_id=row.lifecycle_event_id,
            bundle_relpath=f"tasks/{row.task_id}",
            route_generation=row.route_generation,
            route_identity_digest=row.route_identity_digest,
            phase=row.phase,
            response_object_id=row.response_object_id if expose_response else None,
            response_envelope_digest=(row.response_envelope_digest if expose_response else None),
            response_result_canonical=(row.terminal_result_canonical if expose_response else None),
            response_result_digest=(row.terminal_result_digest if expose_response else None),
            lease=_lease(row),
            replayed_result=replayed,
        )
    except (TypeError, ValueError) as exc:
        raise _error(PublicErrorCode.STORAGE_CORRUPT) from exc


def _same_allocation(row: _OperationRow, allocation: StartAllocation) -> bool:
    return (
        row.task_id == allocation.task_id
        and row.session_id == allocation.session_id
        and row.writer_id == allocation.writer_id
        and row.lifecycle_event_id == allocation.lifecycle_event_id
        and row.route_generation == allocation.route_generation
        and hmac.compare_digest(row.route_identity_digest, allocation.route_identity_digest)
        and row.response_object_id == allocation.response_object_id
        and row.response_envelope_digest == allocation.response_envelope_digest
        and row.terminal_result_canonical == allocation.response_result_canonical
        and row.terminal_result_digest == allocation.response_result_digest
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
        "response_envelope_digest": evidence.response_envelope_digest,
        "response_object_id": evidence.response_object_id,
        "result_digest": evidence.result_digest,
        "route_generation": evidence.route_generation,
        "route_identity_digest": evidence.route_identity_digest,
        "session_id": evidence.session_id,
        "task_id": evidence.task_id,
        "writer_id": evidence.writer_id,
    }


def _validate_completion_evidence(
    row: _OperationRow,
    result: EncryptedResultRef,
    evidence: StartCompletionEvidence,
) -> None:
    if (
        evidence.milestone is not StartMilestone.RESULT_PUBLISHED
        or evidence.owner_generation != row.owner_generation
        or evidence.task_id != row.task_id
        or evidence.session_id != row.session_id
        or evidence.writer_id != row.writer_id
        or evidence.lifecycle_event_id != row.lifecycle_event_id
        or evidence.route_generation != row.route_generation
        or not hmac.compare_digest(evidence.route_identity_digest, row.route_identity_digest)
        or evidence.response_object_id != result.response_object_id
        or evidence.response_envelope_digest != result.envelope_digest
        or evidence.result_digest != result.result_digest
        or not hmac.compare_digest(
            evidence.evidence_digest, canonical_digest(_evidence_value(evidence))
        )
    ):
        raise _error(PublicErrorCode.INTERNAL_ERROR)


def _quarantine_envelope(row: _OperationRow, reason: SafeReason) -> bytes:
    return canonical_encode(
        {
            "lifecycle_event_id": row.lifecycle_event_id,
            "quarantine_code": reason.code,
            "route_identity_digest": row.route_identity_digest,
            "session_id": row.session_id,
            "task_id": row.task_id,
            "writer_id": row.writer_id,
        }
    )


_ROUTE_COLUMNS: Final = """
task_id, workspace_ref_commitment, external_ref_commitment, active_session_id,
bundle_relpath, route_generation, active_route_identity_digest, state
"""

_OPERATION_COLUMNS: Final = """
installation_id, operation_id, request_digest, requested_mode, route_action, state, phase,
task_id, session_id, writer_id, lifecycle_event_id, route_generation, route_identity_digest,
owner_generation, lease_owner_id, lease_generation, lease_expires_at, response_object_id,
response_envelope_digest, terminal_result_canonical, terminal_result_digest, quarantine_code
"""


class SqliteStartCatalog:
    """Durable ``StartCatalogPort`` implementation using the frozen catalog schema."""

    def __init__(
        self,
        connection: apsw.Connection,
        *,
        installation_id: str,
        lookup: MacKeyHandle,
        clock: ClockPort,
        ids: IdPort,
    ) -> None:
        if type(connection) is not apsw.Connection:
            raise TypeError("catalog_connection_invalid")
        self._db = connection
        self._installation_id = validate_id(IdKind.INSTALLATION, installation_id)
        self._lookup = lookup
        self._clock = clock
        self._ids = ids
        self._lease_owner_id = ids.new(IdKind.SERVICE_INSTANCE)
        validate_id(IdKind.SERVICE_INSTANCE, self._lease_owner_id)

    @property
    def generation(self) -> int:
        return self._owner_generation()

    async def commit_identity(self, value: StartIdentityInput) -> StartIdentityCommitments:
        if type(value) is not StartIdentityInput:
            raise _error(PublicErrorCode.INVALID_REQUEST)
        title = _commitment(self._lookup, START_TITLE_DOMAIN, value.task_title)
        workspace = None
        external = None
        if value.workspace_ref is not None and value.external_ref is not None:
            workspace = workspace_ref_commitment(self._lookup, value.workspace_ref)
            external = external_ref_commitment(self._lookup, value.external_ref)
        return StartIdentityCommitments(title, workspace, external)

    async def resolve_route(self, session_id: str) -> TaskRoute | None:
        try:
            session = validate_id(IdKind.SESSION, session_id)
        except (TypeError, ValueError) as exc:
            raise _error(PublicErrorCode.INVALID_REQUEST) from exc
        rows = self._rows(
            f"SELECT {_ROUTE_COLUMNS} FROM task_routes WHERE active_session_id = ? LIMIT 2",
            (session,),
        )
        if not rows:
            return None
        if len(rows) != 1:
            raise _error(PublicErrorCode.STORAGE_CORRUPT)
        return _route_value(_route_from_row(rows[0]))

    async def reserve_or_resume(self, request: StartCommand) -> StartAllocation:
        if type(request) is not StartCommand:
            raise _error(PublicErrorCode.INVALID_REQUEST)
        recomputed = await self.commit_identity(request.identity_input)
        if not self._commitments_match(recomputed, request.identity_commitments):
            raise _error(PublicErrorCode.INVALID_REQUEST)
        now = self._clock.now_utc()
        now_wire = format_rfc3339_millis(now)
        proposed = {
            IdKind.TASK: self._ids.new(IdKind.TASK),
            IdKind.SESSION: self._ids.new(IdKind.SESSION),
            IdKind.WRITER: self._ids.new(IdKind.WRITER),
            IdKind.EVENT: self._ids.new(IdKind.EVENT),
        }
        for kind, candidate in proposed.items():
            validate_id(kind, candidate)
        with self._transaction():
            owner_generation = self._owner_generation()
            existing = self._operation_by_key(request.operation_id)
            if existing is not None:
                return self._resume_existing(existing, request, now, now_wire, owner_generation)

            route = self._resolve_requested_route(request)
            if request.mode is StartMode.CREATE and route is not None:
                raise _error(PublicErrorCode.SESSION_CONFLICT)
            if request.mode is StartMode.ATTACH and route is None:
                raise _error(PublicErrorCode.SESSION_NOT_FOUND)
            if route is not None:
                self._require_no_exclusive_maintenance(route.task_id)

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
                self._db.execute(
                    """INSERT INTO task_routes (
                        task_id, workspace_ref_commitment, external_ref_commitment,
                        active_session_id, bundle_relpath, route_generation,
                        active_route_identity_digest, state, quarantine_code, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 1, ?, 'initializing', NULL, ?, ?)""",
                    (
                        task_id,
                        request.identity_commitments.workspace_ref_commitment,
                        request.identity_commitments.external_ref_commitment,
                        session_id,
                        bundle_relpath,
                        route_digest,
                        now_wire,
                        now_wire,
                    ),
                )
                route = _RouteRow(
                    task_id,
                    request.identity_commitments.workspace_ref_commitment,
                    request.identity_commitments.external_ref_commitment,
                    session_id,
                    bundle_relpath,
                    1,
                    route_digest,
                    TaskRouteState.INITIALIZING,
                )
            else:
                session_id = proposed[IdKind.SESSION]

            lease_expires_at = format_rfc3339_millis(now + timedelta(seconds=_LEASE_SECONDS))
            self._db.execute(
                """INSERT INTO start_operations (
                    installation_id, operation_id, request_digest, requested_mode, route_action,
                    state, phase, task_id, session_id, writer_id, lifecycle_event_id,
                    route_generation, route_identity_digest, owner_generation, lease_owner_id,
                    lease_generation, lease_expires_at, response_object_id, response_envelope_digest,
                    terminal_result_canonical, terminal_result_digest, quarantine_code,
                    terminal_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', 'route_reserved', ?, ?, ?, ?, ?, ?, ?, ?, 1,
                    ?, NULL, NULL, NULL, NULL, NULL, NULL, ?, ?)""",
                (
                    self._installation_id,
                    request.operation_id,
                    request.request_digest,
                    request.mode.value,
                    "created" if created else "attached",
                    route.task_id,
                    session_id,
                    proposed[IdKind.WRITER],
                    proposed[IdKind.EVENT],
                    route.route_generation,
                    route.route_identity_digest,
                    str(owner_generation),
                    self._lease_owner_id,
                    lease_expires_at,
                    now_wire,
                    now_wire,
                ),
            )
            inserted = self._operation_by_key(request.operation_id)
            if inserted is None:
                raise _error(PublicErrorCode.STORAGE_CORRUPT)
            return _allocation(inserted, "reserved")

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
        now_wire = format_rfc3339_millis(now)
        with self._transaction():
            row = self._operation_for(allocation)
            self._require_lease(row, allocation, now, self._owner_generation())
            if row.phase is phase:
                if result is not None and (
                    row.response_object_id != result.response_object_id
                    or row.response_envelope_digest != result.envelope_digest
                    or row.terminal_result_canonical != result.result_canonical
                    or row.terminal_result_digest != result.result_digest
                ):
                    raise _error(PublicErrorCode.INTERNAL_ERROR)
                return _allocation(row, allocation.outcome)
            if _PHASE_SUCCESSOR.get(row.phase) is not phase:
                raise _error(PublicErrorCode.INTERNAL_ERROR)
            response_object_id = result.response_object_id if result is not None else None
            response_envelope_digest = result.envelope_digest if result is not None else None
            result_canonical = result.result_canonical if result is not None else None
            result_digest = result.result_digest if result is not None else None
            cursor = self._db.execute(
                """UPDATE start_operations SET phase = ?, response_object_id = ?,
                   response_envelope_digest = ?, terminal_result_canonical = ?,
                   terminal_result_digest = ?, updated_at = ?
                   WHERE installation_id = ? AND operation_id = ? AND state = 'pending'
                     AND phase = ? AND owner_generation = ? AND lease_owner_id = ?
                     AND lease_generation = ? AND lease_expires_at = ?""",
                (
                    phase.value,
                    response_object_id,
                    response_envelope_digest,
                    result_canonical,
                    result_digest,
                    now_wire,
                    row.installation_id,
                    row.operation_id,
                    row.phase.value,
                    str(row.owner_generation),
                    row.lease_owner_id,
                    row.lease_generation,
                    format_rfc3339_millis(cast(datetime, row.lease_expires_at)),
                ),
            )
            if cursor.getconnection().changes() != 1:
                raise _error(PublicErrorCode.OPERATION_PENDING, retryable=True)
            updated = self._operation_by_key(row.operation_id)
            if updated is None:
                raise _error(PublicErrorCode.STORAGE_CORRUPT)
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
        now_wire = format_rfc3339_millis(now)
        with self._transaction():
            row = self._operation_for(allocation)
            self._require_lease(row, allocation, now, self._owner_generation())
            if row.phase is not StartPhase.RESULT_PUBLISHED:
                raise _error(PublicErrorCode.INTERNAL_ERROR)
            if (
                row.response_object_id != result.response_object_id
                or row.response_envelope_digest != result.envelope_digest
                or row.terminal_result_canonical != result.result_canonical
                or row.terminal_result_digest != result.result_digest
            ):
                raise _error(PublicErrorCode.INTERNAL_ERROR)
            _validate_completion_evidence(row, result, evidence)
            route = self._require_current_route(row)
            self._require_no_exclusive_maintenance(row.task_id)
            self._db.execute(
                """UPDATE task_routes
                   SET active_session_id = ?, state = 'active', quarantine_code = NULL, updated_at = ?
                   WHERE task_id = ? AND route_generation = ?
                     AND active_route_identity_digest = ?""",
                (
                    row.session_id,
                    now_wire,
                    route.task_id,
                    route.route_generation,
                    route.route_identity_digest,
                ),
            )
            if self._db.changes() != 1:
                raise _error(PublicErrorCode.STORAGE_CORRUPT)
            self._db.execute(
                """UPDATE start_operations SET
                    state = 'complete', phase = 'terminal', owner_generation = NULL,
                    lease_owner_id = NULL, lease_generation = NULL, lease_expires_at = NULL,
                    terminal_result_canonical = ?, terminal_result_digest = ?, terminal_at = ?,
                    updated_at = ?
                   WHERE installation_id = ? AND operation_id = ? AND state = 'pending'
                     AND phase = 'result_published'""",
                (
                    result.result_canonical,
                    result.result_digest,
                    now_wire,
                    now_wire,
                    row.installation_id,
                    row.operation_id,
                ),
            )
            if self._db.changes() != 1:
                raise _error(PublicErrorCode.OPERATION_PENDING, retryable=True)

    async def quarantine(self, allocation: StartAllocation, reason: SafeReason) -> None:
        if type(allocation) is not StartAllocation or type(reason) is not SafeReason:
            raise _error(PublicErrorCode.INVALID_REQUEST)
        now = self._clock.now_utc()
        now_wire = format_rfc3339_millis(now)
        with self._transaction():
            row = self._operation_for(allocation)
            self._require_lease(row, allocation, now, self._owner_generation())
            route = self._require_current_route(row)
            terminal = _quarantine_envelope(row, reason)
            terminal_digest = f"sha256:{hashlib.sha256(terminal).hexdigest()}"
            if row.route_action == "created" and route.state is TaskRouteState.INITIALIZING:
                self._db.execute(
                    """UPDATE task_routes SET state = 'quarantined', quarantine_code = ?,
                       updated_at = ? WHERE task_id = ? AND state = 'initializing'""",
                    (reason.code, now_wire, route.task_id),
                )
                if self._db.changes() != 1:
                    raise _error(PublicErrorCode.STORAGE_CORRUPT)
            self._db.execute(
                """UPDATE start_operations SET
                    state = 'quarantined', phase = 'terminal', owner_generation = NULL,
                    lease_owner_id = NULL, lease_generation = NULL, lease_expires_at = NULL,
                    response_object_id = NULL, response_envelope_digest = NULL,
                    terminal_result_canonical = ?, terminal_result_digest = ?, quarantine_code = ?,
                    terminal_at = ?, updated_at = ?
                   WHERE installation_id = ? AND operation_id = ? AND state = 'pending'""",
                (
                    terminal,
                    terminal_digest,
                    reason.code,
                    now_wire,
                    now_wire,
                    row.installation_id,
                    row.operation_id,
                ),
            )
            if self._db.changes() != 1:
                raise _error(PublicErrorCode.OPERATION_PENDING, retryable=True)

    def _transaction(self) -> _Transaction:
        return _Transaction(self._db)

    def _rows(self, sql: str, bindings: tuple[apsw.Binding, ...]) -> list[tuple[object, ...]]:
        cursor = self._db.execute(sql, bindings)
        return [cast(tuple[object, ...], row) for row in cursor]

    def _owner_generation(self) -> int:
        values = self._rows(
            "SELECT key, value FROM catalog_meta WHERE key IN ('installation_id', 'owner_generation')",
            (),
        )
        metadata = {_text(row[0]): _text(row[1]) for row in values if len(row) == 2}
        if metadata.get("installation_id") != self._installation_id:
            raise _error(PublicErrorCode.STORAGE_CORRUPT)
        try:
            generation = int(metadata["owner_generation"], 10)
        except (KeyError, ValueError) as exc:
            raise _error(PublicErrorCode.STORAGE_CORRUPT) from exc
        if generation <= 0:
            raise _error(PublicErrorCode.STORAGE_CORRUPT)
        return generation

    def _operation_by_key(self, operation_id: str) -> _OperationRow | None:
        rows = self._rows(
            f"SELECT {_OPERATION_COLUMNS} FROM start_operations "
            "WHERE installation_id = ? AND operation_id = ? LIMIT 2",
            (self._installation_id, operation_id),
        )
        if not rows:
            return None
        if len(rows) != 1:
            raise _error(PublicErrorCode.STORAGE_CORRUPT)
        return _operation_from_row(rows[0])

    def _operation_for(self, allocation: StartAllocation) -> _OperationRow:
        rows = self._rows(
            f"SELECT {_OPERATION_COLUMNS} FROM start_operations WHERE installation_id = ? "
            "AND task_id = ? AND session_id = ? AND writer_id = ? AND lifecycle_event_id = ? LIMIT 2",
            (
                self._installation_id,
                allocation.task_id,
                allocation.session_id,
                allocation.writer_id,
                allocation.lifecycle_event_id,
            ),
        )
        if len(rows) != 1:
            raise _error(PublicErrorCode.STORAGE_CORRUPT)
        row = _operation_from_row(rows[0])
        if not _same_allocation(row, allocation):
            raise _error(PublicErrorCode.STORAGE_CORRUPT)
        return row

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

    def _resolve_requested_route(self, request: StartCommand) -> _RouteRow | None:
        by_commitment: _RouteRow | None = None
        workspace = request.identity_commitments.workspace_ref_commitment
        external = request.identity_commitments.external_ref_commitment
        if workspace is not None and external is not None:
            rows = self._rows(
                f"SELECT {_ROUTE_COLUMNS} FROM task_routes "
                "WHERE workspace_ref_commitment = ? AND external_ref_commitment = ? LIMIT 2",
                (workspace, external),
            )
            if len(rows) > 1:
                raise _error(PublicErrorCode.STORAGE_CORRUPT)
            if rows:
                by_commitment = _route_from_row(rows[0])
        by_session: _RouteRow | None = None
        if request.session_id is not None:
            rows = self._rows(
                f"SELECT {_ROUTE_COLUMNS} FROM task_routes WHERE active_session_id = ? LIMIT 2",
                (request.session_id,),
            )
            if len(rows) > 1:
                raise _error(PublicErrorCode.STORAGE_CORRUPT)
            if rows:
                by_session = _route_from_row(rows[0])
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
        row: _OperationRow,
        request: StartCommand,
        now: datetime,
        now_wire: str,
        owner_generation: int,
    ) -> StartAllocation:
        if not hmac.compare_digest(row.request_digest, request.request_digest):
            raise _error(PublicErrorCode.IDEMPOTENCY_CONFLICT)
        if row.state != "pending":
            if row.terminal_result_canonical is None:
                raise _error(PublicErrorCode.STORAGE_CORRUPT)
            return _allocation(row, "replayed")
        if (
            row.owner_generation == owner_generation
            and row.lease_expires_at is not None
            and row.lease_expires_at > now
        ):
            raise _error(PublicErrorCode.OPERATION_PENDING, retryable=True)
        if row.lease_generation is None:
            raise _error(PublicErrorCode.STORAGE_CORRUPT)
        expires_wire = format_rfc3339_millis(now + timedelta(seconds=_LEASE_SECONDS))
        self._db.execute(
            """UPDATE start_operations SET owner_generation = ?, lease_owner_id = ?,
               lease_generation = ?, lease_expires_at = ?, updated_at = ?
               WHERE installation_id = ? AND operation_id = ? AND state = 'pending'
                 AND owner_generation IS ? AND lease_owner_id IS ? AND lease_generation IS ?
                 AND lease_expires_at IS ?""",
            (
                str(owner_generation),
                self._lease_owner_id,
                row.lease_generation + 1,
                expires_wire,
                now_wire,
                row.installation_id,
                row.operation_id,
                None if row.owner_generation is None else str(row.owner_generation),
                row.lease_owner_id,
                row.lease_generation,
                None
                if row.lease_expires_at is None
                else format_rfc3339_millis(row.lease_expires_at),
            ),
        )
        if self._db.changes() != 1:
            raise _error(PublicErrorCode.OPERATION_PENDING, retryable=True)
        reclaimed = self._operation_by_key(row.operation_id)
        if reclaimed is None:
            raise _error(PublicErrorCode.STORAGE_CORRUPT)
        return _allocation(reclaimed, "resumed")

    def _require_lease(
        self,
        row: _OperationRow,
        allocation: StartAllocation,
        now: datetime,
        owner_generation: int,
    ) -> None:
        supplied = allocation.lease
        if (
            row.state != "pending"
            or supplied is None
            or row.owner_generation != owner_generation
            or row.owner_generation != supplied.owner_generation
            or row.lease_owner_id != supplied.lease_owner_id
            or row.lease_generation != supplied.lease_generation
            or row.lease_expires_at != supplied.lease_expires_at
            or row.lease_expires_at is None
            or row.lease_expires_at <= now
        ):
            raise _error(PublicErrorCode.OPERATION_PENDING, retryable=True)

    def _require_current_route(self, row: _OperationRow) -> _RouteRow:
        rows = self._rows(
            f"SELECT {_ROUTE_COLUMNS} FROM task_routes WHERE task_id = ? LIMIT 2", (row.task_id,)
        )
        if len(rows) != 1:
            raise _error(PublicErrorCode.STORAGE_CORRUPT)
        route = _route_from_row(rows[0])
        if (
            route.route_generation != row.route_generation
            or not hmac.compare_digest(route.route_identity_digest, row.route_identity_digest)
            or route.bundle_relpath != f"tasks/{row.task_id}"
        ):
            raise _error(PublicErrorCode.STORAGE_CORRUPT)
        return route

    def _require_no_exclusive_maintenance(self, task_id: str) -> None:
        rows = self._rows(
            """SELECT operation_id FROM maintenance_operations
               WHERE task_id = ? AND state = 'pending' AND kind IN ('restore', 'migration')
               LIMIT 1""",
            (task_id,),
        )
        if rows:
            raise _error(PublicErrorCode.BUNDLE_BUSY, retryable=True)
