"""Bounded ordinary client for the authenticated per-user Yoetz service."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Final, Literal, TypedDict, cast

from yoetz import __version__
from yoetz.adapters.control.unix_socket import (
    AuthenticatedUnixStream,
    LocalControlTransportError,
    connect_control,
)
from yoetz.config.paths import ensure_owner_only_dir, log_dir
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
    McpRouteProfile,
    ProjectionRenderMode,
    ServiceStatus,
    ServiceStopResult,
    WorkspaceLocator,
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

if TYPE_CHECKING:
    from yoetz.service.lifecycle import SingletonHolder

__all__ = [
    "GetPrivacyReceiptRequest",
    "ListPrivacyReceiptsRequest",
    "PrivacyReceiptFilters",
    "PrivacyReceiptFound",
    "PrivacyReceiptGetResult",
    "PrivacyReceiptNotFound",
    "ServiceClient",
    "accepted_but_unresponsive",
    "connect_service",
    "connect_service_on_demand",
    "service_holder_identity",
    "supersede_incompatible_service",
    "wait_for_singleton_release",
]

_MAX_IN_FLIGHT: Final = 32
_CANCEL_SEND_TIMEOUT_SECONDS: Final = 0.05
_STREAM_CLOSE_TIMEOUT_SECONDS: Final = 0.05
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
_CONNECT_HANDSHAKE_TIMEOUT_SECONDS: Final = 5.0
_SERVICE_START_TIMEOUT_SECONDS: Final = 30.0
_SERVICE_START_POLL_SECONDS: Final = 0.05
# A listening service that rejected this client's hello is a different Yoetz installation (or
# generation) sharing the one per-user endpoint. Both reasons name that condition; only these
# may make on-demand startup replace the running process.
_SUPERSEDE_REASONS: Final = frozenset({"service_incompatible", "protocol_mismatch"})
_SECRET_ENV_MARKERS: Final = (
    "API_KEY",
    "AUTHORIZATION",
    "CREDENTIAL",
    "PASSWORD",
    "SECRET",
    "TOKEN",
)
_SERVICE_CONFIG_ENV_NAMES: Final = frozenset(
    {
        "YOETZ_CONFIG",
        "YOETZ_LOG_LEVEL",
        "YOETZ_PROFILE",
        "YOETZ_PROVIDER_ENDPOINT_PROFILE_ID",
        "YOETZ_PROVIDER_ENDPOINT_PROFILE_VERSION",
        "YOETZ_PROVIDER_ID",
        "YOETZ_PROVIDER_MODEL",
        "YOETZ_PROVIDER_TIMEOUT_SECONDS",
        "YOETZ_STORAGE_DATA_DIR",
        "YOETZ_STORAGE_DURABILITY",
        "YOETZ_VERIFICATION_MAX_FINDINGS",
        "YOETZ_VERIFICATION_SEMANTIC",
    }
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
        and (not name.startswith("YOETZ_") or name in _SERVICE_CONFIG_ENV_NAMES)
    }


def _open_service_stderr_log() -> BinaryIO:
    root = log_dir()
    ensure_owner_only_dir(root)
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(root / "service.stderr.jsonl", flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        return cast(BinaryIO, os.fdopen(descriptor, "ab", buffering=0))
    except BaseException:
        os.close(descriptor)
        raise


def _spawn_service_process() -> None:
    command = (sys.executable, "-m", "yoetz", "service", "run")
    with _open_service_stderr_log() as stderr_log:
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
                stderr=stderr_log,
            )
        else:
            subprocess.Popen(  # noqa: S603 - fixed interpreter/module/arguments
                command,
                close_fds=True,
                env=_service_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=stderr_log,
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
    if error.reason == "endpoint_unsafe":
        return ControlError("endpoint_unsafe")
    return ControlError("service_unavailable", retryable=True)


def _protocol_error(error: ControlProtocolError) -> ControlError:
    if error.reason in {"manifest_mismatch", "handshake_rejected"}:
        # The peer is a live Yoetz service from another installation: it either answered with a
        # different schema-manifest digest or closed on our hello without answering (that is how
        # a service rejects a manifest it does not share). Retryable: it heals once the endpoint
        # is owned by a service of this installation.
        return ControlError("service_incompatible", retryable=True)
    if error.reason == "protocol_mismatch":
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
        "_retired_rpc_ids",
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
        self._retired_rpc_ids: set[str] = set()
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

    @property
    def hello_service_status(self) -> ServiceStatus | None:
        """The service-status snapshot this session's hello-result carried at connect time."""

        return self._session.service_status

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
                if result.rpc_id in self._retired_rpc_ids:
                    try:
                        self._session.correlate(result)
                    except ControlProtocolError as exc:
                        raise ControlError("frame_invalid") from exc
                    self._retired_rpc_ids.remove(result.rpc_id)
                    continue
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
            self._retired_rpc_ids.clear()
            for pending in tuple(self._pending.values()):
                if not pending.done():
                    pending.set_exception(error)
            await _best_effort_close_stream(self._stream)
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
        task = asyncio.create_task(self._send(request))
        try:
            done, _pending = await asyncio.wait((task,), timeout=_CANCEL_SEND_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            task.cancel()
            task.add_done_callback(_consume_background_task)
            return
        if task in done:
            _consume_background_task(task)
            return
        task.cancel()
        task.add_done_callback(_consume_background_task)

    def _retire_call(self, rpc_id: str, future: asyncio.Future[ControlResult]) -> None:
        """Keep bounded correlation state for a terminal result that may still arrive."""

        if not future.done():
            self._retired_rpc_ids.add(rpc_id)
            future.cancel()

    async def call(self, request: ControlCallRequest) -> ControlResult:
        self._ensure_live()
        self._admit(request.method)
        if (
            request.service_instance_id != self._session.service_instance_id
            or request.service_generation != self._session.service_generation
        ):
            raise ControlError("service_unavailable", retryable=True)
        if request.rpc_id in self._pending or request.rpc_id in self._retired_rpc_ids:
            raise ControlError("service_unavailable", retryable=True)
        if len(self._pending) + len(self._retired_rpc_ids) >= _MAX_IN_FLIGHT:
            await self._fail_connection(ControlError("service_unavailable", retryable=True))
            raise ControlError("service_unavailable", retryable=True)

        future = asyncio.get_running_loop().create_future()
        self._pending[request.rpc_id] = future
        sent = False
        try:
            try:
                self._session.admit(request)
            except ControlProtocolError as exc:
                raise ControlError("frame_invalid") from exc
            if request.deadline_ms is None:
                await self._send(request)
                sent = True
                result = await future
            else:
                try:
                    async with asyncio.timeout(request.deadline_ms / 1_000):
                        await self._send(request)
                        sent = True
                        result = await asyncio.shield(future)
                except TimeoutError as exc:
                    if sent:
                        self._retire_call(request.rpc_id, future)
                        await self._request_cancel(request.rpc_id)
                    else:
                        future.cancel()
                        await self._fail_connection(
                            ControlError("service_unavailable", retryable=True)
                        )
                    raise ControlError("request_timeout", retryable=True) from exc
        except asyncio.CancelledError:
            if sent:
                self._retire_call(request.rpc_id, future)
                await self._request_cancel(request.rpc_id)
            else:
                future.cancel()
                await self._fail_connection(ControlError("service_unavailable", retryable=True))
            raise
        finally:
            self._pending.pop(request.rpc_id, None)

        if result.outcome == "error" and isinstance(result.body, ControlError):
            # Only generation skew forces connection teardown. Projection errors
            # (privacy_projection_unavailable / privacy_projection_blocked) must propagate so the
            # agent can switch format or widen policy on the same connection.
            if result.body.reason == "service_generation_changed":
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
        route_profile: McpRouteProfile | None = None,
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
            route_profile=route_profile,
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

    async def check(
        self,
        request: CheckRequest,
        *,
        deadline_ms: int | None = None,
        route_profile: McpRouteProfile | None = None,
    ) -> CheckResult:
        return cast(
            CheckResult,
            await self._invoke(
                ControlMethod.CHECK,
                request,
                deadline_ms=deadline_ms,
                route_profile=route_profile,
            ),
        )

    async def respond(
        self, request: RespondRequest, *, deadline_ms: int | None = None
    ) -> RespondResult:
        return cast(
            RespondResult,
            await self._invoke(ControlMethod.RESPOND, request, deadline_ms=deadline_ms),
        )

    async def status(
        self,
        request: StatusRequest,
        *,
        deadline_ms: int | None = None,
        route_profile: McpRouteProfile | None = None,
    ) -> StatusResult:
        return cast(
            StatusResult,
            await self._invoke(
                ControlMethod.STATUS,
                request,
                deadline_ms=deadline_ms,
                route_profile=route_profile,
            ),
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

    async def observation_ingest(
        self, request: JsonObject, *, deadline_ms: int | None = None
    ) -> JsonObject:
        return await self._support(
            ControlMethod.OBSERVATION_INGEST, request, deadline_ms=deadline_ms
        )

    async def observation_status(
        self, request: JsonObject, *, deadline_ms: int | None = None
    ) -> JsonObject:
        return await self._support(
            ControlMethod.OBSERVATION_STATUS, request, deadline_ms=deadline_ms
        )

    async def observation_pause(
        self, request: JsonObject, *, deadline_ms: int | None = None
    ) -> JsonObject:
        return await self._support(
            ControlMethod.OBSERVATION_PAUSE, request, deadline_ms=deadline_ms
        )

    async def observation_resume(
        self, request: JsonObject, *, deadline_ms: int | None = None
    ) -> JsonObject:
        return await self._support(
            ControlMethod.OBSERVATION_RESUME, request, deadline_ms=deadline_ms
        )

    async def observation_revoke(
        self, request: JsonObject, *, deadline_ms: int | None = None
    ) -> JsonObject:
        return await self._support(
            ControlMethod.OBSERVATION_REVOKE, request, deadline_ms=deadline_ms
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

    async def privacy_pending_list(self, *, deadline_ms: int | None = None) -> JsonObject:
        """List disclosure proposals awaiting a local decision.

        Returns the wire object unchanged: it carries only ids, task, and expiry, so there is no
        richer local type for it to become and nothing here to hide.
        """

        return await self._support(
            ControlMethod.PRIVACY_PENDING_LIST, JsonObject({}), deadline_ms=deadline_ms
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


class _AcceptedServiceUnresponsive(ControlError):
    """A fixed endpoint accepted locally but did not complete its bounded handshake."""

    def __init__(self) -> None:
        super().__init__("service_unavailable", retryable=True)


def accepted_but_unresponsive(error: BaseException) -> bool:
    """True when a fixed endpoint accepted this connection and never completed its handshake.

    "Nothing is listening" and "something is listening and silent" call for opposite next steps,
    and the wire reason is the same for both. The distinction is only ever available here, on the
    client that observed it (#237).
    """

    return type(error) is _AcceptedServiceUnresponsive


def _consume_background_task[ResultT](task: asyncio.Task[ResultT]) -> None:
    try:
        task.result()
    except BaseException:
        pass


async def _best_effort_close_stream(stream: AuthenticatedUnixStream) -> None:
    """Bound cleanup even when a faulty peer stream never completes ``aclose``."""

    task = asyncio.create_task(stream.aclose())
    try:
        done, _pending = await asyncio.wait((task,), timeout=_STREAM_CLOSE_TIMEOUT_SECONDS)
    except BaseException:
        task.cancel()
        task.add_done_callback(_consume_background_task)
        raise
    if task in done:
        _consume_background_task(task)
        return
    task.cancel()
    task.add_done_callback(_consume_background_task)


async def _connect_service_attempt(
    client_kind: ControlClientKind,
    *,
    workspace_locator: WorkspaceLocator | None,
    projection_render_mode: ProjectionRenderMode,
    output_is_controlling_tty: bool,
    timeout_seconds: float,
) -> ServiceClient:
    """Connect and handshake within one attempt budget, preserving accepted-socket identity."""

    stream: AuthenticatedUnixStream | None = None
    deadline = time.monotonic() + timeout_seconds
    try:
        async with asyncio.timeout(timeout_seconds):
            stream = await connect_control()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        handshake = asyncio.create_task(
            client_handshake(
                stream,
                client_kind,
                __version__,
                workspace_locator=workspace_locator,
                projection_render_mode=projection_render_mode,
                output_is_controlling_tty=output_is_controlling_tty,
            )
        )
        try:
            done, _pending = await asyncio.wait((handshake,), timeout=remaining)
        except BaseException:
            handshake.cancel()
            handshake.add_done_callback(_consume_background_task)
            raise
        if handshake not in done:
            handshake.cancel()
            handshake.add_done_callback(_consume_background_task)
            raise TimeoutError
        session = handshake.result()
        return _connected_client(stream, session, client_kind)
    except TimeoutError as exc:
        accepted = stream is not None
        if stream is not None:
            await _best_effort_close_stream(stream)
        error: ControlError
        if accepted:
            error = _AcceptedServiceUnresponsive()
        else:
            error = ControlError("service_unavailable", retryable=True)
        raise error from exc
    except LocalControlTransportError as exc:
        if stream is not None:
            await _best_effort_close_stream(stream)
        raise _transport_error(exc) from exc
    except ControlProtocolError as exc:
        if stream is not None:
            await _best_effort_close_stream(stream)
        raise _protocol_error(exc) from exc
    except BaseException:
        if stream is not None:
            await _best_effort_close_stream(stream)
        raise


async def connect_service(
    client_kind: ControlClientKind,
    *,
    workspace_locator: WorkspaceLocator | None = None,
    projection_render_mode: ProjectionRenderMode = ProjectionRenderMode.MACHINE_READABLE,
    output_is_controlling_tty: bool = False,
) -> ServiceClient:
    """Connect only to the verified fixed ordinary endpoint and perform the handshake."""

    if type(client_kind) is not ControlClientKind:
        raise TypeError("control_client_kind_invalid")
    if workspace_locator is not None and type(workspace_locator) is not WorkspaceLocator:
        raise TypeError("workspace_locator_invalid")
    if type(projection_render_mode) is not ProjectionRenderMode:
        raise TypeError("projection_render_mode_invalid")
    if type(output_is_controlling_tty) is not bool:
        raise TypeError("projection_tty_fact_invalid")
    return await _connect_service_attempt(
        client_kind,
        workspace_locator=workspace_locator,
        projection_render_mode=projection_render_mode,
        output_is_controlling_tty=output_is_controlling_tty,
        timeout_seconds=_CONNECT_HANDSHAKE_TIMEOUT_SECONDS,
    )


def _singleton_lock_path() -> Path:
    from yoetz.config.paths import state_dir
    from yoetz.service.lifecycle import SINGLETON_LOCK_NAME

    return state_dir() / SINGLETON_LOCK_NAME


def service_holder_identity() -> SingletonHolder | None:
    """Read the advisory stamped holder of the fixed service without touching lock or endpoint.

    ``None`` means no live stamped holder could be read. The stamp is advisory identity only
    (never a fence); callers compare successive snapshots to learn that the service was started,
    stopped, or replaced, and must not infer health from it.
    """

    from yoetz.service.lifecycle import probe_singleton_holder_identity

    try:
        return probe_singleton_holder_identity(_singleton_lock_path())
    except Exception:
        return None


async def wait_for_singleton_release(pid: int, *, deadline: float) -> bool:
    """Poll until the stamped holder ``pid`` no longer owns the singleton, or the deadline passes."""

    from yoetz.service.lifecycle import probe_singleton_holder_identity

    path = _singleton_lock_path()
    while True:
        current = probe_singleton_holder_identity(path)
        if current is None or current.pid != pid:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(_SERVICE_START_POLL_SECONDS, remaining))


async def supersede_incompatible_service(*, deadline: float) -> bool:
    """Ask the live same-user singleton holder to stop and wait for it to release the endpoint.

    Called only after a listening service rejected this client's hello. The holder is identified
    through the owner-only singleton stamp (never guessed from process listings), receives the
    daemon's ordinary bounded-shutdown signal, and is polled until it releases the lock. Returns
    ``True`` once the endpoint is free, ``False`` when no supersede candidate can be identified
    (no live stamped holder, an identical-identity holder that rejected us anyway, or a platform
    without POSIX signals), and raises ``service_incompatible`` when the holder outlives the
    deadline. A durable diagnostic names every attempt.
    """

    if os.name == "nt":
        return False
    from yoetz.observability.logging import record_public_error_without_raising
    from yoetz.service.lifecycle import probe_singleton_holder_identity

    holder = probe_singleton_holder_identity(_singleton_lock_path())
    if holder is None:
        return False
    if (
        holder.schema_manifest_digest == _manifest_digest_for_client()
        and holder.service_version == __version__
    ):
        # Same installation identity yet it rejected the hello: this is not an upgrade, and
        # stopping it would not change the outcome. Report instead of restarting.
        return False
    correlation_id = record_public_error_without_raising(
        component="service.client",
        operation="service_supersede",
        reason="service_incompatible",
    )
    try:
        os.kill(holder.pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except OSError as exc:
        raise ControlError(
            "service_incompatible", retryable=True, correlation_id=correlation_id
        ) from exc
    if await wait_for_singleton_release(holder.pid, deadline=deadline):
        return True
    raise ControlError("service_incompatible", retryable=True, correlation_id=correlation_id)


def _manifest_digest_for_client() -> str:
    from yoetz.protocol.schemas import schema_manifest_digest

    return schema_manifest_digest()


async def connect_service_on_demand(
    client_kind: ControlClientKind,
    *,
    workspace_locator: WorkspaceLocator | None = None,
    timeout_seconds: float = _SERVICE_START_TIMEOUT_SECONDS,
    supersede_incompatible: bool = True,
) -> ServiceClient:
    """Connect to the fixed service, starting one detached successor only when absent.

    Startup is deliberately narrower than workflow authority: it supplies no path, configuration,
    credential, vault input, or policy override.  Concurrent bridges may race to spawn; the
    service singleton admits exactly one winner and every caller reconnects to that winner.

    When ``supersede_incompatible`` is set and the running service rejects this client's hello
    (a stale process from another installation holding the one per-user endpoint after an
    upgrade), the holder is asked to stop through its ordinary bounded shutdown and a successor
    of this installation is spawned inside the same budget. The one endpoint then belongs to
    the installation that is actually being used; bridges of the stale installation reconnect
    and are refused in turn, which is the correct outcome of an upgrade.
    """

    if type(client_kind) is not ControlClientKind:
        raise TypeError("control_client_kind_invalid")
    if workspace_locator is not None and type(workspace_locator) is not WorkspaceLocator:
        raise TypeError("workspace_locator_invalid")
    if type(timeout_seconds) is not float or not 0.1 <= timeout_seconds <= 30.0:
        raise ValueError("service_start_timeout_invalid")
    deadline = time.monotonic() + timeout_seconds

    async def connect_with_remaining_budget() -> ServiceClient:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ControlError("service_unavailable", retryable=True)
        return await _connect_service_attempt(
            client_kind,
            workspace_locator=workspace_locator,
            projection_render_mode=ProjectionRenderMode.MACHINE_READABLE,
            output_is_controlling_tty=False,
            timeout_seconds=min(_CONNECT_HANDSHAKE_TIMEOUT_SECONDS, remaining),
        )

    try:
        return await connect_with_remaining_budget()
    except _AcceptedServiceUnresponsive:
        # A process already owns and accepted the fixed endpoint. Starting a successor cannot
        # repair that process and only creates a singleton race, so fail this bounded attempt.
        raise
    except ControlError as exc:
        if exc.reason in _SUPERSEDE_REASONS and supersede_incompatible:
            if not await supersede_incompatible_service(deadline=deadline):
                raise
        elif exc.reason != "service_unavailable":
            raise
    if time.monotonic() >= deadline:
        raise ControlError("service_unavailable", retryable=True)
    try:
        _spawn_service_process()
    except OSError as exc:
        raise ControlError("service_unavailable", retryable=True) from exc

    last_error: ControlError | None = None
    while time.monotonic() < deadline:
        await asyncio.sleep(min(_SERVICE_START_POLL_SECONDS, deadline - time.monotonic()))
        try:
            return await connect_with_remaining_budget()
        except _AcceptedServiceUnresponsive as exc:
            # This process spawned the owner milliseconds ago. Accepted-but-silent here means
            # "still starting", not "wedged": no successor is involved, so spending the remaining
            # budget is the remedy rather than an unbounded wait. The pre-spawn arm above is
            # unchanged and still fails fast for a pre-existing owner (#232/#233).
            last_error = exc
            continue
        except ControlError as exc:
            last_error = exc
            if exc.reason != "service_unavailable":
                raise
    raise ControlError("service_unavailable", retryable=True) from last_error
