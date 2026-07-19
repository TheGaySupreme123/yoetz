"""Pre-writer start allocation, routing, and idempotency boundary."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Final, Literal, Protocol

from yoetz.domain.values import (
    format_rfc3339_millis,
    validate_commitment,
    validate_sha256_digest,
)
from yoetz.ports.runtime import StartCompletionEvidence
from yoetz.protocol.canonical import canonical_digest, ensure_canonical_value
from yoetz.protocol.ids import IdKind, validate_id

__all__ = [
    "EXTERNAL_REF_DOMAIN",
    "START_TITLE_DOMAIN",
    "WORKSPACE_REF_DOMAIN",
    "EncryptedResultRef",
    "SafeReason",
    "StartAllocation",
    "StartCatalogPort",
    "StartCommand",
    "StartIdentityCommitments",
    "StartIdentityInput",
    "StartMode",
    "StartOperationLease",
    "StartPhase",
    "TaskRoute",
    "TaskRouteState",
]

START_TITLE_DOMAIN: Final = b"yoetz/start-title/v1\x00"
WORKSPACE_REF_DOMAIN: Final = b"yoetz/workspace-ref/v1\x00"
EXTERNAL_REF_DOMAIN: Final = b"yoetz/external-task-ref/v1\x00"

_MAX_SAFE_INTEGER: Final = 2**53 - 1
_SAFE_TOKEN_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
_START_QUARANTINE_CODES: Final = frozenset(
    {
        "start_allocation_ambiguous",
        "start_bundle_invalid",
        "start_catalog_integrity",
        "start_lifecycle_contradiction",
        "start_result_object_missing",
        "start_route_contradiction",
    }
)


class TaskRouteState(str, Enum):  # noqa: UP042 - exact durable enum base
    INITIALIZING = "initializing"
    ACTIVE = "active"
    QUARANTINED = "quarantined"


class StartPhase(str, Enum):  # noqa: UP042 - exact durable enum base
    ROUTE_RESERVED = "route_reserved"
    BUNDLE_READY = "bundle_ready"
    LIFECYCLE_COMMITTED = "lifecycle_committed"
    RESULT_PUBLISHED = "result_published"
    TERMINAL = "terminal"


class StartMode(str, Enum):  # noqa: UP042 - exact request enum base
    CREATE = "create"
    ATTACH = "attach"
    CREATE_OR_ATTACH = "create_or_attach"


def _invalid() -> ValueError:
    return ValueError("invalid_start_catalog_value")


def _id(kind: IdKind, value: object) -> str:
    try:
        return validate_id(kind, value)
    except ValueError as exc:
        raise _invalid() from exc


def _safe_token(value: object) -> str:
    if type(value) is not str or _SAFE_TOKEN_PATTERN.fullmatch(value) is None:
        raise _invalid()
    return value


def _optional_identity(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str or not 1 <= len(value.encode("utf-8")) <= 8_192:
        raise _invalid()
    try:
        ensure_canonical_value(value)
    except ValueError as exc:
        raise _invalid() from exc
    return value


@dataclass(frozen=True, slots=True, repr=False)
class StartIdentityInput:
    task_title: str = field(repr=False)
    workspace_ref: str | None = field(default=None, repr=False)
    external_ref: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        title = _optional_identity(self.task_title)
        if title is None:
            raise _invalid()
        workspace = _optional_identity(self.workspace_ref)
        external = _optional_identity(self.external_ref)
        if (workspace is None) != (external is None):
            raise _invalid()

    def __repr__(self) -> str:
        return "StartIdentityInput(<redacted>)"


@dataclass(frozen=True, slots=True)
class StartIdentityCommitments:
    title_commitment: str
    workspace_ref_commitment: str | None
    external_ref_commitment: str | None

    def __post_init__(self) -> None:
        try:
            validate_commitment(self.title_commitment)
            if self.workspace_ref_commitment is not None:
                validate_commitment(self.workspace_ref_commitment)
            if self.external_ref_commitment is not None:
                validate_commitment(self.external_ref_commitment)
        except ValueError as exc:
            raise _invalid() from exc
        if (self.workspace_ref_commitment is None) != (self.external_ref_commitment is None):
            raise _invalid()


@dataclass(frozen=True, slots=True)
class StartCommand:
    operation_id: str
    request_digest: str
    mode: StartMode
    identity_input: StartIdentityInput
    identity_commitments: StartIdentityCommitments
    session_id: str | None = None

    def __post_init__(self) -> None:
        _id(IdKind.REQUEST, self.operation_id)
        try:
            validate_sha256_digest(self.request_digest)
        except ValueError as exc:
            raise _invalid() from exc
        if (
            type(self.mode) is not StartMode
            or type(self.identity_input) is not StartIdentityInput
            or type(self.identity_commitments) is not StartIdentityCommitments
        ):
            raise _invalid()
        if self.session_id is not None:
            _id(IdKind.SESSION, self.session_id)
        input_has_refs = self.identity_input.workspace_ref is not None
        commitments_have_refs = self.identity_commitments.workspace_ref_commitment is not None
        if input_has_refs != commitments_have_refs:
            raise _invalid()
        if self.mode is StartMode.ATTACH and not input_has_refs and self.session_id is None:
            raise _invalid()


@dataclass(frozen=True, slots=True)
class TaskRoute:
    task_id: str
    session_id: str
    bundle_relpath: str
    route_generation: int
    state: TaskRouteState
    route_identity_digest: str

    def __post_init__(self) -> None:
        task = _id(IdKind.TASK, self.task_id)
        _id(IdKind.SESSION, self.session_id)
        if type(self.bundle_relpath) is not str or self.bundle_relpath != f"tasks/{task}":
            raise _invalid()
        if (
            type(self.route_generation) is not int
            or not 1 <= self.route_generation <= _MAX_SAFE_INTEGER
        ):
            raise _invalid()
        if type(self.state) is not TaskRouteState:
            raise _invalid()
        expected = canonical_digest(
            {
                "task_id": task,
                "bundle_relpath": self.bundle_relpath,
                "route_generation": self.route_generation,
            }
        )
        if self.route_identity_digest != expected:
            raise _invalid()


@dataclass(frozen=True, slots=True)
class StartOperationLease:
    """Catalog-start lease; distinct from the check-specific ledger lease."""

    owner_generation: int
    lease_owner_id: str
    lease_generation: int
    lease_expires_at: datetime

    def __post_init__(self) -> None:
        if (
            type(self.owner_generation) is not int
            or not 1 <= self.owner_generation <= _MAX_SAFE_INTEGER
        ):
            raise _invalid()
        _safe_token(self.lease_owner_id)
        if (
            type(self.lease_generation) is not int
            or not 1 <= self.lease_generation <= _MAX_SAFE_INTEGER
        ):
            raise _invalid()
        try:
            format_rfc3339_millis(self.lease_expires_at)
        except ValueError as exc:
            raise _invalid() from exc


@dataclass(frozen=True, slots=True)
class StartAllocation:
    outcome: Literal["reserved", "resumed", "replayed"]
    route_action: Literal["created", "attached"]
    task_id: str
    session_id: str
    writer_id: str
    lifecycle_event_id: str
    bundle_relpath: str
    route_generation: int
    route_identity_digest: str
    phase: StartPhase
    response_object_id: str | None
    lease: StartOperationLease | None
    replayed_result: bytes | None

    def __post_init__(self) -> None:
        if self.outcome not in {"reserved", "resumed", "replayed"}:
            raise _invalid()
        if self.route_action not in {"created", "attached"}:
            raise _invalid()
        task = _id(IdKind.TASK, self.task_id)
        _id(IdKind.SESSION, self.session_id)
        _id(IdKind.WRITER, self.writer_id)
        _id(IdKind.EVENT, self.lifecycle_event_id)
        if type(self.bundle_relpath) is not str or self.bundle_relpath != f"tasks/{task}":
            raise _invalid()
        if (
            type(self.route_generation) is not int
            or not 1 <= self.route_generation <= _MAX_SAFE_INTEGER
        ):
            raise _invalid()
        try:
            validate_sha256_digest(self.route_identity_digest)
        except ValueError as exc:
            raise _invalid() from exc
        expected = canonical_digest(
            {
                "task_id": task,
                "bundle_relpath": self.bundle_relpath,
                "route_generation": self.route_generation,
            }
        )
        if self.route_identity_digest != expected or type(self.phase) is not StartPhase:
            raise _invalid()
        if self.response_object_id is not None:
            _id(IdKind.OBJECT, self.response_object_id)
        if self.outcome == "replayed":
            if (
                self.phase is not StartPhase.TERMINAL
                or self.lease is not None
                or type(self.replayed_result) is not bytes
            ):
                raise _invalid()
        elif (
            self.phase is StartPhase.TERMINAL
            or type(self.lease) is not StartOperationLease
            or self.replayed_result is not None
        ):
            raise _invalid()
        if self.phase is StartPhase.RESULT_PUBLISHED:
            if self.response_object_id is None:
                raise _invalid()
        elif self.outcome != "replayed" and self.response_object_id is not None:
            raise _invalid()


@dataclass(frozen=True, slots=True)
class EncryptedResultRef:
    response_object_id: str
    result_canonical: bytes
    result_digest: str

    def __post_init__(self) -> None:
        _id(IdKind.OBJECT, self.response_object_id)
        if type(self.result_canonical) is not bytes:
            raise _invalid()
        expected = f"sha256:{hashlib.sha256(self.result_canonical).hexdigest()}"
        if self.result_digest != expected:
            raise _invalid()


@dataclass(frozen=True, slots=True)
class SafeReason:
    code: str

    def __post_init__(self) -> None:
        if type(self.code) is not str or self.code not in _START_QUARANTINE_CODES:
            raise _invalid()


class StartCatalogPort(Protocol):
    async def commit_identity(self, value: StartIdentityInput) -> StartIdentityCommitments: ...

    async def resolve_route(self, session_id: str) -> TaskRoute | None: ...

    async def reserve_or_resume(self, request: StartCommand) -> StartAllocation: ...

    async def complete(
        self,
        allocation: StartAllocation,
        result: EncryptedResultRef,
        evidence: StartCompletionEvidence,
    ) -> None: ...

    async def quarantine(self, allocation: StartAllocation, reason: SafeReason) -> None: ...

    async def advance_phase(
        self,
        allocation: StartAllocation,
        phase: StartPhase,
        result: EncryptedResultRef | None = None,
    ) -> StartAllocation: ...
