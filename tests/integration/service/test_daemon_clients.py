from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

import yoetz.service.daemon as daemon_module
from yoetz.application.service import (
    ClientProjectionContext,
    ControlProjectionBinding,
    ProjectedControlBody,
    ProjectionBindingFacts,
    ProjectionRenderMode,
)
from yoetz.config.models import LoggingConfig, YoetzConfig
from yoetz.domain.values import JsonObject
from yoetz.observability.logging import LogMode
from yoetz.ports.control import (
    ControlCallRequest,
    ControlClientKind,
    ControlError,
    ControlMethod,
    ServiceState,
)
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError
from yoetz.protocol.ids import IdKind, new_id
from yoetz.protocol.models import PublishWorkRequest, PublishWorkResult, StartRequest, StartResult
from yoetz.service.daemon import ServiceComposition, ServiceDaemon
from yoetz.service.lifecycle import LifecycleError, ServiceLifecycle
from yoetz.service.vault import VaultMode

_INSTANCE_ID = "svc_00000000-0000-4000-8000-000000000001"
_UUID = "00000000-0000-4000-8000-000000000002"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Clock:
    def now_utc(self) -> datetime:
        return datetime(2026, 7, 19, tzinfo=UTC)

    def monotonic_seconds(self) -> float:
        return 10.0


class _GenerationStore:
    def advance(self, instance_id: str) -> int:
        assert instance_id == _INSTANCE_ID
        return 7


class _Listener:
    def __init__(self) -> None:
        self.closed = False
        self._closed = asyncio.Event()

    async def accept(self) -> object:
        await self._closed.wait()
        raise RuntimeError("closed")

    async def aclose(self) -> None:
        self.closed = True
        self._closed.set()


@dataclass(frozen=True, slots=True)
class _Capability:
    active: bool = True


class _Monitor:
    capability = _Capability()

    def __init__(self) -> None:
        self.callback = None
        self.closed = False

    async def start(self, callback: object) -> None:
        self.callback = callback

    async def close(self) -> None:
        self.closed = True


class _Vault:
    mode = VaultMode.OS_KEYRING
    generation = 3
    ready = True

    def __init__(self) -> None:
        self.lock_count = 0
        self.close_count = 0

    async def lock(self) -> None:
        self.lock_count += 1
        self.ready = False

    async def close(self) -> None:
        self.close_count += 1
        self.ready = False


class _Application:
    def __init__(self) -> None:
        self.start_calls = 0
        self.publish_work_calls = 0
        self.projections: list[ClientProjectionContext] = []
        self.close_count = 0
        self.publish_work_error: PublicOperationError | None = None

    async def start(self, request: object) -> StartResult:
        assert isinstance(request, StartRequest)
        self.start_calls += 1
        await asyncio.sleep(0)
        return StartResult.model_validate(
            {
                "protocol_version": "0.1",
                "schema_version": "1.0.0",
                "request_id": request.request_id,
                "ok": False,
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": "The request is invalid.",
                    "retryable": False,
                    "correlation_id": f"err_{_UUID}",
                },
            }
        )

    async def publish_work(self, request: object) -> PublishWorkResult:
        assert isinstance(request, PublishWorkRequest)
        self.publish_work_calls += 1
        await asyncio.sleep(0)
        if self.publish_work_error is not None:
            raise self.publish_work_error
        raise AssertionError("publish_work_error_required")

    async def review(self, request: object) -> JsonObject:
        assert isinstance(request, JsonObject)
        return JsonObject({"accepted": True})

    async def projection_binding_facts(
        self,
        method: ControlMethod,
        request: object,
        result: object,
    ) -> ProjectionBindingFacts:
        del method, result
        request_id = getattr(request, "request_id", None)
        return ProjectionBindingFacts(request_id if type(request_id) is str else None, None)

    async def project_result_for_client(
        self,
        context: ClientProjectionContext,
        binding: ControlProjectionBinding,
        result: object,
    ) -> ProjectedControlBody:
        method = binding.method
        assert method in {ControlMethod.START, ControlMethod.REVIEW, ControlMethod.PUBLISH_WORK}
        self.projections.append(context)
        assert isinstance(result, StartResult | PublishWorkResult | JsonObject)
        return result

    async def close(self) -> None:
        self.close_count += 1


