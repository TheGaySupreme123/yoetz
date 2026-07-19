"""Trusted per-user local-service orchestration and ordinary-control dispatch."""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Never, Protocol, cast

import anyio

from yoetz import __version__
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
from yoetz.service.control_protocol import (
    ControlProtocolError,
    ControlSession,
    ControlStream,
    parse_control_request,
    read_control_frame,
    server_handshake,
    validate_request,
    validate_result,
    write_control_frame,
)
from yoetz.service.lifecycle import (
    Admission,
    LifecycleError,
    ServiceLifecycle,
    SessionSecurityEvent,
)

__all__ = ["ServiceComposition", "ServiceDaemon", "main", "run_service"]

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


class _Listener(Protocol):
    async def accept(self) -> ControlStream: ...

    async def aclose(self) -> None: ...


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
    async def project_result_for_client(
        self,
        client_kind: ControlClientKind,
        method: ControlMethod,
        result: object,
    ) -> object: ...

    async def close(self) -> None: ...


class _Closable(Protocol):
    async def close(self) -> None: ...


type _HumanConnectionHandler = Callable[[ControlStream], Awaitable[None]]


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
    secret_ingress_service: _Closable | None = None
    human_connection_handler: _HumanConnectionHandler | None = None

    def __repr__(self) -> str:
        return "ServiceComposition(<redacted>)"


class ServiceDaemon:
    """Own lifecycle, listeners, dispatch admission, and bounded teardown."""

    def __init__(self, *, _composition: ServiceComposition) -> None:
        if type(_composition) is not ServiceComposition:
            raise TypeError("service_composition_invalid")
        self._composition = _composition
        self._application = _composition.application
        self._started = False
        self._closed = False
        self._stopping = False
        self._state_reason = "none"
        self._monitor_state = "unavailable"
        self._stop_event = asyncio.Event()
        self._start_lock = asyncio.Lock()
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

                if self._application is not None and self._composition.vault.ready:
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
                await self.close()
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
        try:
            await self._stop_event.wait()
        finally:
            await self.stop()
            for task in (idle, control, human):
                if task is not None:
                    task.cancel()
            await asyncio.gather(
                *(task for task in (idle, control, human) if task is not None),
                return_exceptions=True,
            )

    async def dispatch(
        self,
        client_kind: ControlClientKind,
        request: ControlCallRequest,
        *,
        _defer_stop: bool = False,
    ) -> ControlResult:
        """Validate, admit, execute, project, and correlate one ordinary call."""

        try:
            if type(client_kind) is not ControlClientKind:
                raise ControlError("method_forbidden")
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
                body = await self._dispatch_ready(client_kind, request)
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
        except ControlProtocolError, TypeError, ValueError:
            return self._error_result(request, ControlError("frame_invalid"))
        except Exception:
            return self._error_result(request, ControlError("internal_error"))

    async def lock(self, reason: str = "explicit_lock") -> None:
        """Drain the ready generation, close it, and remain structurally available."""

        if not self._started or self._closed:
            return
        lifecycle = self._composition.lifecycle
        if lifecycle.state is ServiceState.READY:
            await lifecycle.request_lock(reason)
        await self._close_ready()
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
            if self._started and lifecycle.state is not ServiceState.DRAINING:
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
        self, client_kind: ControlClientKind, request: ControlCallRequest
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
            self._validate_success_body(request, internal)
            if request.method in _PROJECTION_EXEMPT_METHODS:
                return internal
            projected = await application.project_result_for_client(
                client_kind, request.method, internal
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

    @staticmethod
    async def _run_handler(
        handler: Callable[[ControlStream], Awaitable[None]], stream: ControlStream
    ) -> None:
        await handler(stream)

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
        try:
            await self._composition.lifecycle.on_session_event(event)
        except LifecycleError:
            self._monitor_state = "lost"
            await self.lock("monitor_lost")
            return
        value = getattr(event, "value", "")
        if value in {"user_session_locked", "system_suspend", "monitor_lost"}:
            if value == "monitor_lost":
                self._monitor_state = "lost"
            await self._close_ready()
            self._state_reason = cast(str, value)

    async def _close_ready(self) -> None:
        application, self._application = self._application, None
        if application is not None:
            await application.close()
        await self._composition.vault.lock()

    async def _close_components(self) -> None:
        await self._close_ready()
        for component in (
            self._composition.human_control_service,
            self._composition.secret_ingress_service,
            self._composition.session_monitor,
        ):
            if component is not None:
                await component.close()
        for listener in (
            self._composition.human_control_listener,
            self._composition.secret_ingress_listener,
            self._composition.control_listener,
        ):
            if listener is not None:
                await listener.aclose()
        await self._composition.vault.close()

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


async def _production_composition() -> ServiceComposition:
    """Load the final production graph once its Wave-D application factory exists."""

    raise RuntimeError("ready_application_composition_unavailable")


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
