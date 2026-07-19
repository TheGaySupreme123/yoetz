"""Ordinary local-service control client boundary."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Literal, Protocol

from yoetz.domain.values import JsonObject
from yoetz.protocol.canonical import parse_canonical_integer_string
from yoetz.protocol.ids import IdKind, validate_id
from yoetz.protocol.models import (
    CheckRequest,
    CheckResult,
    PublishWorkRequest,
    PublishWorkResult,
    ReceiptRequest,
    ReceiptResult,
    RespondRequest,
    RespondResult,
    StartRequest,
    StartResult,
    StatusRequest,
    StatusResult,
)

__all__ = [
    "ControlCallRequest",
    "ControlCancelRequest",
    "ControlClientKind",
    "ControlClientPort",
    "ControlError",
    "ControlMethod",
    "ControlRequest",
    "ControlResult",
    "ServiceState",
    "ServiceStopResult",
    "ServiceStatus",
]

_CONTROL_PROTOCOL_VERSION = "1.0"
_MAX_DEADLINE_MS = 300_000
_SEMVER_PATTERN = re.compile(
    r"^(?:0|[1-9][0-9]*)\."
    r"(?:0|[1-9][0-9]*)\."
    r"(?:0|[1-9][0-9]*)"
    r"(?:-(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$",
    re.ASCII,
)


class ControlClientKind(str, Enum):  # noqa: UP042 - exact wire enum base
    CLI = "cli"
    MCP_BRIDGE = "mcp_bridge"
    UI = "ui"


class ControlMethod(str, Enum):  # noqa: UP042 - exact wire enum base
    START = "start"
    PUBLISH_WORK = "publish_work"
    CHECK = "check"
    RESPOND = "respond"
    STATUS = "status"
    RECEIPT = "receipt"
    IMPORT_CODEX_JSONL = "import_codex_jsonl"
    REVIEW = "review"
    BACKUP_PREVIEW = "backup_preview"
    BACKUP_EXECUTE = "backup_execute"
    RESTORE_PREVIEW = "restore_preview"
    RESTORE_EXECUTE = "restore_execute"
    MIGRATE_PREVIEW = "migrate_preview"
    MIGRATE_EXECUTE = "migrate_execute"
    INTEGRATION_PREVIEW = "integration_preview"
    INTEGRATION_EXECUTE = "integration_execute"
    SERVICE_STATUS = "service_status"
    SERVICE_LOCK = "service_lock"
    SERVICE_STOP = "service_stop"
    PRIVACY_GET_SETUP = "privacy_get_setup"
    PRIVACY_GET_EFFECTIVE = "privacy_get_effective"
    PRIVACY_PROPOSE_POLICY = "privacy_propose_policy"
    PRIVACY_TIGHTEN_POLICY = "privacy_tighten_policy"
    PRIVACY_RECEIPTS_LIST = "privacy_receipts_list"
    PRIVACY_RECEIPTS_GET = "privacy_receipts_get"


class ServiceState(str, Enum):  # noqa: UP042 - exact wire enum base
    STARTING = "starting"
    LOCKED = "locked"
    UNLOCKING = "unlocking"
    READY = "ready"
    DRAINING = "draining"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ServiceStopResult:
    schema_version: Literal["1.0.0"] = "1.0.0"
    state: Literal["draining"] = "draining"
    accepted: Literal[True] = True

    def __post_init__(self) -> None:
        if self.schema_version != "1.0.0":
            raise ValueError("service_stop_schema_version_invalid")
        if self.state != "draining":
            raise ValueError("service_stop_state_invalid")
        if self.accepted is not True:
            raise ValueError("service_stop_acceptance_invalid")


type ControlCallBody = (
    StartRequest
    | PublishWorkRequest
    | CheckRequest
    | RespondRequest
    | StatusRequest
    | ReceiptRequest
    | JsonObject
)
type ControlSuccessBody = (
    StartResult
    | PublishWorkResult
    | CheckResult
    | RespondResult
    | StatusResult
    | ReceiptResult
    | JsonObject
    | ServiceStatus
    | ServiceStopResult
)

_WORKFLOW_REQUEST_TYPES: dict[ControlMethod, type[object]] = {
    ControlMethod.START: StartRequest,
    ControlMethod.PUBLISH_WORK: PublishWorkRequest,
    ControlMethod.CHECK: CheckRequest,
    ControlMethod.RESPOND: RespondRequest,
    ControlMethod.STATUS: StatusRequest,
    ControlMethod.RECEIPT: ReceiptRequest,
}
_WORKFLOW_RESULT_TYPES: dict[ControlMethod, type[object]] = {
    ControlMethod.START: StartResult,
    ControlMethod.PUBLISH_WORK: PublishWorkResult,
    ControlMethod.CHECK: CheckResult,
    ControlMethod.RESPOND: RespondResult,
    ControlMethod.STATUS: StatusResult,
    ControlMethod.RECEIPT: ReceiptResult,
}


def _validate_protocol_version(value: object) -> None:
    if type(value) is not str or value != _CONTROL_PROTOCOL_VERSION:
        raise ValueError("control_protocol_version_invalid")


def _validate_generation(value: object) -> None:
    if type(value) is not str:
        raise ValueError("service_generation_invalid")
    try:
        parsed = parse_canonical_integer_string(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("service_generation_invalid") from exc
    if parsed <= 0:
        raise ValueError("service_generation_invalid")


def _validate_deadline(value: object) -> None:
    if value is None:
        return
    if type(value) is not int or not 1 <= value <= _MAX_DEADLINE_MS:
        raise ValueError("deadline_ms_invalid")


@dataclass(frozen=True, slots=True)
class ControlCallRequest:
    kind: Literal["call"]
    protocol_version: Literal["1.0"]
    rpc_id: str
    service_instance_id: str
    service_generation: str
    method: ControlMethod
    body: ControlCallBody
    deadline_ms: int | None = None

    def __post_init__(self) -> None:
        if self.kind != "call":
            raise ValueError("control_request_kind_invalid")
        _validate_protocol_version(self.protocol_version)
        validate_id(IdKind.CONTROL_RPC, self.rpc_id)
        validate_id(IdKind.SERVICE_INSTANCE, self.service_instance_id)
        _validate_generation(self.service_generation)
        if type(self.method) is not ControlMethod:
            raise ValueError("control_method_invalid")
        expected_body_type = _WORKFLOW_REQUEST_TYPES.get(self.method, JsonObject)
        if type(self.body) is not expected_body_type:
            raise ValueError("control_method_body_mismatch")
        _validate_deadline(self.deadline_ms)


@dataclass(frozen=True, slots=True)
class ControlCancelRequest:
    kind: Literal["cancel"]
    protocol_version: Literal["1.0"]
    rpc_id: str
    service_instance_id: str
    service_generation: str
    target_rpc_id: str

    def __post_init__(self) -> None:
        if self.kind != "cancel":
            raise ValueError("control_request_kind_invalid")
        _validate_protocol_version(self.protocol_version)
        validate_id(IdKind.CONTROL_RPC, self.rpc_id)
        validate_id(IdKind.SERVICE_INSTANCE, self.service_instance_id)
        _validate_generation(self.service_generation)
        validate_id(IdKind.CONTROL_RPC, self.target_rpc_id)
        if self.rpc_id == self.target_rpc_id:
            raise ValueError("target_rpc_id_not_distinct")


type ControlRequest = ControlCallRequest | ControlCancelRequest


_CONTROL_ERROR_REASONS = frozenset(
    {
        "service_unavailable",
        "peer_untrusted",
        "protocol_mismatch",
        "frame_invalid",
        "frame_too_large",
        "request_cancelled",
        "request_timeout",
        "vault_locked",
        "service_draining",
        "method_forbidden",
        "internal_error",
        "privacy_projection_unavailable",
        "service_generation_changed",
    }
)


class ControlError(Exception):
    """A bounded control failure with no free-form message or details."""

    __slots__ = ("reason", "retryable")

    reason: str
    retryable: bool

    def __init__(self, reason: str, *, retryable: bool = False) -> None:
        if type(reason) is not str or reason not in _CONTROL_ERROR_REASONS:
            raise TypeError("control_error_reason_invalid")
        if type(retryable) is not bool:
            raise TypeError("control_error_retryable_invalid")
        if reason == "privacy_projection_unavailable" and not retryable:
            raise ValueError("privacy_projection_error_must_be_retryable")
        self.reason = reason
        self.retryable = retryable
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class ControlResult:
    protocol_version: Literal["1.0"]
    rpc_id: str
    service_instance_id: str
    service_generation: str
    method: ControlMethod
    outcome: Literal["ok", "error"]
    body: ControlSuccessBody | ControlError

    def __post_init__(self) -> None:
        _validate_protocol_version(self.protocol_version)
        validate_id(IdKind.CONTROL_RPC, self.rpc_id)
        validate_id(IdKind.SERVICE_INSTANCE, self.service_instance_id)
        _validate_generation(self.service_generation)
        if type(self.method) is not ControlMethod:
            raise ValueError("control_method_invalid")
        if self.outcome not in {"ok", "error"}:
            raise ValueError("control_outcome_invalid")
        if (self.outcome == "error") is not isinstance(self.body, ControlError):
            raise ValueError("control_outcome_body_mismatch")
        if self.outcome == "ok":
            expected_body_type = _WORKFLOW_RESULT_TYPES.get(self.method, JsonObject)
            if self.method in {ControlMethod.SERVICE_STATUS, ControlMethod.SERVICE_LOCK}:
                expected_body_type = ServiceStatus
            elif self.method is ControlMethod.SERVICE_STOP:
                expected_body_type = ServiceStopResult
            if type(self.body) is not expected_body_type:
                raise ValueError("control_method_body_mismatch")


_SERVICE_STATE_REASONS = frozenset(
    {
        "none",
        "keyring_locked",
        "keyring_unavailable",
        "human_authority_unavailable",
        "vault_uninitialized",
        "unlock_failed",
        "explicit_lock",
        "idle_relock",
        "user_session_locked",
        "system_suspend",
        "monitor_lost",
        "shutdown_requested",
        "internal_error",
    }
)
_VAULT_MODES = frozenset({"uninitialized", "os_keyring", "passphrase"})
_SERVICE_CAPABILITIES = frozenset(
    {
        "workflow",
        "maintenance",
        "import_review",
        "external_provider",
        "confidential_ingress",
        "session_event_monitor",
    }
)
_SESSION_MONITOR_STATES = frozenset({"active", "unavailable", "lost"})


@dataclass(frozen=True, slots=True)
class ServiceStatus:
    protocol_version: Literal["1.0"]
    service_version: str
    service_instance_id: str
    service_generation: str
    state: ServiceState
    state_reason: str
    vault_mode: str
    capabilities: tuple[str, ...]
    session_monitor: str
    idle_relock_seconds: int | None = None

    def __post_init__(self) -> None:
        _validate_protocol_version(self.protocol_version)
        if type(self.service_version) is not str or not 5 <= len(self.service_version) <= 128:
            raise ValueError("service_version_invalid")
        if _SEMVER_PATTERN.fullmatch(self.service_version) is None:
            raise ValueError("service_version_invalid")
        validate_id(IdKind.SERVICE_INSTANCE, self.service_instance_id)
        _validate_generation(self.service_generation)
        if type(self.state) is not ServiceState:
            raise ValueError("service_state_invalid")
        if self.state_reason not in _SERVICE_STATE_REASONS:
            raise ValueError("service_state_reason_invalid")
        if self.vault_mode not in _VAULT_MODES:
            raise ValueError("vault_mode_invalid")
        if type(self.capabilities) is not tuple:
            raise ValueError("service_capabilities_invalid")
        if any(type(capability) is not str for capability in self.capabilities):
            raise ValueError("service_capabilities_invalid")
        encoded = tuple(capability.encode("ascii") for capability in self.capabilities)
        if not set(self.capabilities) <= _SERVICE_CAPABILITIES or encoded != tuple(
            sorted(set(encoded))
        ):
            raise ValueError("service_capabilities_invalid")
        if self.session_monitor not in _SESSION_MONITOR_STATES:
            raise ValueError("session_monitor_invalid")
        if self.idle_relock_seconds is not None and (
            type(self.idle_relock_seconds) is not int
            or not 60 <= self.idle_relock_seconds <= 86_400
        ):
            raise ValueError("idle_relock_seconds_invalid")


class ControlClientPort(Protocol):
    async def connect(self) -> None: ...

    async def call(self, request: ControlCallRequest) -> ControlResult: ...

    async def cancel(self, request: ControlCancelRequest) -> None: ...

    async def service_status(self) -> ServiceStatus: ...

    async def lock(self) -> ServiceStatus: ...

    async def stop(self) -> ServiceStopResult: ...

    async def close(self) -> None: ...