def _start_body() -> StartRequest:
    return StartRequest.model_validate(
        {
            "protocol_version": "0.1",
            "schema_version": "1.0.0",
            "request_id": f"req_{_UUID}",
            "actor": {"actor_id": "harness:test", "actor_type": "harness"},
            "client": {
                "kind": "test_client",
                "version": "0.1.0",
                "integration": "local_cli",
            },
            "mode": "create",
            "task_title": "shared service",
            "requested_view": "compact",
        }
    )


def _publish_work_body() -> PublishWorkRequest:
    return PublishWorkRequest.model_validate(
        {
            "protocol_version": "0.1",
            "schema_version": "1.0.0",
            "request_id": "req_00000000-0000-4000-8000-000000000010",
            "session_id": "ses_00000000-0000-4000-8000-000000000011",
            "writer_id": "wri_00000000-0000-4000-8000-000000000012",
            "expected_frontier": {"sequence": "0", "head_digest": "genesis"},
            "event_drafts": [
                {
                    "event_id": "evt_00000000-0000-4000-8000-000000000013",
                    "schema": {"name": "plan_published", "version": "1.0.0"},
                    "occurred_at": "2026-01-01T00:00:00.000Z",
                    # Deliberately unsorted: application rejects with unsorted_set_field.
                    "causal_parents": [
                        "evt_00000000-0000-4000-8000-000000000002",
                        "evt_00000000-0000-4000-8000-000000000001",
                    ],
                    "payload": {
                        "plan_version": 1,
                        "summary": "Plan",
                        "obligation_refs": [],
                    },
                    "artifact_refs": [],
                    "evidence_refs": [],
                }
            ],
            "actor": {"actor_id": "harness:test", "actor_type": "harness"},
            "client": {
                "kind": "test_client",
                "version": "0.1.0",
                "integration": "local_cli",
            },
        }
    )


def _request(daemon: ServiceDaemon, method: ControlMethod, body: object) -> ControlCallRequest:
    instance = daemon.composition.lifecycle.instance
    return ControlCallRequest(
        kind="call",
        protocol_version="1.0",
        rpc_id=new_id(IdKind.CONTROL_RPC),
        service_instance_id=instance.instance_id,
        service_generation=str(instance.generation),
        method=method,
        body=body,  # pyright: ignore[reportArgumentType]
    )


def _daemon() -> tuple[ServiceDaemon, _Application, _Vault, _Listener]:
    application = _Application()
    vault = _Vault()
    listener = _Listener()
    lifecycle = ServiceLifecycle(
        _Clock(),
        generation_store=_GenerationStore(),
        process_start_identity_commitment="sha256:" + "1" * 64,
        instance_id=_INSTANCE_ID,
    )
    composition = ServiceComposition(
        lifecycle=lifecycle,
        control_listener=listener,  # pyright: ignore[reportArgumentType]
        secret_ingress_listener=None,
        human_control_listener=None,
        human_control_service=None,
        session_monitor=_Monitor(),  # pyright: ignore[reportArgumentType]
        vault=vault,
        application=application,
    )
    return ServiceDaemon(_composition=composition), application, vault, listener


def _locked_daemon(
    root: Path,
    factory: object,
) -> tuple[ServiceDaemon, _Vault]:
    vault = _Vault()
    vault.ready = False
    lifecycle = ServiceLifecycle(
        _Clock(),
        generation_store=_GenerationStore(),
        process_start_identity_commitment="sha256:" + "2" * 64,
        instance_id=_INSTANCE_ID,
        singleton_lock_path=root / "service.lock",
    )
    composition = ServiceComposition(
        lifecycle=lifecycle,
        control_listener=_Listener(),  # pyright: ignore[reportArgumentType]
        secret_ingress_listener=None,
        human_control_listener=None,
        human_control_service=None,
        session_monitor=None,
        vault=vault,
        ready_application_factory=factory,  # pyright: ignore[reportArgumentType]
    )
    return ServiceDaemon(_composition=composition), vault


