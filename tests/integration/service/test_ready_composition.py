from __future__ import annotations

import asyncio
import hashlib
import hmac
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast

import apsw
import pytest

import yoetz.adapters.sqlite.connection as connection_module
import yoetz.service.ready_composition as ready_composition_module
from yoetz.adapters.keys.encrypted_vault import EncryptedVaultStore
from yoetz.adapters.keys.secret_memory import LocalSecretMemory
from yoetz.adapters.sqlite.connection import open_catalog_writer
from yoetz.adapters.sqlite.migrations import initialize_catalog
from yoetz.application.service import ClientProjectionContext, ControlProjectionBinding
from yoetz.config.models import YoetzConfig
from yoetz.ports.control import (
    ControlCallRequest,
    ControlClientKind,
    ControlMethod,
    ServiceState,
)
from yoetz.ports.diagnostics import StartupCheckResult
from yoetz.ports.secret_memory import SecretPurpose
from yoetz.protocol.canonical import canonical_encode
from yoetz.protocol.ids import IdKind, new_id
from yoetz.protocol.models import StartRequest, StartResult
from yoetz.service.daemon import ServiceComposition, ServiceDaemon
from yoetz.service.lifecycle import ServiceLifecycle
from yoetz.service.ready_composition import (
    IdPort,
    build_privacy_coordinator,
    build_ready_application_factory,
    open_ready_catalog,
)
from yoetz.service.vault import VaultMode, VaultService

_INSTALLATION_ID = "ins_00000000-0000-4000-8000-000000000001"
_INSTANCE_ID = "svc_00000000-0000-4000-8000-000000000002"


class _SupportPolicyFactory(Protocol):
    def __call__(
        self,
        *,
        manifest_id: str,
        required_options: frozenset[str],
        denied_options: frozenset[str],
    ) -> object: ...


class _Clock:
    def now_utc(self) -> datetime:
        return datetime(2026, 7, 21, 18, 0, 0, tzinfo=UTC)

    def monotonic_seconds(self) -> float:
        return 1.0


class _GenerationStore:
    def advance(self, instance_id: str) -> int:
        assert instance_id == _INSTANCE_ID
        return 1


class _Lookup:
    def mac(self, domain: bytes, message: bytes) -> str:
        return (
            "hmac-sha256:"
            + hmac.new(
                b"catalog-test-key",
                domain + message,
                hashlib.sha256,
            ).hexdigest()
        )


def _accept_private_path(_path: Path) -> None:
    return None


class _Paths:
    def __init__(self, bundle: Path) -> None:
        self._bundle = bundle

    @property
    def bundle(self) -> Path:
        return self._bundle


class _Diagnostics:
    def record(self, result: StartupCheckResult) -> None:
        assert type(result) is StartupCheckResult


class _Listener:
    def __init__(self) -> None:
        self.closed = asyncio.Event()

    async def accept(self) -> object:
        await self.closed.wait()
        raise RuntimeError("closed")

    async def aclose(self) -> None:
        self.closed.set()


