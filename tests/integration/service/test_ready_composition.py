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
from yoetz.protocol.canonical import JsonValue, canonical_encode
from yoetz.protocol.ids import IdKind, new_id
from yoetz.protocol.models import (
    CheckRequest,
    CheckResult,
    PublishWorkRequest,
    StartRequest,
    StartResult,
)
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
async def test_ready_factory_completes_and_projects_deterministic_check(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    clock = _Clock()
    memory = LocalSecretMemory()
    lifecycle = ServiceLifecycle(
        clock,
        generation_store=_GenerationStore(),
        process_start_identity_commitment="sha256:" + "7" * 64,
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
        pristine_state_digest="sha256:" + "8" * 64,
    )
    initialize = memory.capture(SecretPurpose.VAULT_INITIALIZE, bytearray(b"correct horse battery"))
    await vault.initialize_passphrase(initialize, "sha256:" + "9" * 64)
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
        common = {
            "protocol_version": "0.1",
            "schema_version": "1.0.0",
            "actor": {"actor_id": "harness:pytest", "actor_type": "harness"},
            "client": {
                "kind": "cooperative_agent",
                "version": "0.1.0",
                "integration": "cooperative_mcp",
            },
        }
        start_request = StartRequest.model_validate(
            {
                **common,
                "request_id": "req_00000000-0000-4000-8000-000000000101",
                "mode": "create",
                "task_title": "Exercise the ready check path",
                "requested_view": "compact",
            }
        )
        started = await app.start(start_request)
        assert started.ok is True
        frontier = started.frontier

        batches: tuple[tuple[str, list[JsonValue]], ...] = (
            (
                "req_00000000-0000-4000-8000-000000000102",
                [
                    {
                        "event_id": "evt_00000000-0000-4000-8000-000000000201",
                        "schema": {"name": "plan_published", "version": "1.0.0"},
                        "occurred_at": "2026-07-21T18:00:00.000Z",
                        "causal_parents": [],
                        "payload": {
                            "plan_version": 1,
                            "summary": "Exercise the ready composition through a closed check.",
                            "obligation_refs": [],
                        },
                        "artifact_refs": [],
                        "evidence_refs": [],
                    }
                ],
            ),
            (
                "req_00000000-0000-4000-8000-000000000103",
                [
                    {
                        "event_id": "evt_00000000-0000-4000-8000-000000000202",
                        "schema": {"name": "action_recorded", "version": "1.0.0"},
                        "occurred_at": "2026-07-21T18:01:00.000Z",
                        "causal_parents": ["evt_00000000-0000-4000-8000-000000000201"],
                        "payload": {
                            "action_id": "act_00000000-0000-4000-8000-000000000201",
                            "action_kind": "review",
                            "description": "Ran the ready-composition acceptance path.",
                        },
                        "artifact_refs": [],
                        "evidence_refs": [],
                    },
                    {
                        "event_id": "evt_00000000-0000-4000-8000-000000000203",
                        "schema": {"name": "result_recorded", "version": "1.0.0"},
                        "occurred_at": "2026-07-21T18:02:00.000Z",
                        "causal_parents": ["evt_00000000-0000-4000-8000-000000000202"],
                        "payload": {
                            "result_id": "res_00000000-0000-4000-8000-000000000201",
                            "action_id": "act_00000000-0000-4000-8000-000000000201",
                            "outcome": "success",
                            "summary": "The bounded acceptance path completed.",
                        },
                        "artifact_refs": [],
                        "evidence_refs": [],
                    },
                ],
            ),
            (
                "req_00000000-0000-4000-8000-000000000104",
                [
                    {
                        "event_id": "evt_00000000-0000-4000-8000-000000000204",
                        "schema": {"name": "evidence_recorded", "version": "1.0.0"},
                        "occurred_at": "2026-07-21T18:03:00.000Z",
                        "causal_parents": ["evt_00000000-0000-4000-8000-000000000203"],
                        "payload": {
                            "evidence_id": "evd_00000000-0000-4000-8000-000000000201",
                            "evidence_kind": "test_result",
                            "strength": "metadata_only",
                            "observed_at": "2026-07-21T18:03:00.000Z",
                            "description": "Ready-composition regression coverage.",
                        },
                        "artifact_refs": [],
                        "evidence_refs": [],
                    },
                    {
                        "event_id": "evt_00000000-0000-4000-8000-000000000205",
                        "schema": {"name": "claim_recorded", "version": "1.0.0"},
                        "occurred_at": "2026-07-21T18:04:00.000Z",
                        "causal_parents": ["evt_00000000-0000-4000-8000-000000000204"],
                        "payload": {
                            "claim_id": "clm_00000000-0000-4000-8000-000000000201",
                            "claim_kind": "completion",
                            "statement": "The ready-composition check path completed.",
                            "supporting_refs": ["evd_00000000-0000-4000-8000-000000000201"],
                        },
                        "artifact_refs": [],
                        "evidence_refs": [],
                    },
                ],
            ),
        )
        for request_id, event_drafts in batches:
            publish_request = PublishWorkRequest.model_validate(
                {
                    **common,
                    "request_id": request_id,
                    "session_id": started.session_id,
                    "writer_id": started.writer_id,
                    "expected_frontier": {
                        "sequence": str(frontier.sequence),
                        "head_digest": frontier.head_digest,
                    },
                    "event_drafts": event_drafts,
                }
            )
            published = await app.publish_work(publish_request)
            assert published.ok is True
            frontier = published.result_frontier

        check_request = CheckRequest.model_validate(
            {
                **common,
                "request_id": "req_00000000-0000-4000-8000-000000000105",
                "session_id": started.session_id,
                "writer_id": started.writer_id,
                "expected_frontier": {
                    "sequence": str(frontier.sequence),
                    "head_digest": frontier.head_digest,
                },
                "mode": "deterministic_only",
                "max_findings": "3",
                "policy_packs": ["work-integrity/0.1.0"],
            }
        )
        checked = await app.check(check_request)
        assert checked.outcome == "committed"
        assert checked.verdict.value in {
            "action_required",
            "insufficient_coverage",
            "no_issue_detected",
        }

        rpc_id = new_id(IdKind.CONTROL_RPC)
        facts = await app.projection_binding_facts(ControlMethod.CHECK, check_request, checked)
        projected = await app.project_result_for_client(
            ClientProjectionContext.fail_safe(ControlClientKind.MCP_BRIDGE),
            ControlProjectionBinding(
                rpc_id=rpc_id,
                method=ControlMethod.CHECK,
                service_instance_id=_INSTANCE_ID,
                service_generation=1,
                original_request_id=facts.original_request_id,
                route_identity_digest=facts.route_identity_digest,
                control_request_canonical=canonical_encode(
                    {
                        "method": ControlMethod.CHECK.value,
                        "rpc_id": rpc_id,
                        "service_generation": "1",
                        "service_instance_id": _INSTANCE_ID,
                    }
                ),
            ),
            checked,
        )
        assert isinstance(projected, CheckResult)
        validated = CheckResult.model_validate(
            projected.model_dump(mode="json", by_alias=True, exclude_unset=True)
        )
        assert validated.root.ok is True
        assert validated.root.verdict == checked.verdict.value
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


@pytest.mark.anyio
async def test_ready_factory_deterministic_check_records_semantic_not_requested_gap(
    tmp_path: Path,
) -> None:
    """Deterministic-only checks under ready composition advertise the not-requested gap."""

    tmp_path.chmod(0o700)
    clock = _Clock()
    memory = LocalSecretMemory()
    lifecycle = ServiceLifecycle(
        clock,
        generation_store=_GenerationStore(),
        process_start_identity_commitment="sha256:" + "a" * 64,
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
        pristine_state_digest="sha256:" + "b" * 64,
    )
    initialize = memory.capture(SecretPurpose.VAULT_INITIALIZE, bytearray(b"correct horse battery"))
    await vault.initialize_passphrase(initialize, "sha256:" + "c" * 64)
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
        common = {
            "protocol_version": "0.1",
            "schema_version": "1.0.0",
            "actor": {"actor_id": "harness:pytest", "actor_type": "harness"},
            "client": {
                "kind": "cooperative_agent",
                "version": "0.1.0",
                "integration": "cooperative_mcp",
            },
        }
        started = await app.start(
            StartRequest.model_validate(
                {
                    **common,
                    "request_id": "req_00000000-0000-4000-8000-000000000201",
                    "mode": "create",
                    "task_title": "Semantic gap under ready composition",
                    "requested_view": "compact",
                }
            )
        )
        assert started.ok is True
        frontier = started.frontier
        published = await app.publish_work(
            PublishWorkRequest.model_validate(
                {
                    **common,
                    "request_id": "req_00000000-0000-4000-8000-000000000202",
                    "session_id": started.session_id,
                    "writer_id": started.writer_id,
                    "expected_frontier": {
                        "sequence": str(frontier.sequence),
                        "head_digest": frontier.head_digest,
                    },
                    "event_drafts": [
                        {
                            "event_id": "evt_00000000-0000-4000-8000-000000000301",
                            "schema": {"name": "plan_published", "version": "1.0.0"},
                            "occurred_at": "2026-07-24T12:00:00.000Z",
                            "causal_parents": [],
                            "payload": {
                                "plan_version": 1,
                                "summary": "Semantic gap path.",
                                "obligation_refs": [],
                            },
                            "artifact_refs": [],
                            "evidence_refs": [],
                        }
                    ],
                }
            )
        )
        assert published.ok is True
        frontier = published.result_frontier
        checked = await app.check(
            CheckRequest.model_validate(
                {
                    **common,
                    "request_id": "req_00000000-0000-4000-8000-000000000203",
                    "session_id": started.session_id,
                    "writer_id": started.writer_id,
                    "expected_frontier": {
                        "sequence": str(frontier.sequence),
                        "head_digest": frontier.head_digest,
                    },
                    "mode": "deterministic_only",
                    "max_findings": "3",
                    "policy_packs": ["work-integrity/0.1.0"],
                }
            )
        )
        assert checked.outcome == "committed"
        assert "semantic_review_not_requested" in checked.coverage.known_gaps
        assert checked.semantic_status.value == "not_requested"
        assert checked.semantic_reason.value == "deterministic_mode"

        # An omitted mode must resolve through VerificationPolicy.default_check_mode. The default
        # config is semantic="optional" -> semantic_if_configured, so the outcome is
        # "not configured" rather than "not requested"; the latter would prove the omission had
        # silently fallen back to deterministic_only.
        frontier = checked.result_frontier
        resolved = await app.check(
            CheckRequest.model_validate(
                {
                    **common,
                    "request_id": "req_00000000-0000-4000-8000-000000000204",
                    "session_id": started.session_id,
                    "writer_id": started.writer_id,
                    "expected_frontier": {
                        "sequence": str(frontier.sequence),
                        "head_digest": frontier.head_digest,
                    },
                    "max_findings": "3",
                    "policy_packs": ["work-integrity/0.1.0"],
                }
            )
        )
        assert resolved.outcome == "committed"
        assert resolved.semantic_status.value == "not_configured"
        assert resolved.semantic_reason.value == "provider_not_configured"
        assert "semantic_review_not_requested" not in resolved.coverage.known_gaps

        # The exact r4 dogfood request: semantic_required against an installation with no bound
        # provider. SEMANTIC is now advertised whenever semantic is not disabled, so this path
        # advances through SEMANTIC_WAIT; it must still commit an honest incomplete check rather
        # than erroring or reporting a clean deterministic pass.
        frontier = resolved.result_frontier
        required = await app.check(
            CheckRequest.model_validate(
                {
                    **common,
                    "request_id": "req_00000000-0000-4000-8000-000000000205",
                    "session_id": started.session_id,
                    "writer_id": started.writer_id,
                    "expected_frontier": {
                        "sequence": str(frontier.sequence),
                        "head_digest": frontier.head_digest,
                    },
                    "mode": "semantic_required",
                    "max_findings": "3",
                    "policy_packs": ["work-integrity/0.1.0"],
                }
            )
        )
        assert required.outcome == "committed"
        assert required.semantic_status.value == "not_configured"
        assert required.semantic_reason.value == "provider_not_configured"
        assert required.verdict.value == "incomplete_check"
    finally:
        if app is not None:
            await app.close()
        await vault.close()
        memory.close()
        await lifecycle.close()