@pytest.mark.anyio
async def test_cli_mcp_and_ui_share_one_ready_application_and_projection() -> None:
    daemon, application, _vault, _listener = _daemon()
    await daemon.start()
    body = _start_body()
    kinds = (ControlClientKind.CLI, ControlClientKind.MCP_BRIDGE, ControlClientKind.UI)
    results = await asyncio.gather(
        *(daemon.dispatch(kind, _request(daemon, ControlMethod.START, body)) for kind in kinds)
    )
    assert all(result.outcome == "ok" for result in results)
    assert application.start_calls == 3
    assert application.projections == [ClientProjectionContext.fail_safe(kind) for kind in kinds]


@pytest.mark.anyio
async def test_public_operation_error_surfaces_as_ok_false_not_internal_error() -> None:
    daemon, application, _vault, _listener = _daemon()
    await daemon.start()
    application.publish_work_error = PublicOperationError(
        PublicErrorCode.EVENT_INVALID,
        "The event batch is invalid.",
        False,
        safe_details={"reason_code": "unsorted_set_field"},
    )
    body = _publish_work_body()

    result = await daemon.dispatch(
        ControlClientKind.MCP_BRIDGE,
        _request(daemon, ControlMethod.PUBLISH_WORK, body),
    )

    assert result.outcome == "ok"
    assert isinstance(result.body, PublishWorkResult)
    assert result.body.root.ok is False
    assert result.body.root.error.code is PublicErrorCode.EVENT_INVALID
    assert result.body.root.error.safe_details == {"reason_code": "unsorted_set_field"}
    assert result.body.root.request_id == body.request_id
    assert result.body.root.error.correlation_id.startswith("err_")
    assert application.publish_work_calls == 1
    assert application.projections == []
    assert not isinstance(result.body, ControlError)
    await daemon.close()


@pytest.mark.anyio
async def test_dispatch_passes_trusted_human_projection_context() -> None:
    daemon, application, _vault, _listener = _daemon()
    await daemon.start()
    body = _start_body()
    context = ClientProjectionContext(
        ControlClientKind.CLI,
        ProjectionRenderMode.HUMAN_READABLE,
        True,
    )

    result = await daemon.dispatch(
        ControlClientKind.CLI,
        _request(daemon, ControlMethod.START, body),
        projection_context=context,
    )

    assert result.outcome == "ok"
    assert application.projections == [context]

    cross_paired = await daemon.dispatch(
        ControlClientKind.UI,
        _request(daemon, ControlMethod.START, body),
        projection_context=context,
    )
    assert cross_paired.outcome == "error"
    assert isinstance(cross_paired.body, ControlError)
    assert cross_paired.body.reason == "frame_invalid"
    assert application.projections == [context]
    assert daemon.status().state is ServiceState.READY
    await daemon.close()


@pytest.mark.anyio
async def test_client_kind_and_state_admission_fail_closed() -> None:
    daemon, application, vault, _listener = _daemon()
    await daemon.start()
    forbidden = await daemon.dispatch(
        ControlClientKind.MCP_BRIDGE,
        _request(daemon, ControlMethod.SERVICE_STATUS, JsonObject({})),
    )
    assert forbidden.outcome == "error"
    assert isinstance(forbidden.body, ControlError)
    assert forbidden.body.reason == "method_forbidden"

    locked = await daemon.dispatch(
        ControlClientKind.CLI,
        _request(daemon, ControlMethod.SERVICE_LOCK, JsonObject({})),
    )
    assert locked.outcome == "ok"
    assert daemon.status().state is ServiceState.LOCKED
    assert application.close_count == 1
    assert vault.lock_count >= 1

    rejected = await daemon.dispatch(
        ControlClientKind.UI,
        _request(daemon, ControlMethod.START, _start_body()),
    )
    assert rejected.outcome == "error"
    assert isinstance(rejected.body, ControlError)
    assert rejected.body.reason == "vault_locked"
    await daemon.close()