@pytest.fixture(autouse=True)
def _sqlite_policy(  # pyright: ignore[reportUnusedFunction]
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    monkeypatch.setattr(connection_module, "verify_private_local_bundle", _accept_private_path)
    monkeypatch.setattr(
        ready_composition_module,
        "verify_private_local_bundle",
        _accept_private_path,
    )
    factory = cast(_SupportPolicyFactory, getattr(connection_module, "_SqliteSupportPolicy"))
    installer = cast(
        Callable[[object | None], None], getattr(connection_module, "_install_support_policy")
    )
    db = apsw.Connection(":memory:")
    try:
        raw_options: object = db.pragma("compile_options")
    finally:
        db.close()
    assert type(raw_options) is list
    items = cast(list[object], raw_options)
    assert all(type(item) is str for item in items)
    installer(
        factory(
            manifest_id="test-ready-composition-runtime-support",
            required_options=frozenset(cast(list[str], items)),
            denied_options=frozenset({"OMIT_FOREIGN_KEY", "OMIT_WAL", "THREADSAFE=0"}),
        )
    )
    yield
    installer(None)


def test_open_catalog_writer_allows_unfenced_catalog_initialization(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    catalog_path = tmp_path / "catalog.sqlite3"

    db = open_catalog_writer(catalog_path)
    try:
        initialize_catalog(db)
        with db:
            db.execute(
                "INSERT INTO catalog_meta(key, value) VALUES ('installation_id', ?)",
                (_INSTALLATION_ID,),
            )
            db.execute("INSERT INTO catalog_meta(key, value) VALUES ('owner_generation', '7')")
        row = db.execute("SELECT value FROM catalog_meta WHERE key='owner_generation'").fetchone()
        assert row == ("7",)
    finally:
        db.close(force=True)


@pytest.mark.anyio
async def test_build_privacy_coordinator_reuses_durable_seed_on_second_ready(
    tmp_path: Path,
) -> None:
    tmp_path.chmod(0o700)
    clock = _Clock()
    memory = LocalSecretMemory()
    vault = VaultService(
        installation_id=_INSTALLATION_ID,
        service_generation=1,
        mode=VaultMode.UNINITIALIZED,
        secret_memory=memory,
        clock=clock,
        vault_store_factory=lambda: EncryptedVaultStore(tmp_path / "vault"),
        pristine_state_digest="sha256:" + "7" * 64,
    )
    initialize = memory.capture(SecretPurpose.VAULT_INITIALIZE, bytearray(b"correct horse battery"))
    await vault.initialize_passphrase(initialize, "sha256:" + "8" * 64)
    catalog_path = tmp_path / "catalog.sqlite3"
    db = open_catalog_writer(catalog_path)
    try:
        initialize_catalog(db)
        with db:
            db.execute(
                "INSERT INTO catalog_meta(key, value) VALUES ('installation_id', ?)",
                (_INSTALLATION_ID,),
            )
            db.execute("INSERT INTO catalog_meta(key, value) VALUES ('owner_generation', '1')")
        first = await build_privacy_coordinator(
            catalog_db=db,
            installation_id=_INSTALLATION_ID,
            service_generation=1,
            vault_generation=vault.generation,
            vault=vault,
            clock=clock,
            ids=IdPort(),
        )
        second = await build_privacy_coordinator(
            catalog_db=db,
            installation_id=_INSTALLATION_ID,
            service_generation=2,
            vault_generation=vault.generation,
            vault=vault,
            clock=clock,
            ids=IdPort(),
        )
        assert first[1].policy_id == second[1].policy_id
        assert first[1].created_at == second[1].created_at
        assert first[1].policy_digest == second[1].policy_digest
    finally:
        db.close(force=True)
        await vault.close()
        memory.close()


@pytest.mark.anyio
async def test_open_ready_catalog_seeds_generation_property(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    catalog = await open_ready_catalog(
        tmp_path / "catalog.sqlite3",
        installation_id=_INSTALLATION_ID,
        service_generation=11,
        lookup=_Lookup(),
        clock=_Clock(),
        ids=IdPort(),
    )
    try:
        assert catalog.generation == 11
        row = catalog._db.execute(  # pyright: ignore[reportPrivateUsage]
            "SELECT value FROM catalog_meta WHERE key='installation_id'"
        ).fetchone()
        assert row == (_INSTALLATION_ID,)
    finally:
        catalog._db.close(force=True)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.anyio
async def test_ready_factory_installs_application_that_starts(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    clock = _Clock()
    memory = LocalSecretMemory()
    lifecycle = ServiceLifecycle(
        clock,
        generation_store=_GenerationStore(),
        process_start_identity_commitment="sha256:" + "1" * 64,
        instance_id=_INSTANCE_ID,
    )
    await lifecycle.acquire_singleton()
    await lifecycle.transition(ServiceState.LOCKED)
    vault = VaultService(
        installation_id=_INSTALLATION_ID,
        service_generation=1,
        mode=VaultMode.UNINITIALIZED,
        secret_memory=memory,
        clock=clock,
        vault_store_factory=lambda: EncryptedVaultStore(tmp_path / "vault"),
        pristine_state_digest="sha256:" + "2" * 64,
    )
    initialize = memory.capture(SecretPurpose.VAULT_INITIALIZE, bytearray(b"correct horse battery"))
    await vault.initialize_passphrase(initialize, "sha256:" + "3" * 64)
    app = None
    try:
        factory = build_ready_application_factory(
            lifecycle=lifecycle,
            vault=vault,
            config=YoetzConfig(),
            paths=_Paths(tmp_path),
            clock=clock,
            secret_memory=memory,
            diagnostics=_Diagnostics(),
        )
        app = await factory(1, vault.generation)
        request = StartRequest.model_validate(
            {
                "protocol_version": "0.1",
                "schema_version": "1.0.0",
                "request_id": "req_00000000-0000-4000-8000-000000000003",
                "mode": "create",
                "task_title": "Make documentation fully consistent",
                "actor": {"actor_id": "harness:pytest", "actor_type": "harness"},
                "client": {
                    "kind": "codex_cli",
                    "version": "0.144.6",
                    "integration": "local_cli",
                },
                "requested_view": "compact",
            }
        )
        result = await app.start(request)
        rpc_id = new_id(IdKind.CONTROL_RPC)
        facts = await app.projection_binding_facts(ControlMethod.START, request, result)
        binding = ControlProjectionBinding(
            rpc_id=rpc_id,
            method=ControlMethod.START,
            service_instance_id=_INSTANCE_ID,
            service_generation=1,
            original_request_id=facts.original_request_id,
            route_identity_digest=facts.route_identity_digest,
            control_request_canonical=canonical_encode(
                {
                    "method": ControlMethod.START.value,
                    "rpc_id": rpc_id,
                    "service_generation": "1",
                    "service_instance_id": _INSTANCE_ID,
                }
            ),
        )
        projected = await app.project_result_for_client(
            ClientProjectionContext.fail_safe(ControlClientKind.CLI),
            binding,
            result,
        )

        assert result.ok is True
        assert result.outcome == "created"
        assert result.frontier.sequence == "1"
        assert isinstance(projected, StartResult)
        assert projected.root.ok is True
        assert projected.root.request_id == request.request_id
    finally:
        if app is not None:
            await app.close()
        await vault.close()
        memory.close()
        await lifecycle.close()


@pytest.mark.anyio
async def test_daemon_unlock_installs_real_application_and_dispatches_start(
    tmp_path: Path,
) -> None:
    tmp_path.chmod(0o700)
    clock = _Clock()
    memory = LocalSecretMemory()
    lifecycle = ServiceLifecycle(
        clock,
        generation_store=_GenerationStore(),
        process_start_identity_commitment="sha256:" + "4" * 64,
        instance_id=_INSTANCE_ID,
    )
    vault = VaultService(
        installation_id=_INSTALLATION_ID,
        service_generation=1,
        mode=VaultMode.UNINITIALIZED,
        secret_memory=memory,
        clock=clock,
        vault_store_factory=lambda: EncryptedVaultStore(tmp_path / "vault"),
        pristine_state_digest="sha256:" + "5" * 64,
    )
    initialize = memory.capture(SecretPurpose.VAULT_INITIALIZE, bytearray(b"correct horse battery"))
    await vault.initialize_passphrase(initialize, "sha256:" + "6" * 64)
    await vault.lock()
    diagnostics = _Diagnostics()
    factory = build_ready_application_factory(
        lifecycle=lifecycle,
        vault=vault,
        config=YoetzConfig(),
        paths=_Paths(tmp_path),
        clock=clock,
        secret_memory=memory,
        diagnostics=diagnostics,
    )
    daemon = ServiceDaemon(
        _composition=ServiceComposition(
            lifecycle=lifecycle,
            control_listener=_Listener(),  # pyright: ignore[reportArgumentType]
            secret_ingress_listener=None,
            human_control_listener=None,
            human_control_service=None,
            session_monitor=None,
            vault=vault,
            ready_application_factory=factory,  # pyright: ignore[reportArgumentType]
            secret_memory=memory,
            diagnostics=diagnostics,
        )
    )
    try:
        await daemon.start()
        assert daemon.status().state is ServiceState.LOCKED
        await lifecycle.transition(ServiceState.UNLOCKING)
        unlock = memory.capture(SecretPurpose.VAULT_UNLOCK, bytearray(b"correct horse battery"))
        await vault.unlock(unlock)
        await daemon.activate_ready_application(1, vault.generation)

        instance = daemon.composition.lifecycle.instance
        body = StartRequest.model_validate(
            {
                "protocol_version": "0.1",
                "schema_version": "1.0.0",
                "request_id": "req_00000000-0000-4000-8000-000000000004",
                "mode": "create",
                "task_title": "Make documentation fully consistent",
                "actor": {"actor_id": "harness:pytest", "actor_type": "harness"},
                "client": {
                    "kind": "codex_cli",
                    "version": "0.144.6",
                    "integration": "local_cli",
                },
                "requested_view": "compact",
            }
        )
        result = await daemon.dispatch(
            ControlClientKind.CLI,
            ControlCallRequest(
                kind="call",
                protocol_version="1.0",
                rpc_id=new_id(IdKind.CONTROL_RPC),
                service_instance_id=instance.instance_id,
                service_generation=str(instance.generation),
                method=ControlMethod.START,
                body=body,
            ),
        )

        assert result.outcome == "ok"
        assert isinstance(result.body, StartResult)
        assert result.body.root.ok is True
        assert result.body.root.request_id == body.request_id
        assert daemon.status().state is ServiceState.READY
    finally:
        await daemon.close()
