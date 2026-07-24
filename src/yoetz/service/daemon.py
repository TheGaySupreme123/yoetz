"""Trusted per-user local-service orchestration and ordinary-control dispatch."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import os
import signal
import stat
import struct
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Never, Protocol, cast

import anyio
from pydantic import BaseModel

from yoetz import __version__
from yoetz.adapters.control.unix_socket import (
    EndpointKind,
    LocalControlTransportError,
    bind_control_listener,
    bind_human_control_listener,
    bind_secret_listener,
    remove_stale_endpoint,
)
from yoetz.adapters.keys.encrypted_vault import EncryptedVaultStore
from yoetz.adapters.keys.os_keyring import AutoUnlockPassphraseStore
from yoetz.adapters.keys.secret_memory import LocalSecretMemory
from yoetz.adapters.keys.vault_passphrase import VaultRootEnvelope
from yoetz.adapters.session_events import SessionEventMonitor
from yoetz.application.service import (
    ClientProjectionContext,
    ControlProjectionBinding,
    ProjectedControlBody,
    ProjectionBindingFacts,
)
from yoetz.config.load import load_config
from yoetz.config.models import YoetzConfig
from yoetz.config.paths import (
    bundle_root,
    ensure_owner_only_dir,
    service_generation_path,
    state_dir,
    unlock_throttle_path,
    verify_private_local_bundle,
)
from yoetz.observability.logging import record_unexpected_exception_without_raising
from yoetz.ports.control import (
    ControlCallRequest,
    ControlClientKind,
    ControlError,
    ControlMethod,
    ControlResult,
    ServiceState,
    ServiceStatus,
    ServiceStopResult,
)
from yoetz.ports.diagnostics import StartupCheckResult
from yoetz.ports.secret_memory import HumanAuthorizationProof, SecretHandle, SecretPurpose
from yoetz.protocol.canonical import (
    JsonValue,
    canonical_digest,
    canonical_encode,
    strict_json_parse,
)
from yoetz.protocol.errors import PublicOperationError
from yoetz.protocol.ids import IdKind, new_id, validate_id
from yoetz.protocol.models import (
    CheckResult,
    PublishWorkResult,
    ReceiptResult,
    RespondResult,
    StartResult,
    StatusResult,
)
from yoetz.service.confidential_protocol import (
    HUMAN_PROTOCOL_MAGIC,
    HUMAN_PROTOCOL_VERSION,
    MAX_HUMAN_CONTROL_FRAME_BYTES,
    ClientActionEnvelope,
    ClientCancelEnvelope,
    ClientOpenEnvelope,
    EmptyVaultTarget,
    HumanCeremonyKind,
    HumanPreview,
    IdleRelockPolicyResult,
    IdleRelockPolicyTarget,
    KeyringRetryPreview,
    PortableRecoveryResult,
    PortableRecoveryTarget,
    PrivacyDecisionResult,
    PrivacyPendingTarget,
    ProviderCredentialResult,
    ProviderCredentialRotatePreview,
    ProviderCredentialSetPreview,
    ProviderCredentialTarget,
    SecretRequiredPhase,
    ServerCloseEnvelope,
    ServerErrorEnvelope,
    ServerResultEnvelope,
    VaultInitializePreview,
    VaultUnlockPreview,
    decode_human_frame,
    encode_human_frame,
)
from yoetz.service.control_protocol import (
    ControlProtocolError,
    ControlSession,
    ControlStream,
    encode_control_frame,
    parse_control_request,
    read_control_frame,
    server_handshake,
    validate_request,
    validate_result,
    write_control_frame,
)
from yoetz.service.human_control import HumanControlError, HumanControlService
from yoetz.service.lifecycle import (
    Admission,
    LifecycleError,
    ServiceLifecycle,
    SessionSecurityEvent,
)
from yoetz.service.ready_composition import build_ready_application_factory
from yoetz.service.secret_ingress import SecretIngressService
from yoetz.service.unlock import UnlockCoordinator, UnlockThrottleRecord, UnlockThrottleStore
from yoetz.service.vault import VaultMode, VaultService

__all__ = ["ServiceComposition", "ServiceDaemon", "main", "run_service"]

_INSTALLATION_MARKER_NAME = "installation-state.json"
_INSTALLATION_MARKER_DOMAIN = b"yoetz/installation-state/v1\x00"
_SERVICE_GENERATION_DOMAIN = b"yoetz/service-generation/v1\x00"
_MAX_INSTALLATION_MARKER_BYTES = 65_536
_HUMAN_HEADER = struct.Struct(">4sBBI")

_WORKFLOW_METHODS = frozenset(
    {
        ControlMethod.START,
        ControlMethod.PUBLISH_WORK,
        ControlMethod.CHECK,
        ControlMethod.RESPOND,
        ControlMethod.STATUS,
        ControlMethod.RECEIPT,
    }
)
_STRUCTURAL_METHODS = frozenset(
    {
        ControlMethod.SERVICE_STATUS,
        ControlMethod.SERVICE_LOCK,
        ControlMethod.SERVICE_STOP,
    }
)
_PROJECTION_EXEMPT_METHODS = _STRUCTURAL_METHODS | {
    ControlMethod.PRIVACY_RECEIPTS_LIST,
    ControlMethod.PRIVACY_RECEIPTS_GET,
}
_WORKFLOW_RESULT_MODELS: Final[Mapping[ControlMethod, type[BaseModel]]] = {
    ControlMethod.START: StartResult,
    ControlMethod.PUBLISH_WORK: PublishWorkResult,
    ControlMethod.CHECK: CheckResult,
    ControlMethod.RESPOND: RespondResult,
    ControlMethod.STATUS: StatusResult,
    ControlMethod.RECEIPT: ReceiptResult,
}


class _Listener(Protocol):
    async def accept(self) -> ControlStream: ...

    async def aclose(self) -> None: ...


class _ServiceLockAuthority(Protocol):
    def assert_held(self) -> None: ...


class _SessionCapability(Protocol):
    @property
    def active(self) -> bool: ...


class _SessionMonitor(Protocol):
    @property
    def capability(self) -> _SessionCapability: ...

    async def start(self, callback: Callable[[SessionSecurityEvent], Awaitable[None]]) -> None: ...

    async def close(self) -> None: ...


class _Vault(Protocol):
    @property
    def ready(self) -> bool: ...

    @property
    def generation(self) -> int: ...

    @property
    def mode(self) -> object: ...

    async def lock(self) -> object: ...

    async def close(self) -> None: ...


class _ReadyApplication(Protocol):
    async def projection_binding_facts(
        self,
        method: ControlMethod,
        request: object,
        result: object,
    ) -> ProjectionBindingFacts: ...

    async def project_result_for_client(
        self,
        context: ClientProjectionContext,
        binding: ControlProjectionBinding,
        result: object,
    ) -> ProjectedControlBody: ...

    async def close(self) -> None: ...


class _Closable(Protocol):
    async def close(self) -> None: ...


class _SyncClosable(Protocol):
    def close(self) -> None: ...


class _Diagnostics(Protocol):
    def record(self, result: StartupCheckResult) -> None: ...


type _HumanConnectionHandler = Callable[[ControlStream], Awaitable[None]]
type _ReadyApplicationFactory = Callable[[int, int], Awaitable[_ReadyApplication]]


class _ReadyActivationRelay:
    """Bind UnlockCoordinator to the daemon without exposing application ownership."""

    def __init__(self) -> None:
        self._activate: Callable[[int, int], Awaitable[None]] | None = None

    def bind(self, activate: Callable[[int, int], Awaitable[None]]) -> None:
        if self._activate is not None or not callable(activate):
            raise RuntimeError("ready_activation_relay_invalid")
        self._activate = activate

    async def __call__(self, service_generation: int, vault_generation: int) -> None:
        activate = self._activate
        if activate is None:
            raise RuntimeError("ready_activation_relay_unbound")
        await activate(service_generation, vault_generation)


class _ReadyCloseRelay:
    """Let lifecycle drains close the daemon-owned ready graph exactly once."""

    def __init__(self) -> None:
        self._close: Callable[[], Awaitable[None]] | None = None

    def bind(self, close: Callable[[], Awaitable[None]]) -> None:
        if self._close is not None or not callable(close):
            raise RuntimeError("ready_close_relay_invalid")
        self._close = close

    async def __call__(self) -> None:
        close = self._close
        if close is None:
            raise RuntimeError("ready_close_relay_unbound")
        await close()


@dataclass(frozen=True, slots=True)
class ServiceComposition:
    """One generation's service-owned components, redacted by construction."""

    lifecycle: ServiceLifecycle
    control_listener: _Listener
    secret_ingress_listener: _Listener | None
    human_control_listener: _Listener | None
    human_control_service: _Closable | None
    session_monitor: _SessionMonitor | None
    vault: _Vault
    application: _ReadyApplication | None = None
    ready_application_factory: _ReadyApplicationFactory | None = None
    secret_ingress_service: _Closable | None = None
    unlock_service: _Closable | None = None
    secret_memory: _SyncClosable | None = None
    diagnostics: _Diagnostics | None = None
    human_connection_handler: _HumanConnectionHandler | None = None
    ready_activation_relay: _ReadyActivationRelay | None = None
    ready_close_relay: _ReadyCloseRelay | None = None

    def __repr__(self) -> str:
        return "ServiceComposition(<redacted>)"