@pytest.mark.anyio
async def test_shutdown_is_idempotent_and_closes_owned_listener() -> None:
    daemon, application, vault, listener = _daemon()
    await daemon.start()
    await daemon.stop()
    await daemon.stop()
    assert listener.closed
    assert application.close_count == 1
    assert vault.close_count == 1


@pytest.mark.anyio
async def test_unlock_activation_constructs_exact_generation_once(tmp_path: Path) -> None:
    application = _Application()
    calls: list[tuple[int, int]] = []

    async def factory(service_generation: int, vault_generation: int) -> _Application:
        calls.append((service_generation, vault_generation))
        return application

    daemon, vault = _locked_daemon(tmp_path, factory)
    await daemon.start()
    assert daemon.status().state is ServiceState.LOCKED
    await daemon.composition.lifecycle.transition(ServiceState.UNLOCKING)
    vault.ready = True

    await daemon.activate_ready_application(7, 3)

    assert calls == [(7, 3)]
    assert daemon.status().state is ServiceState.READY
    assert daemon.status().state_reason == "none"
    await daemon.lock()
    assert application.close_count == 1
    assert not vault.ready
    await daemon.close()


@pytest.mark.anyio
async def test_preunlocked_vault_activates_ready_application_on_daemon_start(
    tmp_path: Path,
) -> None:
    application = _Application()
    calls: list[tuple[int, int]] = []

    async def factory(service_generation: int, vault_generation: int) -> _Application:
        calls.append((service_generation, vault_generation))
        return application

    daemon, vault = _locked_daemon(tmp_path, factory)
    vault.ready = True

    await daemon.start()

    assert calls == [(7, 3)]
    assert daemon.status().state is ServiceState.READY
    await daemon.close()


@pytest.mark.anyio
async def test_locked_passphrase_vault_never_reports_keyring_locked(tmp_path: Path) -> None:
    async def factory(_service_generation: int, _vault_generation: int) -> _Application:
        return _Application()

    daemon, vault = _locked_daemon(tmp_path, factory)
    vault.mode = VaultMode.PASSPHRASE

    await daemon.start()

    assert daemon.status().state is ServiceState.LOCKED
    assert daemon.status().state_reason == "passphrase_required"
    await daemon.close()


@pytest.mark.anyio
async def test_unlock_activation_revalidates_after_factory_and_closes_partial(
    tmp_path: Path,
) -> None:
    application = _Application()
    daemon: ServiceDaemon
    vault: _Vault

    async def factory(_service_generation: int, _vault_generation: int) -> _Application:
        vault.generation = 4
        return application

    daemon, vault = _locked_daemon(tmp_path, factory)
    await daemon.start()
    await daemon.composition.lifecycle.transition(ServiceState.UNLOCKING)
    vault.ready = True

    with pytest.raises(LifecycleError, match="invalid_transition"):
        await daemon.activate_ready_application(7, 3)

    assert application.close_count == 1
    assert not vault.ready
    assert daemon.status().state is ServiceState.LOCKED
    assert daemon.status().state_reason == "unlock_failed"
    await daemon.close()


@pytest.mark.anyio
async def test_lock_serializes_with_in_flight_ready_activation(tmp_path: Path) -> None:
    application = _Application()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def factory(_service_generation: int, _vault_generation: int) -> _Application:
        entered.set()
        await release.wait()
        return application

    daemon, vault = _locked_daemon(tmp_path, factory)
    await daemon.start()
    await daemon.composition.lifecycle.transition(ServiceState.UNLOCKING)
    vault.ready = True
    activation = asyncio.create_task(daemon.activate_ready_application(7, 3))
    await entered.wait()
    locking = asyncio.create_task(daemon.lock("explicit_lock"))
    await asyncio.sleep(0)
    assert not locking.done()

    release.set()
    await activation
    await locking

    assert daemon.status().state is ServiceState.LOCKED
    assert application.close_count == 1
    assert not vault.ready
    await daemon.close()


