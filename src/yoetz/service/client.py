"""Bounded ordinary client for the authenticated per-user Yoetz service."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final, Literal, TypedDict, cast

from yoetz import __version__
from yoetz.adapters.control.unix_socket import (
    AuthenticatedUnixStream,
    LocalControlTransportError,
    connect_control,
)
from yoetz.domain.privacy import (
    AuthorizationScope,
    AuthorizationScopeKind,
    ConsentSource,
    EgressChannel,
    EgressReceipt,
    LocalDisclosureReceipt,
    LocalDisclosureSink,
    NonLlmDestination,
    PrivacyOutcome,
    PrivacyReason,
    ProviderBinding,
    ReceiptCounts,
    ReceiptPolicyBinding,
    ReceiptSecretScan,
    ReceiptTransformations,
    RequestCommitment,
)
from yoetz.domain.values import (
    JsonObject,
    JsonValue,
    format_rfc3339_millis,
    parse_rfc3339_millis,
)
from yoetz.ports.control import (
    ControlCallBody,
    ControlCallRequest,
    ControlCancelRequest,
    ControlClientKind,
    ControlClientPort,
    ControlError,
    ControlMethod,
    ControlResult,
    ServiceStatus,
    ServiceStopResult,
)
from yoetz.ports.privacy import (
    LocalDisclosureReceiptView,
    NetworkEgressReceiptView,
    PrivacyReceiptPage,
    PrivacyReceiptView,
)
from yoetz.protocol.canonical import parse_canonical_integer_string
from yoetz.protocol.ids import IdKind, new_id, validate_id
from yoetz.protocol.models import (
    CheckRequest,
    CheckResult,
    DataCategory,
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
from yoetz.service.control_protocol import (
    CONTROL_PROTOCOL_VERSION,
    ControlProtocolError,
    ControlSession,
    client_handshake,
    parse_control_result,
    read_control_frame,
    validate_request,
    validate_result,
    write_control_frame,
)

__all__ = [
    "GetPrivacyReceiptRequest",
    "ListPrivacyReceiptsRequest",
    "PrivacyReceiptFilters",
    "PrivacyReceiptFound",
    "PrivacyReceiptGetResult",
    "PrivacyReceiptNotFound",
    "ServiceClient",
    "connect_service",
    "connect_service_on_demand",
]

_MAX_IN_FLIGHT: Final = 32
_WORKFLOW_METHODS: Final = frozenset(
    {
        ControlMethod.START,
        ControlMethod.PUBLISH_WORK,
        ControlMethod.CHECK,
        ControlMethod.RESPOND,
        ControlMethod.STATUS,
        ControlMethod.RECEIPT,
    }
)
_LIFECYCLE_METHODS: Final = frozenset(
    {ControlMethod.SERVICE_STATUS, ControlMethod.SERVICE_LOCK, ControlMethod.SERVICE_STOP}
)
_PRIVATE_CONSTRUCTOR_TOKEN: Final = object()
_SERVICE_START_TIMEOUT_SECONDS: Final = 10.0
_SERVICE_START_POLL_SECONDS: Final = 0.05
_SECRET_ENV_MARKERS: Final = (
    "API_KEY",
    "AUTHORIZATION",
    "CREDENTIAL",
    "PASSWORD",
    "SECRET",
    "TOKEN",
)


def _service_environment() -> dict[str, str]:
    """Return inherited nonsecret process context for the detached service.

    The service never consumes provider credentials from ambient environment state.  Filtering
    secret-shaped names here also prevents an unrelated harness/API credential inherited by the
    MCP bridge from becoming visible in the service process metadata.
    """

    return {
        name: value
        for name, value in os.environ.items()
        if not any(marker in name.upper() for marker in _SECRET_ENV_MARKERS)
    }


def _spawn_service_process() -> None:
    command = (sys.executable, "-m", "yoetz", "service", "run")
    if os.name == "nt":
        creationflags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) | int(
            getattr(subprocess, "DETACHED_PROCESS", 0),
        )
        subprocess.Popen(  # noqa: S603 - fixed interpreter/module/arguments
            command,
            close_fds=True,
            creationflags=creationflags,
            env=_service_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        subprocess.Popen(  # noqa: S603 - fixed interpreter/module/arguments
            command,
            close_fds=True,
            env=_service_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )


@dataclass(frozen=True, slots=True)
class PrivacyReceiptFilters:
    outcome: PrivacyOutcome | None = None
    channel: EgressChannel | None = None
    sink: LocalDisclosureSink | None = None
    provider_id: str | None = None
    endpoint_profile_id: str | None = None
    policy_version: int | None = None
    scope_kind: AuthorizationScopeKind | None = None
    finished_from: datetime | None = None
    finished_through: datetime | None = None

    def __post_init__(self) -> None:
        for value, expected in (
            (self.outcome, PrivacyOutcome),
            (self.channel, EgressChannel),
            (self.sink, LocalDisclosureSink),
            (self.scope_kind, AuthorizationScopeKind),
        ):
            if value is not None and type(value) is not expected:
                raise TypeError("privacy_receipt_filter_invalid")
        for value in (self.provider_id, self.endpoint_profile_id):
            if value is not None and (type(value) is not str or not value):
                raise ValueError("privacy_receipt_filter_invalid")
        if self.policy_version is not None and (
            type(self.policy_version) is not int or self.policy_version <= 0
        ):
            raise ValueError("privacy_receipt_filter_invalid")
        if self.finished_from is not None:
            format_rfc3339_millis(self.finished_from)
        if self.finished_through is not None:
            format_rfc3339_millis(self.finished_through)
        if (
            self.finished_from is not None
            and self.finished_through is not None
            and self.finished_from > self.finished_through
        ):
            raise ValueError("privacy_receipt_filter_invalid")

    def _wire(self) -> JsonObject:
        values: dict[str, JsonValue] = {}
        for name in ("outcome", "channel", "sink", "scope_kind"):
            value = getattr(self, name)
            if value is not None:
                values[name] = value.value
        for name in ("provider_id", "endpoint_profile_id"):
            value = getattr(self, name)
            if value is not None:
                values[name] = value
        if self.policy_version is not None:
            values["policy_version"] = str(self.policy_version)
        if self.finished_from is not None:
            values["finished_from"] = format_rfc3339_millis(self.finished_from)
        if self.finished_through is not None:
            values["finished_through"] = format_rfc3339_millis(self.finished_through)
        return JsonObject(values)


@dataclass(frozen=True, slots=True)
class ListPrivacyReceiptsRequest:
    filters: PrivacyReceiptFilters = field(default_factory=PrivacyReceiptFilters)
    page_size: int = 50
    cursor: str | None = None
    schema_version: Literal["1.0.0"] = "1.0.0"

    def __post_init__(self) -> None:
        if type(self.filters) is not PrivacyReceiptFilters:
            raise TypeError("privacy_receipt_filters_invalid")
        if type(self.page_size) is not int or not 1 <= self.page_size <= 100:
            raise ValueError("privacy_receipt_page_size_invalid")
        if self.cursor is not None and (
            type(self.cursor) is not str or not 1 <= len(self.cursor) <= 1_024
        ):
            raise ValueError("privacy_receipt_cursor_invalid")
        if self.schema_version != "1.0.0":
            raise ValueError("privacy_receipt_schema_version_invalid")

    def _wire(self) -> JsonObject:
        values: dict[str, JsonValue] = {
            "schema_version": self.schema_version,
            "filters": self.filters._wire(),  # pyright: ignore[reportPrivateUsage]
            "page_size": self.page_size,
        }
        if self.cursor is not None:
            values["cursor"] = self.cursor
        return JsonObject(values)


@dataclass(frozen=True, slots=True)
class GetPrivacyReceiptRequest:
    receipt_id: str
    schema_version: Literal["1.0.0"] = "1.0.0"

    def __post_init__(self) -> None:
        validate_id(IdKind.EGRESS_RECEIPT, self.receipt_id)
        if self.schema_version != "1.0.0":
            raise ValueError("privacy_receipt_schema_version_invalid")

    def _wire(self) -> JsonObject:
        return JsonObject({"schema_version": self.schema_version, "receipt_id": self.receipt_id})


@dataclass(frozen=True, slots=True)
class PrivacyReceiptFound:
    receipt: PrivacyReceiptView
    schema_version: Literal["1.0.0"] = "1.0.0"
    outcome: Literal["found"] = "found"

    def __post_init__(self) -> None:
        if self.schema_version != "1.0.0" or self.outcome != "found":
            raise ValueError("privacy_receipt_get_result_invalid")
        if type(self.receipt) not in {
            NetworkEgressReceiptView,
            LocalDisclosureReceiptView,
        }:
            raise TypeError("privacy_receipt_get_result_invalid")


@dataclass(frozen=True, slots=True)
class PrivacyReceiptNotFound:
    schema_version: Literal["1.0.0"] = "1.0.0"
    outcome: Literal["not_found"] = "not_found"

    def __post_init__(self) -> None:
        if self.schema_version != "1.0.0" or self.outcome != "not_found":
            raise ValueError("privacy_receipt_get_result_invalid")


type PrivacyReceiptGetResult = PrivacyReceiptFound | PrivacyReceiptNotFound


class _ReceiptCommon(TypedDict):
    schema_version: Literal["1.0.0"]
    receipt_id: str
    request_id: str
    privacy_proposal_id: str
    outcome: PrivacyOutcome
    finished_at: datetime
    scope: AuthorizationScope
    purpose: str
    policy: ReceiptPolicyBinding
    consent_source: ConsentSource
    approved_categories: tuple[DataCategory, ...]
    blocked_categories: tuple[DataCategory, ...]
    counts: ReceiptCounts
    transformations: ReceiptTransformations
    secret_scan: ReceiptSecretScan
    safe_failure_reason: PrivacyReason | None
    audit_store_version: Literal[1]


def _transport_error(error: LocalControlTransportError) -> ControlError:
    if error.reason == "peer_untrusted":
        return ControlError("peer_untrusted")
    return ControlError("service_unavailable", retryable=True)


def _protocol_error(error: ControlProtocolError) -> ControlError:
    if error.reason == "protocol_mismatch" or error.reason == "manifest_mismatch":
        return ControlError("protocol_mismatch")
    if error.reason == "peer_untrusted":
        return ControlError("peer_untrusted")
    if error.reason == "frame_too_large":
        return ControlError("frame_too_large")
    if error.reason == "method_forbidden":
        return ControlError("method_forbidden")
    if error.reason in {"frame_invalid", "correlation_mismatch", "duplicate_rpc_id"}:
        return ControlError("frame_invalid")
    return ControlError("service_unavailable", retryable=True)


def _object(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("privacy_receipt_wire_invalid")
    return cast(Mapping[str, object], value)


def _decimal(value: object) -> int:
    return parse_canonical_integer_string(cast(str, value))


def _scope_from_wire(value: object) -> AuthorizationScope:
    source = _object(value)
    kind = AuthorizationScopeKind(cast(str, source["kind"]))
    return AuthorizationScope(
        kind=kind,
        installation_id=cast(str, source["installation_id"]),
        workspace_ref_commitment=cast(str | None, source.get("workspace_ref_commitment")),
        task_id=cast(str | None, source.get("task_id")),
        request_id=cast(str | None, source.get("request_id")),
    )


def _policy_from_wire(value: object) -> ReceiptPolicyBinding:
    source = _object(value)
    return ReceiptPolicyBinding(
        policy_id=cast(str, source["policy_id"]),
        version=_decimal(source["version"]),
        policy_digest=cast(str, source["policy_digest"]),
        authorization_scope_digest=cast(str, source["authorization_scope_digest"]),
    )


def _counts_from_wire(value: object) -> ReceiptCounts:
    source = _object(value)
    return ReceiptCounts(
        candidate_items=_decimal(source["candidate_items"]),
        included_items=_decimal(source["included_items"]),
        removed_items=_decimal(source["removed_items"]),
        approved_items=_decimal(source["approved_items"]),
        blocked_items=_decimal(source["blocked_items"]),
        candidate_bytes=_decimal(source["candidate_bytes"]),
        final_bytes=_decimal(source["final_bytes"]),
        estimated_input_tokens=(
            None
            if "estimated_input_tokens" not in source
            else _decimal(source["estimated_input_tokens"])
        ),
        request_body_bytes=(
            None if "request_body_bytes" not in source else _decimal(source["request_body_bytes"])
        ),
    )


def _transformations_from_wire(value: object) -> ReceiptTransformations:
    source = _object(value)
    return ReceiptTransformations(
        minimized_items=_decimal(source["minimized_items"]),
        redacted_spans=_decimal(source["redacted_spans"]),
        blocked_items=_decimal(source["blocked_items"]),
    )


def _secret_scan_from_wire(value: object) -> ReceiptSecretScan:
    source = _object(value)
    return ReceiptSecretScan(
        registry_version=cast(str, source["registry_version"]),
        scanner_profile_digest=cast(str, source["scanner_profile_digest"]),
        match_count=_decimal(source["match_count"]),
        passed=cast(bool, source["passed"]),
    )


def _categories_from_wire(value: object) -> tuple[DataCategory, ...]:
    if type(value) is not tuple:
        raise ValueError("privacy_receipt_wire_invalid")
    members = cast(tuple[object, ...], value)
    return tuple(DataCategory(cast(str, item)) for item in members)


def _destination_from_wire(value: object) -> ProviderBinding | NonLlmDestination:
    source = _object(value)
    if "provider_id" in source:
        return ProviderBinding(
            provider_id=cast(str, source["provider_id"]),
            model_id=cast(str, source["model_id"]),
            endpoint_profile_id=cast(str, source["endpoint_profile_id"]),
            endpoint_profile_version=cast(str, source["endpoint_profile_version"]),
            transport=cast(Literal["external", "local_af_unix"], source["transport"]),
        )
    return NonLlmDestination(
        kind=EgressChannel(cast(str, source["kind"])),
        profile_id=cast(str, source["profile_id"]),
        profile_version=cast(str, source["profile_version"]),
    )


def _request_commitment_from_wire(value: object) -> RequestCommitment:
    source = _object(value)
    return RequestCommitment(
        algorithm=cast(
            Literal["hmac-sha256/yoetz-privacy-egress-request-v1"],
            source["algorithm"],
        ),
        commitment=cast(str, source["commitment"]),
    )


def _receipt_view_from_wire(value: object) -> PrivacyReceiptView:
    wrapper = _object(value)
    receipt = _object(wrapper["receipt"])
    common: _ReceiptCommon = {
        "schema_version": cast(Literal["1.0.0"], receipt["schema_version"]),
        "receipt_id": cast(str, receipt["receipt_id"]),
        "request_id": cast(str, receipt["request_id"]),
        "privacy_proposal_id": cast(str, receipt["privacy_proposal_id"]),
        "outcome": PrivacyOutcome(cast(str, receipt["outcome"])),
        "finished_at": parse_rfc3339_millis(receipt["finished_at"]),
        "scope": _scope_from_wire(receipt["scope"]),
        "purpose": cast(str, receipt["purpose"]),
        "policy": _policy_from_wire(receipt["policy"]),
        "consent_source": ConsentSource(cast(str, receipt["consent_source"])),
        "approved_categories": _categories_from_wire(receipt["approved_categories"]),
        "blocked_categories": _categories_from_wire(receipt["blocked_categories"]),
        "counts": _counts_from_wire(receipt["counts"]),
        "transformations": _transformations_from_wire(receipt["transformations"]),
        "secret_scan": _secret_scan_from_wire(receipt["secret_scan"]),
        "safe_failure_reason": (
            None
            if "safe_failure_reason" not in receipt
            else PrivacyReason(cast(str, receipt["safe_failure_reason"]))
        ),
        "audit_store_version": cast(Literal[1], receipt["audit_store_version"]),
    }
    if wrapper["kind"] == "network_egress":
        network_receipt = EgressReceipt(
            **common,
            channel=EgressChannel(cast(str, receipt["channel"])),
            destination=_destination_from_wire(receipt["destination"]),
            authorization_id=cast(str | None, receipt.get("authorization_id")),
            dispatch_id=cast(str | None, receipt.get("dispatch_id")),
            dispatch_started_at=(
                None
                if "dispatch_started_at" not in receipt
                else parse_rfc3339_millis(receipt["dispatch_started_at"])
            ),
            request_commitment=(
                None
                if "request_commitment" not in receipt
                else _request_commitment_from_wire(receipt["request_commitment"])
            ),
        )
        return NetworkEgressReceiptView(kind="network_egress", receipt=network_receipt)
    local_receipt = LocalDisclosureReceipt(
        **common,
        sink=LocalDisclosureSink(cast(str, receipt["sink"])),
    )
    return LocalDisclosureReceiptView(kind="local_disclosure", receipt=local_receipt)


def _receipt_page_from_wire(value: JsonObject) -> PrivacyReceiptPage:
    source = _object(value)
    raw_receipts = source["receipts"]
    if type(raw_receipts) is not tuple:
        raise ValueError("privacy_receipt_wire_invalid")
    receipt_members = cast(tuple[object, ...], raw_receipts)
    return PrivacyReceiptPage(
        snapshot_generation=_decimal(source["snapshot_generation"]),
        receipts=tuple(_receipt_view_from_wire(item) for item in receipt_members),
        next_cursor=cast(str | None, source.get("next_cursor")),
    )


def _receipt_get_from_wire(value: JsonObject) -> PrivacyReceiptGetResult:
    source = _object(value)
    if source["outcome"] == "not_found":
        return PrivacyReceiptNotFound(
            schema_version=cast(Literal["1.0.0"], source["schema_version"])
        )
    return PrivacyReceiptFound(
        schema_version=cast(Literal["1.0.0"], source["schema_version"]),
        receipt=_receipt_view_from_wire(source["receipt"]),
    )


class ServiceClient(ControlClientPort):
    """One authenticated ordinary-control session.

    Instances are created by :func:`connect_service`.  The package-private connected
    constructor exists solely so protocol tests can inject a bounded fake byte stream.
    """

    __slots__ = (
        "_client_kind",
        "_closed",
        "_pending",
        "_pid",
        "_receiver",
        "_session",
        "_stream",
        "_write_lock",
    )

    def __init__(
        self,
        stream: AuthenticatedUnixStream,
        session: ControlSession,
        client_kind: ControlClientKind,
        *,
        _token: object,
    ) -> None:
        if _token is not _PRIVATE_CONSTRUCTOR_TOKEN:
            raise TypeError("service_client_constructor_private")
        if type(client_kind) is not ControlClientKind:
            raise TypeError("control_client_kind_invalid")
        self._stream = stream
        self._session = session
        self._client_kind = client_kind
        self._pid = os.getpid()
        self._closed = False
        self._pending: dict[str, asyncio.Future[ControlResult]] = {}
        self._write_lock = asyncio.Lock()
        self._receiver = asyncio.create_task(self._receive_results())

    def _ensure_live(self) -> None:
        if os.getpid() != self._pid:
            self._closed = True
            raise ControlError("service_unavailable")
        if self._closed:
            raise ControlError("service_unavailable", retryable=True)

    def _admit(self, method: ControlMethod) -> None:
        if self._client_kind is ControlClientKind.MCP_BRIDGE and method not in _WORKFLOW_METHODS:
            raise ControlError("method_forbidden")
        if method in _LIFECYCLE_METHODS and self._client_kind is ControlClientKind.MCP_BRIDGE:
            raise ControlError("method_forbidden")

    async def connect(self) -> None:
        """Confirm that this already-handshaken session remains locally usable."""

        self._ensure_live()

    async def _send(self, request: ControlCallRequest | ControlCancelRequest) -> None:
        validate_request(request)
        async with self._write_lock:
            self._ensure_live()
            try:
                await write_control_frame(self._stream, request)
            except LocalControlTransportError as exc:
                await self._fail_connection(_transport_error(exc))
                raise _transport_error(exc) from exc
            except ControlProtocolError as exc:
                error = _protocol_error(exc)
                await self._fail_connection(error)
                raise error from exc

    async def _receive_results(self) -> None:
        failure = ControlError("service_unavailable", retryable=True)
        try:
            while not self._closed:
                frame = await read_control_frame(self._stream)
                result = parse_control_result(frame)
                validate_result(result)
                if (
                    result.service_instance_id != self._session.service_instance_id
                    or result.service_generation != self._session.service_generation
                ):
                    raise ControlError("service_unavailable", retryable=True)
                pending = self._pending.get(result.rpc_id)
                if pending is None or pending.done():
                    raise ControlError("frame_invalid")
                try:
                    self._session.correlate(result)
                except ControlProtocolError as exc:
                    raise ControlError("frame_invalid") from exc
                pending.set_result(result)
        except asyncio.CancelledError:
            return
        except ControlError as exc:
            failure = exc
        except ControlProtocolError as exc:
            failure = _protocol_error(exc)
        except Exception:
            failure = ControlError("service_unavailable", retryable=True)
        await self._fail_connection(failure, from_receiver=True)

    async def _fail_connection(self, error: ControlError, *, from_receiver: bool = False) -> None:
        if not self._closed:
            self._closed = True
            self._session.close()
            for pending in tuple(self._pending.values()):
                if not pending.done():
                    pending.set_exception(error)
            await self._stream.aclose()
        if not from_receiver and self._receiver is not asyncio.current_task():
            self._receiver.cancel()
            await asyncio.gather(self._receiver, return_exceptions=True)

    async def _request_cancel(self, target_rpc_id: str) -> None:
        if self._closed:
            return
        request = ControlCancelRequest(
            kind="cancel",
            protocol_version=CONTROL_PROTOCOL_VERSION,
            rpc_id=new_id(IdKind.CONTROL_RPC),
            service_instance_id=self._session.service_instance_id,
            service_generation=self._session.service_generation,
            target_rpc_id=target_rpc_id,
        )
        try:
            await asyncio.shield(self._send(request))
        except ControlError, asyncio.CancelledError:
            return

    async def call(self, request: ControlCallRequest) -> ControlResult:
        self._ensure_live()
        self._admit(request.method)
        if (
            request.service_instance_id != self._session.service_instance_id
            or request.service_generation != self._session.service_generation
        ):
            raise ControlError("service_unavailable", retryable=True)
        if request.rpc_id in self._pending or len(self._pending) >= _MAX_IN_FLIGHT:
            raise ControlError("service_unavailable", retryable=True)

        future = asyncio.get_running_loop().create_future()
        self._pending[request.rpc_id] = future
        try:
            try:
                self._session.admit(request)
            except ControlProtocolError as exc:
                raise ControlError("frame_invalid") from exc
            await self._send(request)
            if request.deadline_ms is None:
                result = await future
            else:
                try:
                    async with asyncio.timeout(request.deadline_ms / 1_000):
                        result = await future
                except TimeoutError as exc:
                    await self._request_cancel(request.rpc_id)
                    raise ControlError("request_timeout", retryable=True) from exc
        except asyncio.CancelledError:
            await self._request_cancel(request.rpc_id)
            raise
        finally:
            self._pending.pop(request.rpc_id, None)

        if result.outcome == "error" and isinstance(result.body, ControlError):
            if result.body.reason in {
                "service_generation_changed",
                "privacy_projection_unavailable",
            }:
                await self._fail_connection(ControlError("service_unavailable", retryable=True))
                return ControlResult(
                    protocol_version=result.protocol_version,
                    rpc_id=result.rpc_id,
                    service_instance_id=result.service_instance_id,
                    service_generation=result.service_generation,
                    method=result.method,
                    outcome="error",
                    body=ControlError("service_unavailable", retryable=True),
                )
        return result

    async def cancel(self, request: ControlCancelRequest) -> None:
        self._ensure_live()
        if (
            request.service_instance_id != self._session.service_instance_id
            or request.service_generation != self._session.service_generation
        ):
            raise ControlError("service_unavailable", retryable=True)
        try:
            self._session.admit(request)
        except ControlProtocolError as exc:
            raise ControlError("frame_invalid") from exc
        await self._send(request)

    async def _invoke(
        self,
        method: ControlMethod,
        body: ControlCallBody,
        *,
        deadline_ms: int | None = None,
    ) -> object:
        request = ControlCallRequest(
            kind="call",
            protocol_version=CONTROL_PROTOCOL_VERSION,
            rpc_id=new_id(IdKind.CONTROL_RPC),
            service_instance_id=self._session.service_instance_id,
            service_generation=self._session.service_generation,
            method=method,
            body=body,
            deadline_ms=deadline_ms,
        )
        result = await self.call(request)
        if result.outcome == "error":
            error = result.body
            if not isinstance(error, ControlError):
                raise ControlError("frame_invalid")
            raise error
        return result.body

    async def start(self, request: StartRequest, *, deadline_ms: int | None = None) -> StartResult:
        return cast(
            StartResult, await self._invoke(ControlMethod.START, request, deadline_ms=deadline_ms)
        )

    async def publish_work(
        self, request: PublishWorkRequest, *, deadline_ms: int | None = None
    ) -> PublishWorkResult:
        return cast(
            PublishWorkResult,
            await self._invoke(ControlMethod.PUBLISH_WORK, request, deadline_ms=deadline_ms),
        )

    async def check(self, request: CheckRequest, *, deadline_ms: int | None = None) -> CheckResult:
        return cast(
            CheckResult, await self._invoke(ControlMethod.CHECK, request, deadline_ms=deadline_ms)
        )

    async def respond(
        self, request: RespondRequest, *, deadline_ms: int | None = None
    ) -> RespondResult:
        return cast(
            RespondResult,
            await self._invoke(ControlMethod.RESPOND, request, deadline_ms=deadline_ms),
        )

    async def status(
        self, request: StatusRequest, *, deadline_ms: int | None = None
    ) -> StatusResult:
        return cast(
            StatusResult, await self._invoke(ControlMethod.STATUS, request, deadline_ms=deadline_ms)
        )

    async def receipt(
        self, request: ReceiptRequest, *, deadline_ms: int | None = None
    ) -> ReceiptResult:
        return cast(
            ReceiptResult,
            await self._invoke(ControlMethod.RECEIPT, request, deadline_ms=deadline_ms),
        )

    async def _support(
        self,
        method: ControlMethod,
        body: JsonObject,
        *,
        deadline_ms: int | None = None,
    ) -> JsonObject:
        return cast(JsonObject, await self._invoke(method, body, deadline_ms=deadline_ms))

    async def import_codex_jsonl(
        self, request: JsonObject, *, deadline_ms: int | None = None
    ) -> JsonObject:
        return await self._support(
            ControlMethod.IMPORT_CODEX_JSONL, request, deadline_ms=deadline_ms
        )

    async def review(self, request: JsonObject, *, deadline_ms: int | None = None) -> JsonObject:
        return await self._support(ControlMethod.REVIEW, request, deadline_ms=deadline_ms)

    async def backup_preview(
        self, request: JsonObject, *, deadline_ms: int | None = None
    ) -> JsonObject:
        return await self._support(ControlMethod.BACKUP_PREVIEW, request, deadline_ms=deadline_ms)

    async def backup_execute(
        self, request: JsonObject, *, deadline_ms: int | None = None
    ) -> JsonObject:
        return await self._support(ControlMethod.BACKUP_EXECUTE, request, deadline_ms=deadline_ms)

    async def restore_preview(
        self, request: JsonObject, *, deadline_ms: int | None = None
    ) -> JsonObject:
        return await self._support(ControlMethod.RESTORE_PREVIEW, request, deadline_ms=deadline_ms)

    async def restore_execute(
        self, request: JsonObject, *, deadline_ms: int | None = None
    ) -> JsonObject:
        return await self._support(ControlMethod.RESTORE_EXECUTE, request, deadline_ms=deadline_ms)

    async def migrate_preview(
        self, request: JsonObject, *, deadline_ms: int | None = None
    ) -> JsonObject:
        return await self._support(ControlMethod.MIGRATE_PREVIEW, request, deadline_ms=deadline_ms)

    async def migrate_execute(
        self, request: JsonObject, *, deadline_ms: int | None = None
    ) -> JsonObject:
        return await self._support(ControlMethod.MIGRATE_EXECUTE, request, deadline_ms=deadline_ms)

    async def integration_preview(
        self, request: JsonObject, *, deadline_ms: int | None = None
    ) -> JsonObject:
        return await self._support(
            ControlMethod.INTEGRATION_PREVIEW, request, deadline_ms=deadline_ms
        )

    async def integration_execute(
        self, request: JsonObject, *, deadline_ms: int | None = None
    ) -> JsonObject:
        return await self._support(
            ControlMethod.INTEGRATION_EXECUTE, request, deadline_ms=deadline_ms
        )

    async def privacy_get_setup(
        self, request: JsonObject, *, deadline_ms: int | None = None
    ) -> JsonObject:
        return await self._support(
            ControlMethod.PRIVACY_GET_SETUP, request, deadline_ms=deadline_ms
        )

    async def privacy_get_effective(
        self, request: JsonObject, *, deadline_ms: int | None = None
    ) -> JsonObject:
        return await self._support(
            ControlMethod.PRIVACY_GET_EFFECTIVE, request, deadline_ms=deadline_ms
        )

    async def privacy_propose_policy(
        self, request: JsonObject, *, deadline_ms: int | None = None
    ) -> JsonObject:
        return await self._support(
            ControlMethod.PRIVACY_PROPOSE_POLICY, request, deadline_ms=deadline_ms
        )

    async def privacy_tighten_policy(
        self, request: JsonObject, *, deadline_ms: int | None = None
    ) -> JsonObject:
        return await self._support(
            ControlMethod.PRIVACY_TIGHTEN_POLICY, request, deadline_ms=deadline_ms
        )

    async def privacy_receipts_list(
        self,
        request: ListPrivacyReceiptsRequest,
        *,
        deadline_ms: int | None = None,
    ) -> PrivacyReceiptPage:
        raw = await self._support(
            ControlMethod.PRIVACY_RECEIPTS_LIST,
            request._wire(),  # pyright: ignore[reportPrivateUsage]
            deadline_ms=deadline_ms,
        )
        try:
            return _receipt_page_from_wire(raw)
        except (KeyError, TypeError, ValueError) as exc:
            error = ControlError("frame_invalid")
            await self._fail_connection(error)
            raise error from exc

    async def privacy_receipts_get(
        self,
        request: GetPrivacyReceiptRequest,
        *,
        deadline_ms: int | None = None,
    ) -> PrivacyReceiptGetResult:
        raw = await self._support(
            ControlMethod.PRIVACY_RECEIPTS_GET,
            request._wire(),  # pyright: ignore[reportPrivateUsage]
            deadline_ms=deadline_ms,
        )
        try:
            return _receipt_get_from_wire(raw)
        except (KeyError, TypeError, ValueError) as exc:
            error = ControlError("frame_invalid")
            await self._fail_connection(error)
            raise error from exc

    async def service_status(self) -> ServiceStatus:
        return cast(ServiceStatus, await self._invoke(ControlMethod.SERVICE_STATUS, JsonObject({})))

    async def lock(self) -> ServiceStatus:
        return cast(ServiceStatus, await self._invoke(ControlMethod.SERVICE_LOCK, JsonObject({})))

    async def stop(self) -> ServiceStopResult:
        return cast(
            ServiceStopResult,
            await self._invoke(ControlMethod.SERVICE_STOP, JsonObject({})),
        )

    async def close(self) -> None:
        if os.getpid() != self._pid:
            self._closed = True
            return
        await self._fail_connection(ControlError("service_unavailable", retryable=True))


def _connected_client(
    stream: AuthenticatedUnixStream,
    session: ControlSession,
    client_kind: ControlClientKind,
) -> ServiceClient:
    return ServiceClient(
        stream,
        session,
        client_kind,
        _token=_PRIVATE_CONSTRUCTOR_TOKEN,
    )


async def connect_service(client_kind: ControlClientKind) -> ServiceClient:
    """Connect only to the verified fixed ordinary endpoint and perform the handshake."""

    if type(client_kind) is not ControlClientKind:
        raise TypeError("control_client_kind_invalid")
    stream: AuthenticatedUnixStream | None = None
    try:
        stream = await connect_control()
        session = await client_handshake(stream, client_kind, __version__)
        return _connected_client(stream, session, client_kind)
    except LocalControlTransportError as exc:
        if stream is not None:
            await stream.aclose()
        raise _transport_error(exc) from exc
    except ControlProtocolError as exc:
        if stream is not None:
            await stream.aclose()
        raise _protocol_error(exc) from exc
    except Exception:
        if stream is not None:
            await stream.aclose()
        raise


async def connect_service_on_demand(
    client_kind: ControlClientKind,
    *,
    timeout_seconds: float = _SERVICE_START_TIMEOUT_SECONDS,
) -> ServiceClient:
    """Connect to the fixed service, starting one detached successor only when absent.

    Startup is deliberately narrower than workflow authority: it supplies no path, configuration,
    credential, vault input, or policy override.  Concurrent bridges may race to spawn; the
    service singleton admits exactly one winner and every caller reconnects to that winner.
    """

    if type(client_kind) is not ControlClientKind:
        raise TypeError("control_client_kind_invalid")
    if type(timeout_seconds) is not float or not 0.1 <= timeout_seconds <= 30.0:
        raise ValueError("service_start_timeout_invalid")
    try:
        return await connect_service(client_kind)
    except ControlError as exc:
        if exc.reason != "service_unavailable":
            raise
    try:
        _spawn_service_process()
    except OSError as exc:
        raise ControlError("service_unavailable", retryable=True) from exc

    deadline = time.monotonic() + timeout_seconds
    last_error: ControlError | None = None
    while time.monotonic() < deadline:
        await asyncio.sleep(_SERVICE_START_POLL_SECONDS)
        try:
            return await connect_service(client_kind)
        except ControlError as exc:
            last_error = exc
            if exc.reason != "service_unavailable":
                raise
    raise ControlError("service_unavailable", retryable=True) from last_error