class ServiceDaemon:
    """Own lifecycle, listeners, dispatch admission, and bounded teardown."""

    def __init__(self, *, _composition: ServiceComposition) -> None:
        if type(_composition) is not ServiceComposition:
            raise TypeError("service_composition_invalid")
        self._composition = _composition
        relay = _composition.ready_activation_relay
        if relay is not None:
            relay.bind(self.activate_ready_application)
        close_relay = _composition.ready_close_relay
        if close_relay is not None:
            close_relay.bind(self._close_ready_locked)
        self._application = _composition.application
        self._started = False
        self._closed = False
        self._stopping = False
        self._state_reason = "none"
        self._monitor_state = "unavailable"
        self._stop_event = asyncio.Event()
        self._start_lock = asyncio.Lock()
        self._activation_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._connection_tasks: set[asyncio.Task[None]] = set()

    @property
    def composition(self) -> ServiceComposition:
        return self._composition

    async def start(self) -> None:
        """Acquire singleton authority and publish exactly one admitted state."""

        async with self._start_lock:
            if self._started:
                return
            if self._closed:
                raise LifecycleError("invalid_transition")
            lifecycle = self._composition.lifecycle
            await lifecycle.acquire_singleton()
            try:
                await lifecycle.publish_endpoint()
                monitor = self._composition.session_monitor
                if monitor is not None:
                    try:
                        await monitor.start(self._on_session_event)
                    except Exception:
                        self._monitor_state = "unavailable"
                    else:
                        self._monitor_state = (
                            "active" if monitor.capability.active else "unavailable"
                        )

                if (
                    self._composition.vault.ready
                    and self._application is None
                    and self._composition.ready_application_factory is not None
                ):
                    await lifecycle.transition(ServiceState.LOCKED)
                    await lifecycle.transition(ServiceState.UNLOCKING)
                    await self.activate_ready_application(
                        lifecycle.instance.generation,
                        self._composition.vault.generation,
                    )

                if self._application is not None and self._composition.vault.ready:
                    if lifecycle.state is not ServiceState.READY:
                        await lifecycle.transition(
                            ServiceState.READY,
                            vault_generation=self._composition.vault.generation,
                        )
                    self._state_reason = "none"
                else:
                    await lifecycle.transition(ServiceState.LOCKED)
                    self._state_reason = self._locked_reason()
                self._started = True
            except BaseException:
                if lifecycle.state is ServiceState.STARTING:
                    await lifecycle.transition(ServiceState.FAILED)
                await self.close()
                raise

    async def activate_ready_application(
        self, service_generation: int, vault_generation: int
    ) -> None:
        """Construct and publish one generation-bound ready application."""

        partial: _ReadyApplication | None = None
        async with self._activation_lock:
            lifecycle = self._composition.lifecycle
            try:
                if (
                    type(service_generation) is not int
                    or type(vault_generation) is not int
                    or service_generation <= 0
                    or vault_generation <= 0
                    or lifecycle.state is not ServiceState.UNLOCKING
                    or lifecycle.instance.generation != service_generation
                    or not self._composition.vault.ready
                    or self._composition.vault.generation != vault_generation
                    or self._application is not None
                ):
                    raise LifecycleError("invalid_transition")
                factory = self._composition.ready_application_factory
                if factory is None:
                    raise LifecycleError("invalid_transition")
                partial = await factory(service_generation, vault_generation)
                if not _is_ready_application(partial):
                    raise TypeError("ready_application_invalid")
                if (
                    lifecycle.state is not ServiceState.UNLOCKING
                    or lifecycle.instance.generation != service_generation
                    or not self._composition.vault.ready
                    or self._composition.vault.generation != vault_generation
                ):
                    raise LifecycleError("invalid_transition")
                self._application = partial
                await lifecycle.transition(
                    ServiceState.READY,
                    vault_generation=vault_generation,
                )
                self._state_reason = "none"
            except BaseException:
                if self._application is partial:
                    self._application = None
                if partial is not None:
                    try:
                        await partial.close()
                    except Exception:
                        pass
                try:
                    await self._composition.vault.lock()
                except Exception:
                    pass
                if lifecycle.state is ServiceState.UNLOCKING:
                    try:
                        await lifecycle.transition(ServiceState.LOCKED)
                    except Exception:
                        pass
                self._state_reason = "unlock_failed"
                raise

    async def serve(self) -> None:
        """Serve authenticated ordinary and human-control clients in foreground."""

        await self.start()
        self._install_signal_handlers()
        idle = asyncio.create_task(self._composition.lifecycle.run_idle_monitor())
        control = asyncio.create_task(
            self._accept_loop(self._composition.control_listener, self._serve_control_connection)
        )
        human: asyncio.Task[None] | None = None
        if (
            self._composition.human_control_listener is not None
            and self._composition.human_connection_handler is not None
        ):
            human = asyncio.create_task(
                self._accept_loop(
                    self._composition.human_control_listener,
                    self._composition.human_connection_handler,
                )
            )
        stop_wait = asyncio.create_task(self._stop_event.wait())
        stop_reason = "shutdown_requested"
        try:
            done, _pending = await asyncio.wait(
                {idle, stop_wait}, return_when=asyncio.FIRST_COMPLETED
            )
            if idle in done:
                await idle
                stop_reason = "idle_shutdown"
        finally:
            await self.stop(stop_reason)
            for task in (idle, control, human, stop_wait):
                if task is not None:
                    task.cancel()
            for task in (idle, control, human, stop_wait):
                if task is not None:
                    await asyncio.gather(task, return_exceptions=True)

    async def dispatch(
        self,
        client_kind: ControlClientKind,
        request: ControlCallRequest,
        *,
        projection_context: ClientProjectionContext | None = None,
        _defer_stop: bool = False,
    ) -> ControlResult:
        """Validate, admit, execute, project, and correlate one ordinary call."""

        try:
            if type(client_kind) is not ControlClientKind:
                raise ControlError("method_forbidden")
            if projection_context is None:
                projection_context = ClientProjectionContext.fail_safe(client_kind)
            elif (
                type(projection_context) is not ClientProjectionContext
                or projection_context.client_kind is not client_kind
            ):
                raise ControlError("frame_invalid")
            validate_request(request)
            self._validate_generation(request)
            self._validate_client_method(client_kind, request.method)
            if request.method is ControlMethod.SERVICE_STATUS:
                body: object = self.status()
            elif request.method is ControlMethod.SERVICE_LOCK:
                await self.lock("explicit_lock")
                body = self.status()
            elif request.method is ControlMethod.SERVICE_STOP:
                body = ServiceStopResult()
            else:
                body = await self._dispatch_ready(projection_context, request)
            result = self._result(request, body)
            if request.method is ControlMethod.SERVICE_STOP and not _defer_stop:
                await self.stop()
            return result
        except asyncio.CancelledError:
            return self._error_result(request, ControlError("request_cancelled", retryable=True))
        except ControlError as exc:
            return self._error_result(request, exc)
        except LifecycleError as exc:
            reason = "service_draining" if exc.reason == "service_draining" else "vault_locked"
            return self._error_result(request, ControlError(reason, retryable=True))
        except PublicOperationError as exc:
            return self._public_operation_failure_result(request, exc)
        except ControlProtocolError, TypeError, ValueError:
            return self._error_result(request, ControlError("frame_invalid"))
        except Exception as exc:
            record_unexpected_exception_without_raising(
                exc,
                component="service.daemon",
                operation=f"{request.method.value}_internal_error",
            )
            return self._error_result(request, ControlError("internal_error"))

    async def lock(self, reason: str = "explicit_lock") -> None:
        """Drain the ready generation, close it, and remain structurally available."""

        if not self._started or self._closed:
            return
        lifecycle = self._composition.lifecycle
        async with self._activation_lock:
            if lifecycle.state is ServiceState.READY:
                await lifecycle.request_lock(reason)
            elif lifecycle.state is ServiceState.UNLOCKING:
                await self._composition.vault.lock()
                await lifecycle.transition(ServiceState.LOCKED)
            await self._close_ready_locked()
            self._state_reason = (
                reason
                if reason
                in {
                    "explicit_lock",
                    "idle_relock",
                    "user_session_locked",
                    "system_suspend",
                    "monitor_lost",
                }
                else "explicit_lock"
            )

    async def stop(self, reason: str = "shutdown_requested") -> None:
        """Drain, unlink owned endpoints, release singleton authority, and stop serving."""

        async with self._close_lock:
            if self._closed:
                self._stop_event.set()
                return
            self._stopping = True
            self._state_reason = reason if reason == "internal_error" else "shutdown_requested"
            lifecycle = self._composition.lifecycle
            if self._started:
                async with self._activation_lock:
                    if lifecycle.state is ServiceState.UNLOCKING:
                        await self._composition.vault.lock()
                        await lifecycle.transition(ServiceState.LOCKED)
                    if lifecycle.state is not ServiceState.DRAINING:
                        try:
                            await lifecycle.request_stop(reason)
                        except LifecycleError as exc:
                            if exc.reason != "invalid_transition":
                                raise
            await self._close_components()
            await lifecycle.close()
            self._closed = True
            self._stop_event.set()

    async def close(self) -> None:
        """Idempotent alias for bounded service shutdown."""

        await self.stop()

    def status(self) -> ServiceStatus:
        lifecycle = self._composition.lifecycle
        instance = lifecycle.instance
        vault_mode = getattr(self._composition.vault.mode, "value", self._composition.vault.mode)
        if vault_mode not in {"uninitialized", "os_keyring", "passphrase"}:
            vault_mode = "uninitialized"
        capabilities = {"confidential_ingress"}
        if lifecycle.state is ServiceState.READY and self._application is not None:
            capabilities.update({"workflow", "maintenance", "import_review"})
        if self._monitor_state == "active":
            capabilities.add("session_event_monitor")
        return ServiceStatus(
            protocol_version="1.0",
            service_version=__version__,
            service_instance_id=instance.instance_id,
            service_generation=str(instance.generation),
            state=lifecycle.state,
            state_reason=self._state_reason,
            vault_mode=cast(str, vault_mode),
            capabilities=tuple(sorted(capabilities, key=lambda value: value.encode("ascii"))),
            session_monitor=self._monitor_state,
            idle_relock_seconds=lifecycle.idle_relock_policy.seconds,
        )

    async def _dispatch_ready(
        self, projection_context: ClientProjectionContext, request: ControlCallRequest
    ) -> object:
        application = self._application
        if application is None:
            raise ControlError("vault_locked", retryable=True)
        admission: Admission | None = None
        try:
            admission = await self._composition.lifecycle.admit(request.method.value)
            handler = getattr(application, request.method.value, None)
            if not callable(handler):
                raise ControlError("method_forbidden")
            if request.deadline_ms is None:
                internal = await cast(Callable[[object], Awaitable[object]], handler)(request.body)
            else:
                try:
                    async with asyncio.timeout(request.deadline_ms / 1_000):
                        internal = await cast(Callable[[object], Awaitable[object]], handler)(
                            request.body
                        )
                except TimeoutError as exc:
                    raise ControlError("request_timeout", retryable=True) from exc
            if request.method in _PROJECTION_EXEMPT_METHODS:
                self._validate_success_body(request, internal)
                return internal
            facts = await application.projection_binding_facts(
                request.method,
                request.body,
                internal,
            )
            binding = ControlProjectionBinding(
                rpc_id=request.rpc_id,
                method=request.method,
                service_instance_id=request.service_instance_id,
                service_generation=int(request.service_generation),
                original_request_id=facts.original_request_id,
                route_identity_digest=facts.route_identity_digest,
                control_request_canonical=encode_control_frame(request)[4:],
            )
            projected = await application.project_result_for_client(
                projection_context,
                binding,
                internal,
            )
            self._validate_success_body(request, projected)
            return projected
        finally:
            if admission is not None:
                await self._composition.lifecycle.release(admission)

    async def _accept_loop(
        self,
        listener: _Listener,
        handler: Callable[[ControlStream], Awaitable[None]],
    ) -> None:
        while not self._stop_event.is_set():
            try:
                stream = await listener.accept()
            except asyncio.CancelledError:
                raise
            except Exception:
                if self._stopping or self._closed:
                    return
                await self.stop("internal_error")
                return
            task = asyncio.create_task(self._run_handler(handler, stream))
            self._connection_tasks.add(task)
            task.add_done_callback(self._connection_tasks.discard)

    async def _run_handler(
        self, handler: Callable[[ControlStream], Awaitable[None]], stream: ControlStream
    ) -> None:
        lifecycle = self._composition.lifecycle
        connected = False
        try:
            await lifecycle.client_connected()
            connected = True
            await handler(stream)
        finally:
            if connected:
                try:
                    await lifecycle.client_disconnected()
                except LifecycleError:
                    pass

    async def _serve_control_connection(self, stream: ControlStream) -> None:
        session: ControlSession | None = None
        calls: dict[str, asyncio.Task[None]] = {}
        write_lock = asyncio.Lock()
        try:
            session = await server_handshake(stream, stream.peer_identity, self.status())
            while not self._stop_event.is_set():
                request = parse_control_request(await read_control_frame(stream))
                session.admit(request)
                if not isinstance(request, ControlCallRequest):
                    target = calls.get(request.target_rpc_id)
                    if target is not None:
                        target.cancel()
                    continue
                task = asyncio.create_task(self._serve_call(stream, session, request, write_lock))
                calls[request.rpc_id] = task
                task.add_done_callback(lambda _task, rpc_id=request.rpc_id: calls.pop(rpc_id, None))
        except asyncio.CancelledError:
            raise
        except Exception:
            return
        finally:
            if session is not None:
                session.close()
            for task in tuple(calls.values()):
                task.cancel()
            await asyncio.gather(*calls.values(), return_exceptions=True)
            await stream.aclose()

    async def _serve_call(
        self,
        stream: ControlStream,
        session: ControlSession,
        request: ControlCallRequest,
        write_lock: asyncio.Lock,
    ) -> None:
        result = await self.dispatch(session.client_kind, request, _defer_stop=True)
        session.correlate(result)
        async with write_lock:
            await write_control_frame(stream, result)
        if request.method is ControlMethod.SERVICE_STOP and result.outcome == "ok":
            await self.stop()

    def _result(self, request: ControlCallRequest, body: object) -> ControlResult:
        result = ControlResult(
            protocol_version="1.0",
            rpc_id=request.rpc_id,
            service_instance_id=request.service_instance_id,
            service_generation=request.service_generation,
            method=request.method,
            outcome="ok",
            body=body,  # pyright: ignore[reportArgumentType]
        )
        validate_result(result)
        return result

    def _error_result(self, request: ControlCallRequest, error: ControlError) -> ControlResult:
        result = ControlResult(
            protocol_version="1.0",
            rpc_id=request.rpc_id,
            service_instance_id=request.service_instance_id,
            service_generation=request.service_generation,
            method=request.method,
            outcome="error",
            body=error,
        )
        validate_result(result)
        return result

    def _public_operation_failure_result(
        self, request: ControlCallRequest, error: PublicOperationError
    ) -> ControlResult:
        """Frame a bound public application failure as outcome=ok with ok:false body.

        Workflow PublicOperationError values are already client-safe. They must not collapse into
        control ``internal_error``, and they never enter privacy projection (no content-bearing
        success body exists to project).
        """

        result_type = _WORKFLOW_RESULT_MODELS.get(request.method)
        if result_type is None or type(error) is not PublicOperationError:
            record_unexpected_exception_without_raising(
                error,
                component="service.daemon",
                operation=f"{request.method.value}_public_error_internal_error",
            )
            return self._error_result(request, ControlError("internal_error"))
        try:
            bound = (
                error
                if error.correlation_id is not None
                else error.bind_correlation_id(new_id(IdKind.CORRELATION))
            )
            request_id = getattr(request.body, "request_id", None)
            candidate: dict[str, object] = {
                "protocol_version": "0.1",
                "schema_version": "1.0.0",
                "ok": False,
                "error": bound.as_public_dict(),
            }
            if type(request_id) is str:
                candidate["request_id"] = request_id
            body = result_type.model_validate(candidate)
            return self._result(request, body)
        except Exception as exc:
            record_unexpected_exception_without_raising(
                exc,
                component="service.daemon",
                operation=f"{request.method.value}_public_error_internal_error",
            )
            return self._error_result(request, ControlError("internal_error"))

    def _validate_success_body(self, request: ControlCallRequest, body: object) -> None:
        self._result(request, body)

    def _validate_generation(self, request: ControlCallRequest) -> None:
        instance = self._composition.lifecycle.instance
        if request.service_instance_id != instance.instance_id or request.service_generation != str(
            instance.generation
        ):
            raise ControlError("service_generation_changed", retryable=True)

    @staticmethod
    def _validate_client_method(client_kind: ControlClientKind, method: ControlMethod) -> None:
        if client_kind is ControlClientKind.MCP_BRIDGE and method not in _WORKFLOW_METHODS:
            raise ControlError("method_forbidden")

    async def _on_session_event(self, event: SessionSecurityEvent) -> None:
        if type(event) is not SessionSecurityEvent:
            self._monitor_state = "lost"
            await self.lock("monitor_lost")
            return
        value = event.value
        if value in {"user_session_locked", "system_suspend", "monitor_lost"}:
            if value == "monitor_lost":
                self._monitor_state = "lost"
            await self.lock(value)
            return
        await self._composition.lifecycle.on_session_event(event)

    async def _close_ready(self) -> None:
        async with self._activation_lock:
            await self._close_ready_locked()

    async def _close_ready_locked(self) -> None:
        application, self._application = self._application, None
        if application is not None:
            await application.close()
        if self._composition.vault.ready:
            await self._composition.vault.lock()

    async def _close_components(self) -> None:
        await self._close_ready()
        failure: BaseException | None = None
        for listener in (
            self._composition.human_control_listener,
            self._composition.secret_ingress_listener,
            self._composition.control_listener,
        ):
            if listener is not None:
                try:
                    await listener.aclose()
                except BaseException as exc:
                    if failure is None:
                        failure = exc
        for component in (
            self._composition.human_control_service,
            self._composition.unlock_service,
            self._composition.secret_ingress_service,
            self._composition.session_monitor,
        ):
            if component is not None:
                try:
                    await component.close()
                except BaseException as exc:
                    if failure is None:
                        failure = exc
        try:
            await self._composition.vault.close()
        except BaseException as exc:
            if failure is None:
                failure = exc
        if self._composition.secret_memory is not None:
            try:
                self._composition.secret_memory.close()
            except BaseException as exc:
                if failure is None:
                    failure = exc
        if failure is not None:
            raise failure

    def _locked_reason(self) -> str:
        mode = getattr(self._composition.vault.mode, "value", self._composition.vault.mode)
        return "vault_uninitialized" if mode == "uninitialized" else "keyring_locked"

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, self._stop_event.set)
            except NotImplementedError, RuntimeError:
                continue