@pytest.mark.anyio
async def test_production_composition_starts_locked_before_ready_only_state(
    tmp_path: Path,
) -> None:
    root = tmp_path / "data"
    metadata = tmp_path / "state"
    paths = daemon_module._ProductionPaths(  # pyright: ignore[reportPrivateUsage]
        root,
        metadata / "service-generation.json",
        metadata / "unlock-throttle.json",
        metadata / "service.lock",
    )
    bound: list[str] = []

    async def bind(kind: str) -> _Listener:
        bound.append(kind)
        return _Listener()

    binders = daemon_module._ListenerBinders(  # pyright: ignore[reportPrivateUsage]
        lambda: bind("control"),  # pyright: ignore[reportArgumentType]
        lambda: bind("secret"),  # pyright: ignore[reportArgumentType]
        lambda: bind("human"),  # pyright: ignore[reportArgumentType]
    )
    composition = await daemon_module._production_composition(  # pyright: ignore[reportPrivateUsage]
        _config=YoetzConfig(),
        _paths=paths,
        _binders=binders,
    )
    daemon = ServiceDaemon(_composition=composition)

    await daemon.start()

    assert bound == ["control", "secret", "human"]
    assert daemon.status().state is ServiceState.LOCKED
    assert daemon.status().vault_mode == "uninitialized"
    assert daemon.status().capabilities == ("confidential_ingress",)
    assert not (root / "vault").exists()
    await daemon.close()


def test_installation_marker_round_trip_is_canonical_and_self_authenticated(
    tmp_path: Path,
) -> None:
    root = tmp_path / "data"
    root.mkdir(mode=0o700)
    marker_path = root / "installation-state.json"
    store = daemon_module._InstallationStateStore(  # pyright: ignore[reportPrivateUsage]
        marker_path,
        tmp_path / "unlock-throttle.json",
        tmp_path / "service-generation.json",
    )
    digest = "sha256:" + "3" * 64

    store.publish(VaultMode.OS_KEYRING, None, digest)
    loaded = store.load()

    assert loaded is not None
    assert loaded.vault_mode is VaultMode.OS_KEYRING
    assert loaded.mode_binding_digest == digest
    assert marker_path.read_bytes().endswith(b"\n")
    assert marker_path.stat().st_mode & 0o077 == 0

    corrupted = bytearray(marker_path.read_bytes())
    digest_start = corrupted.rfind(b"sha256:") + len(b"sha256:")
    corrupted[digest_start] = ord("0") if corrupted[digest_start] != ord("0") else ord("1")
    marker_path.write_bytes(corrupted)
    with pytest.raises(RuntimeError, match="installation_marker_invalid"):
        store.load()


@pytest.mark.anyio
async def test_service_entry_point_installs_the_structural_log_sink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unexpected-dispatch correlation ids are useless unless this sink is installed."""

    installed: list[LogMode] = []

    class _Daemon:
        def __init__(self, *, _composition: object) -> None:
            del _composition

        async def serve(self) -> None:
            return None

    async def composition(*, _config: YoetzConfig) -> object:
        assert type(_config) is YoetzConfig
        return object()

    def load(
        overrides: Mapping[str, str], env: Mapping[str, str], path: Path | None
    ) -> YoetzConfig:
        del overrides, env, path
        return YoetzConfig()

    def configure(config: LoggingConfig, mode: LogMode) -> None:
        del config
        installed.append(mode)

    monkeypatch.setattr(daemon_module, "load_config", load)
    monkeypatch.setattr(daemon_module, "configure_logging", configure)
    monkeypatch.setattr(daemon_module, "_production_composition", composition)
    monkeypatch.setattr(daemon_module, "ServiceDaemon", _Daemon)

    with pytest.raises(SystemExit):
        await daemon_module.run_service()

    assert installed == [LogMode.SERVICE]