def _is_ready_application(value: object) -> bool:
    return (
        callable(getattr(value, "projection_binding_facts", None))
        and callable(getattr(value, "project_result_for_client", None))
        and callable(getattr(value, "close", None))
    )


@dataclass(frozen=True, slots=True)
class _InstallationState:
    installation_id: str
    vault_mode: VaultMode
    root_envelope: VaultRootEnvelope | None
    mode_binding_digest: str

    def __post_init__(self) -> None:
        validate_id(IdKind.INSTALLATION, self.installation_id)
        if self.vault_mode is VaultMode.UNINITIALIZED:
            raise ValueError("installation_marker_mode_invalid")
        if (self.vault_mode is VaultMode.PASSPHRASE) != (self.root_envelope is not None):
            raise ValueError("installation_marker_envelope_invalid")
        if not _is_sha256(self.mode_binding_digest):
            raise ValueError("installation_marker_binding_invalid")


@dataclass(frozen=True, slots=True)
class _ProductionPaths:
    bundle: Path
    generation: Path
    throttle: Path
    singleton_lock: Path

    @classmethod
    def canonical(cls, config: YoetzConfig) -> _ProductionPaths:
        root = bundle_root(_data_dir=config.storage.data_dir)
        ensure_owner_only_dir(root)
        verify_private_local_bundle(root)
        generation = service_generation_path()
        throttle = unlock_throttle_path()
        metadata_root = state_dir()
        ensure_owner_only_dir(metadata_root)
        verify_private_local_bundle(metadata_root)
        return cls(root, generation, throttle, metadata_root / "service.lock")


@dataclass(frozen=True, slots=True)
class _ListenerBinders:
    control: Callable[[], Awaitable[_Listener]]
    secret: Callable[[], Awaitable[_Listener]]
    human: Callable[[], Awaitable[_Listener]]


class _SystemClock:
    def now_utc(self) -> datetime:
        # RFC3339 millis contract rejects sub-millisecond timestamps; truncate like
        # observability._SystemClock so throttle wall-anomaly checks do not false-positive
        # into a 300s unlock lockout on every restart.
        now = datetime.now(UTC)
        return now.replace(microsecond=(now.microsecond // 1_000) * 1_000)

    def monotonic_seconds(self) -> float:
        return time.monotonic()


class _NullDiagnostics:
    def record(self, result: StartupCheckResult) -> None:
        if type(result) is not StartupCheckResult:
            raise TypeError("startup_diagnostic_invalid")


class _DeferredListener:
    def __init__(self) -> None:
        self._listener: _Listener | None = None
        self._closed = False

    def install(self, listener: _Listener) -> None:
        if self._closed or self._listener is not None:
            raise RuntimeError("listener_install_invalid")
        self._listener = listener

    async def accept(self) -> ControlStream:
        listener = self._listener
        if listener is None or self._closed:
            raise RuntimeError("listener_unavailable")
        return await listener.accept()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        listener, self._listener = self._listener, None
        if listener is not None:
            await listener.aclose()


class _ProductionListeners:
    def __init__(self, binders: _ListenerBinders) -> None:
        self.control = _DeferredListener()
        self.secret = _DeferredListener()
        self.human = _DeferredListener()
        self._binders = binders
        self._bound = False

    async def recover_stale(self, service_lock: _ServiceLockAuthority) -> None:
        """Remove only fixed endpoints proven stale under lifecycle singleton authority."""

        for kind in (
            EndpointKind.CONTROL,
            EndpointKind.SECRET,
            EndpointKind.HUMAN_CONTROL,
        ):
            try:
                await remove_stale_endpoint(kind, service_lock)
            except LocalControlTransportError as exc:
                if exc.reason != "endpoint_missing":
                    raise

    async def bind(self, _instance: object) -> None:
        if self._bound:
            raise RuntimeError("listeners_already_bound")
        try:
            self.control.install(await self._binders.control())
            self.secret.install(await self._binders.secret())
            self.human.install(await self._binders.human())
            self._bound = True
        except BaseException:
            await self.close(_instance)
            raise

    async def close(self, _instance: object = None) -> None:
        failure: BaseException | None = None
        for listener in (self.human, self.secret, self.control):
            try:
                await listener.aclose()
            except BaseException as exc:
                if failure is None:
                    failure = exc
        if failure is not None:
            raise failure


class _InstallationStateStore:
    def __init__(self, path: Path, throttle_path: Path, generation_path: Path) -> None:
        self._path = path
        self._throttle_path = throttle_path
        self._generation_path = generation_path

    def load(self) -> _InstallationState | None:
        try:
            encoded = _read_private_file(self._path, _MAX_INSTALLATION_MARKER_BYTES)
        except FileNotFoundError:
            return None
        if not encoded.endswith(b"\n") or encoded.endswith(b"\n\n"):
            raise RuntimeError("installation_marker_invalid")
        try:
            value = strict_json_parse(encoded[:-1])
            if canonical_encode(value) != encoded[:-1] or type(value) is not dict:
                raise ValueError
            source = cast(dict[str, JsonValue], value)
            if set(source) != {
                "schema_version",
                "installation_id",
                "vault_mode",
                "root_envelope_base64",
                "mode_binding_digest",
                "record_digest",
            }:
                raise ValueError
            if source["schema_version"] != "1" or type(source["record_digest"]) is not str:
                raise ValueError
            body = dict(source)
            record_digest = cast(str, body.pop("record_digest"))
            if not hmac.compare_digest(record_digest, _installation_digest(body)):
                raise ValueError
            mode = VaultMode(cast(str, source["vault_mode"]))
            raw_envelope = source["root_envelope_base64"]
            envelope = None
            if raw_envelope is not None:
                if type(raw_envelope) is not str:
                    raise ValueError
                envelope = VaultRootEnvelope(base64.b64decode(raw_envelope, validate=True))
            state = _InstallationState(
                cast(str, source["installation_id"]),
                mode,
                envelope,
                cast(str, source["mode_binding_digest"]),
            )
            return state
        except Exception as exc:
            raise RuntimeError("installation_marker_invalid") from exc

    def select_installation_id(self, state: _InstallationState | None) -> str:
        candidates: list[str] = []
        if state is not None:
            candidates.append(state.installation_id)
        throttle = self._provisional_throttle()
        if throttle is not None:
            candidates.append(throttle.installation_id)
        generation_id = self._generation_installation_id()
        if generation_id is not None:
            candidates.append(generation_id)
        if candidates and len(set(candidates)) != 1:
            raise RuntimeError("installation_identity_mismatch")
        return candidates[0] if candidates else new_id(IdKind.INSTALLATION)

    def publish(
        self,
        mode: VaultMode,
        envelope: VaultRootEnvelope | None,
        mode_binding_digest: str,
    ) -> None:
        if self._path.exists():
            raise RuntimeError("installation_marker_exists")
        throttle = self._provisional_throttle()
        if mode is VaultMode.OS_KEYRING and throttle is not None:
            raise RuntimeError("installation_mode_ambiguous")
        if mode is VaultMode.PASSPHRASE and (
            throttle is None or throttle.record_digest != mode_binding_digest
        ):
            raise RuntimeError("installation_throttle_binding_invalid")
        installation_id = (
            throttle.installation_id if throttle is not None else self.select_installation_id(None)
        )
        state = _InstallationState(installation_id, mode, envelope, mode_binding_digest)
        body: dict[str, JsonValue] = {
            "schema_version": "1",
            "installation_id": state.installation_id,
            "vault_mode": state.vault_mode.value,
            "root_envelope_base64": (
                None
                if state.root_envelope is None
                else base64.b64encode(state.root_envelope.canonical_bytes).decode("ascii")
            ),
            "mode_binding_digest": state.mode_binding_digest,
        }
        body["record_digest"] = _installation_digest(body)
        _write_private_atomic(self._path, canonical_encode(body) + b"\n")

    def _provisional_throttle(self) -> UnlockThrottleRecord | None:
        try:
            encoded = _read_private_file(self._throttle_path, 16_384)
        except FileNotFoundError:
            return None
        try:
            return UnlockThrottleRecord.decode(encoded)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("installation_throttle_invalid") from exc

    def _generation_installation_id(self) -> str | None:
        try:
            encoded = _read_private_file(self._generation_path, 16_384)
        except FileNotFoundError:
            return None
        try:
            value = strict_json_parse(encoded[:-1])
            if (
                not encoded.endswith(b"\n")
                or encoded.endswith(b"\n\n")
                or canonical_encode(value) != encoded[:-1]
                or type(value) is not dict
            ):
                raise ValueError
            source = cast(dict[str, JsonValue], value)
            body = dict(source)
            record = body.pop("record_digest")
            expected = (
                "sha256:"
                + hashlib.sha256(_SERVICE_GENERATION_DOMAIN + canonical_encode(body)).hexdigest()
            )
            if type(record) is not str or not hmac.compare_digest(record, expected):
                raise ValueError
            installation_id = cast(str, source["installation_id"])
            validate_id(IdKind.INSTALLATION, installation_id)
            return installation_id
        except Exception as exc:
            raise RuntimeError("service_generation_invalid") from exc


class _LockedHumanEffects:
    def __init__(self, lifecycle: ServiceLifecycle, vault: VaultService) -> None:
        self._lifecycle = lifecycle
        self._vault = vault

    async def prepare(self, request: ClientOpenEnvelope) -> tuple[HumanPreview, str, int | None]:
        target = request.target
        mode = self._vault.mode.value
        if type(target) is EmptyVaultTarget:
            if target.expected_mode != mode:
                raise HumanControlError("target_invalid")
            digest = canonical_digest({"expected_mode": target.expected_mode, "kind": target.kind})
            if request.ceremony_kind is HumanCeremonyKind.VAULT_INITIALIZE:
                return VaultInitializePreview(), digest, None
            if request.ceremony_kind is HumanCeremonyKind.VAULT_UNLOCK:
                return VaultUnlockPreview(), digest, None
            if request.ceremony_kind is HumanCeremonyKind.KEYRING_RETRY:
                operation = "pristine_create" if mode == "uninitialized" else "existing_load"
                return KeyringRetryPreview(operation), digest, None
            raise HumanControlError("kind_forbidden")
        if type(target) is ProviderCredentialTarget:
            if not self._vault.ready or mode != "passphrase":
                raise HumanControlError("kind_forbidden")
            if request.ceremony_kind not in {
                HumanCeremonyKind.PROVIDER_CREDENTIAL_SET,
                HumanCeremonyKind.PROVIDER_CREDENTIAL_ROTATE,
            }:
                raise HumanControlError("kind_forbidden")
            if (request.ceremony_kind is HumanCeremonyKind.PROVIDER_CREDENTIAL_SET) != (
                target.action == "set"
            ):
                raise HumanControlError("target_invalid")
            digest = canonical_digest(
                {
                    "action": target.action,
                    "endpoint_profile_id": target.endpoint_profile_id,
                    "endpoint_profile_version": target.endpoint_profile_version,
                    "kind": target.kind,
                    "model_id": target.model_id,
                    "provider_id": target.provider_id,
                    "purpose": target.purpose,
                    "purpose_digest": target.purpose_digest,
                    "scope_digest": target.scope_digest,
                }
            )
            preview: HumanPreview
            if target.action == "set":
                preview = ProviderCredentialSetPreview(target)
            else:
                preview = ProviderCredentialRotatePreview(target)
            return preview, digest, None
        raise HumanControlError("kind_forbidden")

    async def complete_portable_recovery(
        self, target: PortableRecoveryTarget, secret: SecretHandle
    ) -> PortableRecoveryResult:
        del target, secret
        raise HumanControlError("kind_forbidden")

    async def store_provider_credential(
        self,
        target: ProviderCredentialTarget,
        secret: SecretHandle,
        proof: HumanAuthorizationProof,
        now_monotonic: float,
    ) -> ProviderCredentialResult:
        from yoetz.service.vault import ProviderCredentialBinding

        binding = ProviderCredentialBinding(
            target.provider_id,
            target.model_id,
            target.endpoint_profile_id,
            target.endpoint_profile_version,
            target.purpose,
            target.scope_digest,
            target.purpose_digest,
        )
        await self._vault.store_provider_credential(
            target.action, binding, secret, proof, now_monotonic
        )
        generation = self._vault._provider_generations.get(binding, 1)  # pyright: ignore[reportPrivateUsage]
        return ProviderCredentialResult(target.action, generation, "stored")

    async def decide_privacy(
        self,
        target: PrivacyPendingTarget,
        decision: str,
        proof: HumanAuthorizationProof | None,
        now_monotonic: float,
    ) -> PrivacyDecisionResult:
        del target, decision, proof, now_monotonic
        raise HumanControlError("kind_forbidden")

    async def change_idle_relock_policy(
        self,
        target: IdleRelockPolicyTarget,
        proof: HumanAuthorizationProof,
        now_monotonic: float,
    ) -> IdleRelockPolicyResult:
        del target, proof, now_monotonic
        raise HumanControlError("kind_forbidden")

    async def deny_idle_relock_policy(
        self, target: IdleRelockPolicyTarget, now_monotonic: float
    ) -> IdleRelockPolicyResult:
        del target, now_monotonic
        raise HumanControlError("kind_forbidden")


class _HumanConnectionServer:
    def __init__(self, service: HumanControlService) -> None:
        self._service = service

    async def __call__(self, stream: ControlStream) -> None:
        ceremony_id: str | None = None
        error_step: int | None = None
        try:
            opened = await _read_human_envelope(stream)
            if type(opened) is not ClientOpenEnvelope:
                raise HumanControlError("kind_forbidden")
            response: object = await self._service.open_ceremony(opened)
            ceremony_id = cast(str, getattr(response, "ceremony_id"))
            await _write_human_envelope(stream, response)
            while True:
                # ServerOpenedEnvelope (step 1) and later ServerPhaseEnvelope both carry
                # SecretRequiredPhase; the daemon must await YZS1 completion either way.
                phase = getattr(response, "phase", None)
                if isinstance(phase, SecretRequiredPhase):
                    error_step = cast(int, getattr(response, "step")) + 1
                    response = await self._service.secret_completed(
                        cast(str, getattr(response, "ceremony_id"))
                    )
                else:
                    incoming = await _read_human_envelope(stream)
                    if type(incoming) is ClientActionEnvelope:
                        ceremony_id = incoming.ceremony_id
                        error_step = incoming.step + 1
                        response = await self._service.submit_action(incoming)
                    elif type(incoming) is ClientCancelEnvelope:
                        ceremony_id = incoming.ceremony_id
                        error_step = incoming.step + 1
                        response = await self._service.cancel(incoming.ceremony_id)
                    else:
                        raise HumanControlError("kind_forbidden")
                await _write_human_envelope(stream, response)
                if type(response) is ServerResultEnvelope:
                    await _write_human_envelope(
                        stream,
                        ServerCloseEnvelope(response.ceremony_id, response.step + 1, "completed"),
                    )
                    return
                if type(response) is ServerCloseEnvelope:
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            code = _human_error_code(exc)
            error = ServerErrorEnvelope(code, False, ceremony_id, error_step)
            try:
                await _write_human_envelope(stream, error)
                if ceremony_id is not None and error_step is not None:
                    outcome = "cancelled" if code == "cancelled" else "failed"
                    await _write_human_envelope(
                        stream,
                        ServerCloseEnvelope(ceremony_id, error_step + 1, outcome),
                    )
            except Exception:
                pass
        finally:
            await stream.aclose()


async def _production_composition(
    *,
    _config: YoetzConfig | None = None,
    _paths: _ProductionPaths | None = None,
    _binders: _ListenerBinders | None = None,
    _ready_application_factory: _ReadyApplicationFactory | None = None,
) -> ServiceComposition:
    """Build the real locked-first service graph without opening ready-only state."""

    config = _config or load_config({}, os.environ, None)
    paths = _paths or _ProductionPaths.canonical(config)
    ensure_owner_only_dir(paths.bundle)
    marker_store = _InstallationStateStore(
        paths.bundle / _INSTALLATION_MARKER_NAME,
        paths.throttle,
        paths.generation,
    )
    marker = marker_store.load()
    installation_id = marker_store.select_installation_id(marker)
    clock = _SystemClock()
    production_binders = _binders is None
    listeners = _ProductionListeners(
        _binders
        or _ListenerBinders(
            bind_control_listener,
            bind_secret_listener,
            bind_human_control_listener,
        )
    )
    ready_close_relay = _ReadyCloseRelay()
    lifecycle = ServiceLifecycle(
        clock,
        generation_store=ServiceLifecycle.generation_store(paths.generation, installation_id),
        process_start_identity_commitment=("sha256:" + hashlib.sha256(os.urandom(32)).hexdigest()),
        singleton_lock_path=paths.singleton_lock,
        endpoint_recovery=listeners.recover_stale if production_binders else None,
        endpoint_publisher=listeners.bind,
        endpoint_cleanup=listeners.close,
        close_ready_composition=ready_close_relay,
    )
    secret_memory: LocalSecretMemory | None = None
    vault: VaultService | None = None
    try:
        instance = await lifecycle.acquire_singleton()
        secret_memory = LocalSecretMemory()
        mode = VaultMode.UNINITIALIZED if marker is None else marker.vault_mode
        vault = VaultService(
            installation_id=installation_id,
            service_generation=instance.generation,
            mode=mode,
            secret_memory=secret_memory,
            clock=clock,
            vault_store_factory=lambda: EncryptedVaultStore(paths.bundle / "vault"),
            root_envelope=None if marker is None else marker.root_envelope,
            user_presence_port=None,
            runtime_support={},
            pristine_state_digest=(
                canonical_digest({"installation_id": installation_id, "state": "pristine"})
                if marker is None
                else None
            ),
            publish_mode=marker_store.publish,
        )
        throttle = UnlockThrottleStore(
            paths.throttle,
            installation_id=installation_id,
            writer_instance_id=instance.instance_id,
            clock=clock,
        )
        if mode is VaultMode.PASSPHRASE:
            # mode_binding_digest is the *initial* throttle digest frozen at passphrase
            # publication (vault.md). Later unlock attempts advance record_digest, so restart
            # must not require equality with the live throttle record — only that passphrase
            # mode has a marker and the throttle store opens for this installation.
            if marker is None:
                raise RuntimeError("installation_throttle_binding_invalid")
            record = throttle.open_for_restart()
            if record.installation_id != installation_id:
                raise RuntimeError("installation_throttle_binding_invalid")
            auto_passphrase = AutoUnlockPassphraseStore(paths.bundle).load()
            if auto_passphrase is not None:
                try:
                    handle = secret_memory.capture(SecretPurpose.VAULT_UNLOCK, auto_passphrase)
                    await vault.unlock(handle)
                except Exception:
                    # A missing, locked, stale, or mismatched platform credential never prevents
                    # the service from starting in its ordinary locked state.
                    pass
                finally:
                    for index in range(len(auto_passphrase)):
                        auto_passphrase[index] = 0
        relay = _ReadyActivationRelay()
        secret_ingress = SecretIngressService(clock, secret_memory, listener=listeners.secret)
        diagnostics = _NullDiagnostics()
        ready_application_factory = (
            _ready_application_factory
            if _ready_application_factory is not None
            else cast(
                _ReadyApplicationFactory,
                build_ready_application_factory(
                    lifecycle=lifecycle,
                    vault=vault,
                    config=config,
                    paths=paths,
                    clock=clock,
                    secret_memory=secret_memory,
                    diagnostics=diagnostics,
                ),
            )
        )
        unlock = UnlockCoordinator(
            clock=clock,
            throttle=throttle,
            vault=vault,
            lifecycle=lifecycle,
            activate_ready=relay,
        )
        human = HumanControlService(
            clock=clock,
            lifecycle=lifecycle,
            vault=vault,
            unlock=unlock,
            secret_ingress=secret_ingress,
            effects=_LockedHumanEffects(lifecycle, vault),
            user_presence=None,
        )
        return ServiceComposition(
            lifecycle=lifecycle,
            control_listener=listeners.control,
            secret_ingress_listener=listeners.secret,
            human_control_listener=listeners.human,
            human_control_service=human,
            session_monitor=SessionEventMonitor(),
            vault=vault,
            ready_application_factory=ready_application_factory,
            secret_ingress_service=secret_ingress,
            unlock_service=unlock,
            secret_memory=secret_memory,
            diagnostics=diagnostics,
            human_connection_handler=_HumanConnectionServer(human),
            ready_activation_relay=relay,
            ready_close_relay=ready_close_relay,
        )
    except BaseException:
        await listeners.close()
        if vault is not None:
            await vault.close()
        if secret_memory is not None:
            secret_memory.close()
        try:
            lifecycle_state = lifecycle.state
        except LifecycleError:
            lifecycle_state = None
        if lifecycle_state is ServiceState.STARTING:
            await lifecycle.transition(ServiceState.FAILED)
        await lifecycle.close()
        raise


def _is_sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 71
        and value.startswith("sha256:")
        and all(character in "0123456789abcdef" for character in value[7:])
    )


def _installation_digest(body: dict[str, JsonValue]) -> str:
    return (
        "sha256:" + hashlib.sha256(_INSTALLATION_MARKER_DOMAIN + canonical_encode(body)).hexdigest()
    )


def _read_private_file(path: Path, maximum: int) -> bytes:
    facts = path.lstat()
    if (
        not stat.S_ISREG(facts.st_mode)
        or stat.S_ISLNK(facts.st_mode)
        or facts.st_nlink != 1
        or facts.st_uid != os.geteuid()
        or stat.S_IMODE(facts.st_mode) & 0o077
        or facts.st_size > maximum
    ):
        raise RuntimeError("private_state_unsafe")
    with path.open("rb") as source:
        encoded = source.read(maximum + 1)
    if len(encoded) > maximum:
        raise RuntimeError("private_state_too_large")
    return encoded


def _write_private_atomic(path: Path, encoded: bytes) -> None:
    ensure_owner_only_dir(path.parent)
    temporary = path.with_name(f".{path.name}.{os.urandom(12).hex()}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
        0o600,
    )
    try:
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short_write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


async def _read_human_envelope(stream: ControlStream) -> object:
    header = await _read_stream_exact(stream, _HUMAN_HEADER.size)
    magic, version, _frame_type, payload_size = _HUMAN_HEADER.unpack(header)
    if (
        magic != HUMAN_PROTOCOL_MAGIC
        or version != HUMAN_PROTOCOL_VERSION
        or payload_size > MAX_HUMAN_CONTROL_FRAME_BYTES
    ):
        raise HumanControlError("phase_invalid")
    payload = await _read_stream_exact(stream, payload_size)
    return decode_human_frame(header + payload)


async def _write_human_envelope(stream: ControlStream, envelope: object) -> None:
    await stream.send_all(encode_human_frame(envelope))  # pyright: ignore[reportArgumentType]


async def _read_stream_exact(stream: ControlStream, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = await stream.receive(remaining)
        if not chunk:
            raise HumanControlError("cancelled")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _human_error_code(exc: Exception) -> str:
    if type(exc) is HumanControlError:
        reason = exc.reason
        if reason in {
            "binding_expired",
            "cancelled",
            "kind_forbidden",
            "phase_invalid",
            "presence_unavailable",
            "reauthentication_unavailable",
            "replay",
            "secret_rejected",
            "stale_generation",
            "state_forbidden",
            "target_invalid",
        }:
            return reason
    return "internal_error"


async def run_service() -> Never:
    """Run one foreground service process until a signal requests bounded shutdown."""

    composition = await _production_composition()
    daemon = ServiceDaemon(_composition=composition)
    await daemon.serve()
    raise SystemExit(0)


def main() -> Never:
    """Synchronous installed entry point with one event-loop owner."""

    anyio.run(run_service)
    raise SystemExit(0)
