from __future__ import annotations

import asyncio
import hashlib
import hmac
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast

import apsw
import pytest

import yoetz.adapters.sqlite.connection as connection_module
import yoetz.adapters.sqlite.recovery as recovery_module
import yoetz.service.ready_composition as ready_composition_module
from builders.privacy_policies import minimal_external_policy
from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.adapters.keys.encrypted_vault import EncryptedVaultStore
from yoetz.adapters.keys.secret_memory import LocalSecretMemory
from yoetz.adapters.sqlite.connection import open_catalog_writer
from yoetz.adapters.sqlite.migrations import CATALOG_MIGRATIONS, Migration, initialize_catalog
from yoetz.application.service import ClientProjectionContext, ControlProjectionBinding
from yoetz.config.models import YoetzConfig
from yoetz.config.write import fireworks_provider
from yoetz.domain.observation import ObservationLifecycle
from yoetz.domain.privacy import AuthorizationScope, AuthorizationScopeKind
from yoetz.domain.values import JsonObject
from yoetz.kernel.policies.observation_advice import (
    ObservationAdviceContext,
    ObservationCompositionFact,
    observation_advice_findings,
)
from yoetz.ports.control import (
    ControlCallRequest,
    ControlClientKind,
    ControlMethod,
    ProjectionRenderMode,
    RepositoryPrivacyContext,
    ServiceState,
)
from yoetz.ports.diagnostics import StartupCheckResult
from yoetz.ports.ledger import CheckCommitResult
from yoetz.ports.privacy import (
    EffectivePrivacyPolicy,
    HumanAuthorityCapability,
    LocalDisclosureReceiptView,
    OutboundGatewayPort,
    PrivacyReceiptAudience,
)
from yoetz.ports.secret_memory import HumanAuthorizationProof, SecretPurpose
from yoetz.protocol.canonical import JsonValue, canonical_digest, canonical_encode
from yoetz.protocol.ids import IdKind, new_id
from yoetz.protocol.models import (
    CheckRequest,
    CheckResult,
    CheckSuccessModel,
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
from yoetz.service.vault import VaultMode, VaultService, provider_credential_profile_binding

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

    @property
    def state(self) -> Path:
        return self._bundle / "state"


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


def _write_catalog_v2(path: Path) -> None:
    db = open_catalog_writer(path)
    try:
        with db:
            for migration in CATALOG_MIGRATIONS[:2]:
                db.execute(migration.ddl.decode("utf-8"))
            db.execute(
                "INSERT INTO catalog_meta(key, value) VALUES ('storage_schema_version', '2')"
            )
    finally:
        db.close(force=True)


@pytest.mark.anyio
async def test_open_ready_catalog_transactionally_migrates_v2_before_runtime_use(
    tmp_path: Path,
) -> None:
    tmp_path.chmod(0o700)
    catalog_path = tmp_path / "catalog.sqlite3"
    _write_catalog_v2(catalog_path)
    with pytest.raises(connection_module.StorageUnsafeError, match="schema_metadata_disagrees"):
        open_catalog_writer(catalog_path)

    catalog = await open_ready_catalog(
        catalog_path,
        installation_id=_INSTALLATION_ID,
        service_generation=12,
        lookup=_Lookup(),
        clock=_Clock(),
        ids=IdPort(),
    )
    try:
        assert catalog._db.execute("PRAGMA user_version").fetchone() == (3,)  # pyright: ignore[reportPrivateUsage]
        assert catalog._db.execute(  # pyright: ignore[reportPrivateUsage]
            "SELECT value FROM catalog_meta WHERE key = 'storage_schema_version'"
        ).fetchone() == ("3",)
        assert (
            catalog._db.execute(  # pyright: ignore[reportPrivateUsage]
                "SELECT repository_privacy_commitment FROM task_routes LIMIT 1"
            ).fetchone()
            is None
        )
    finally:
        catalog._db.close(force=True)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.anyio
async def test_open_ready_catalog_failed_migration_rolls_back_and_stays_v2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp_path.chmod(0o700)
    catalog_path = tmp_path / "catalog.sqlite3"
    _write_catalog_v2(catalog_path)
    failing = Migration(
        "0003",
        b"CREATE TABLE migration_partial(value TEXT) STRICT;\n"
        b"SELECT value FROM migration_failure_missing;\n"
        b"PRAGMA user_version = 3;\n",
    )
    monkeypatch.setattr(
        ready_composition_module,
        "CATALOG_MIGRATIONS",
        (*CATALOG_MIGRATIONS[:2], failing),
    )

    with pytest.raises(apsw.SQLError):
        await open_ready_catalog(
            catalog_path,
            installation_id=_INSTALLATION_ID,
            service_generation=12,
            lookup=_Lookup(),
            clock=_Clock(),
            ids=IdPort(),
        )

    db = apsw.Connection(str(catalog_path), flags=apsw.SQLITE_OPEN_READONLY)
    try:
        assert db.execute("PRAGMA user_version").fetchone() == (2,)
        assert (
            db.execute("SELECT 1 FROM sqlite_schema WHERE name = 'migration_partial'").fetchone()
            is None
        )
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


def test_ready_factory_synchronizes_observation_gate_from_loaded_config(tmp_path: Path) -> None:
    paths = _Paths(tmp_path)
    disabled = YoetzConfig().model_copy(
        update={"observation": YoetzConfig().observation.model_copy(update={"enabled": False})}
    )
    build_ready_application_factory(
        lifecycle=object(),  # type: ignore[arg-type]
        vault=object(),  # type: ignore[arg-type]
        config=disabled,
        paths=paths,
        clock=object(),  # type: ignore[arg-type]
        secret_memory=object(),
    )
    store = LocalObservationStore(_state=paths.state)
    assert store.runtime_enabled() is False

    build_ready_application_factory(
        lifecycle=object(),  # type: ignore[arg-type]
        vault=object(),  # type: ignore[arg-type]
        config=YoetzConfig(),
        paths=paths,
        clock=object(),  # type: ignore[arg-type]
        secret_memory=object(),
    )
    assert store.runtime_enabled() is True


@pytest.mark.anyio
async def test_ready_factory_starts_and_reads_repository_bound_setup(tmp_path: Path) -> None:
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
        repository_context = RepositoryPrivacyContext(
            "hmac-sha256:" + "4" * 64,
            "git_common_root",
        )
        setup = await app.privacy_get_setup(
            JsonObject({"schema_version": "2.0.0"}),
            repository_privacy_context=repository_context,
        )
        setup_rpc_id = new_id(IdKind.CONTROL_RPC)
        setup_binding = ControlProjectionBinding(
            rpc_id=setup_rpc_id,
            method=ControlMethod.PRIVACY_GET_SETUP,
            service_instance_id=_INSTANCE_ID,
            service_generation=1,
            original_request_id=None,
            route_identity_digest=None,
            control_request_canonical=canonical_encode(
                {
                    "method": ControlMethod.PRIVACY_GET_SETUP.value,
                    "rpc_id": setup_rpc_id,
                    "service_generation": "1",
                    "service_instance_id": _INSTANCE_ID,
                }
            ),
            repository_privacy_commitment=repository_context.commitment,
        )
        setup_scope = app.disclosure_scope_for(setup_binding, setup)
        assert setup_scope == AuthorizationScope(
            AuthorizationScopeKind.WORKSPACE,
            _INSTALLATION_ID,
            repository_context.commitment,
        )
        assert app.disclosure_scope_for(
            replace(setup_binding, repository_privacy_commitment=None), setup
        ) == AuthorizationScope(AuthorizationScopeKind.MACHINE, _INSTALLATION_ID)
        projected_setup = await app.project_result_for_client(
            ClientProjectionContext(
                ControlClientKind.CLI,
                ProjectionRenderMode.HUMAN_READABLE,
                True,
            ),
            setup_binding,
            setup,
        )
        assert type(projected_setup) is JsonObject
        projection = cast(dict[str, JsonValue], projected_setup)["privacy_projection"]
        assert isinstance(projection, JsonObject)
        receipt_id = cast(str, projection["local_disclosure_receipt_id"])
        policy_app = app.privacy.policy_application
        assert policy_app is not None
        receipt_view = await policy_app.audit.get_receipt(
            receipt_id, PrivacyReceiptAudience.TRUSTED_LOCAL_CONTROL
        )
        assert type(receipt_view) is LocalDisclosureReceiptView
        assert receipt_view.receipt.scope == setup_scope
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

        assert setup["grant_state"] == "missing"
        bound_scope = cast(JsonObject, setup["bound_scope"])
        assert bound_scope["workspace_ref_commitment"] == repository_context.commitment
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


def _bundle_meta(tmp_path: Path, task_id: str) -> dict[str, str]:
    ledger = tmp_path / "tasks" / task_id / "ledger.sqlite3"
    db = apsw.Connection(
        f"file:{ledger}?mode=ro", flags=apsw.SQLITE_OPEN_URI | apsw.SQLITE_OPEN_READONLY
    )
    try:
        rows = db.execute("SELECT key, value FROM bundle_meta").fetchall()
    finally:
        db.close(force=True)
    return {cast(str, row[0]): cast(str, row[1]) for row in rows}


@pytest.mark.anyio
async def test_create_then_attach_same_service_generation_advances_owner_once(
    tmp_path: Path,
) -> None:
    """create then attach in one service generation must succeed and advance ownership once.

    Regression for issue #126 / postmortem 019fc8b8: same-generation attach rewrote owner
    metadata then failed with recovery_generation_not_advanced, poisoning the runtime cache.
    """

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
                "kind": "codex_cli",
                "version": "0.144.6",
                "integration": "local_cli",
            },
            "requested_view": "compact",
            "task_title": "Resume ownership regression",
            "workspace_ref": "https://github.com/example/yoetz-core.git",
            "external_ref": "plan/fix-resumption-ownership",
        }
        created = await app.start(
            StartRequest.model_validate(
                {
                    **common,
                    "request_id": "req_00000000-0000-4000-8000-000000000201",
                    "mode": "create",
                }
            )
        )
        assert created.ok is True
        assert created.outcome == "created"
        after_create = _bundle_meta(tmp_path, created.task_id)
        assert after_create["owner_generation"] == "1"

        attached = await app.start(
            StartRequest.model_validate(
                {
                    **common,
                    "request_id": "req_00000000-0000-4000-8000-000000000202",
                    "mode": "attach",
                    "session_id": created.session_id,
                }
            )
        )
        assert attached.ok is True
        assert attached.outcome == "attached"
        assert attached.task_id == created.task_id
        assert attached.session_id != created.session_id
        assert attached.writer_id != created.writer_id

        after_attach = _bundle_meta(tmp_path, created.task_id)
        # Rebind keeps the create-time fence: ownership advanced exactly once from the fresh "0".
        assert after_attach["owner_generation"] == "1"
        assert after_attach["owner_nonce"] == after_create["owner_nonce"]
    finally:
        if app is not None:
            await app.close()
        await vault.close()
        memory.close()
        await lifecycle.close()


@pytest.mark.anyio
async def test_attach_failure_before_ownership_commit_leaves_meta_and_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reopen-path ownership failure must not rewrite bundle_meta or strand the task."""

    tmp_path.chmod(0o700)
    clock = _Clock()
    memory = LocalSecretMemory()
    lifecycle = ServiceLifecycle(
        clock,
        generation_store=_GenerationStore(),
        process_start_identity_commitment="sha256:" + "d" * 64,
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
        pristine_state_digest="sha256:" + "e" * 64,
    )
    initialize = memory.capture(SecretPurpose.VAULT_INITIALIZE, bytearray(b"correct horse battery"))
    await vault.initialize_passphrase(initialize, "sha256:" + "f" * 64)
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
                "kind": "codex_cli",
                "version": "0.144.6",
                "integration": "local_cli",
            },
            "requested_view": "compact",
            "task_title": "Ownership failure atomicity",
            "workspace_ref": "https://github.com/example/yoetz-core.git",
            "external_ref": "plan/ownership-failure-atomicity",
        }
        created = await app.start(
            StartRequest.model_validate(
                {
                    **common,
                    "request_id": "req_00000000-0000-4000-8000-000000000211",
                    "mode": "create",
                }
            )
        )
        before = _bundle_meta(tmp_path, created.task_id)

        # Force close-then-reopen rather than same-bundle rebind: close the cached entry.
        runtime = app.runtime
        entries = cast(dict[str, object], getattr(runtime, "_entries"))
        entry = entries.pop(created.task_id)
        setattr(entry, "poisoned", True)
        close_entry = cast(
            Callable[[object], Awaitable[None]],
            getattr(runtime, "_close_entry"),
        )
        await close_entry(entry)

        backend = recovery_module._backend()  # pyright: ignore[reportPrivateUsage]
        original = backend.acquire_ownership

        def _failing_acquire(
            state: object,
            *,
            service_instance_id: str,
            service_generation: int,
            owner_nonce: str,
            now: datetime,
        ) -> int:
            del state, service_instance_id, service_generation, owner_nonce, now
            raise ValueError("injected_acquire_failure")

        monkeypatch.setattr(backend, "acquire_ownership", _failing_acquire)
        with pytest.raises(ValueError, match="injected_acquire_failure"):
            await app.start(
                StartRequest.model_validate(
                    {
                        **common,
                        "request_id": "req_00000000-0000-4000-8000-000000000212",
                        "mode": "attach",
                        "session_id": created.session_id,
                    }
                )
            )
        monkeypatch.setattr(backend, "acquire_ownership", original)

        after = _bundle_meta(tmp_path, created.task_id)
        assert after["owner_generation"] == before["owner_generation"]
        assert after["owner_nonce"] == before["owner_nonce"]
        assert after["updated_at"] == before["updated_at"]

        # Original ownership still admits a successful attach once the injection is removed.
        recovered = await app.start(
            StartRequest.model_validate(
                {
                    **common,
                    "request_id": "req_00000000-0000-4000-8000-000000000213",
                    "mode": "attach",
                    "session_id": created.session_id,
                }
            )
        )
        assert recovered.ok is True
        assert recovered.outcome == "attached"
        assert recovered.task_id == created.task_id
    finally:
        if app is not None:
            await app.close()
        await vault.close()
        memory.close()
        await lifecycle.close()


@pytest.mark.anyio
async def test_stale_recovery_state_cas_loser_mutates_nothing(tmp_path: Path) -> None:
    """Two interleaved stale RecoveryState snapshots: exactly one CAS wins."""

    tmp_path.chmod(0o700)
    clock = _Clock()
    memory = LocalSecretMemory()
    lifecycle = ServiceLifecycle(
        clock,
        generation_store=_GenerationStore(),
        process_start_identity_commitment="sha256:" + "11" * 32,
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
        pristine_state_digest="sha256:" + "22" * 32,
    )
    initialize = memory.capture(SecretPurpose.VAULT_INITIALIZE, bytearray(b"correct horse battery"))
    await vault.initialize_passphrase(initialize, "sha256:" + "33" * 32)
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
        created = await app.start(
            StartRequest.model_validate(
                {
                    "protocol_version": "0.1",
                    "schema_version": "1.0.0",
                    "request_id": "req_00000000-0000-4000-8000-000000000221",
                    "mode": "create",
                    "task_title": "CAS interleaving",
                    "actor": {"actor_id": "harness:pytest", "actor_type": "harness"},
                    "client": {
                        "kind": "codex_cli",
                        "version": "0.144.6",
                        "integration": "local_cli",
                    },
                    "requested_view": "compact",
                }
            )
        )
        before = _bundle_meta(tmp_path, created.task_id)
        backend = cast(
            ready_composition_module._RecoveryPersistence,  # pyright: ignore[reportPrivateUsage]
            recovery_module._backend(),  # pyright: ignore[reportPrivateUsage]
        )
        ledger = tmp_path / "tasks" / created.task_id
        state = cast(
            recovery_module.RecoveryState,
            backend.inspect(
                ledger,
                catalog_path=tmp_path / "catalog.sqlite3",
                task_id=created.task_id,
                route_generation=int(before["route_generation"], 10),
                route_identity_digest=before["route_identity_digest"],
            ),
        )
        stale = replace(
            state,
            owner_generation=int(before["owner_generation"], 10),
            owner_nonce=before["owner_nonce"],
        )
        winner = backend.acquire_ownership(
            stale,
            service_instance_id=_INSTANCE_ID,
            service_generation=1,
            owner_nonce="winner-owner-nonce-0000001",
            now=clock.now_utc(),
        )
        assert winner == int(before["owner_generation"], 10) + 1
        mid = _bundle_meta(tmp_path, created.task_id)
        assert mid["owner_generation"] == str(winner)
        assert mid["owner_nonce"] == "winner-owner-nonce-0000001"

        with pytest.raises(ValueError, match="recovery_ownership_conflict"):
            backend.acquire_ownership(
                stale,
                service_instance_id=_INSTANCE_ID,
                service_generation=1,
                owner_nonce="loser-owner-nonce-00000001",
                now=clock.now_utc(),
            )
        after = _bundle_meta(tmp_path, created.task_id)
        assert after["owner_generation"] == mid["owner_generation"]
        assert after["owner_nonce"] == mid["owner_nonce"]
        assert after["updated_at"] == mid["updated_at"]
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
        object.__setattr__(app, "enforce_repository_identity", False)
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
        assert type(checked) is CheckCommitResult, f"unexpected nonterminal check: {type(checked)}"
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
        assert type(validated.root) is CheckSuccessModel
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
        object.__setattr__(app, "enforce_repository_identity", False)
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
        assert type(checked) is CheckCommitResult, f"unexpected nonterminal check: {type(checked)}"
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
        assert type(resolved) is CheckCommitResult, (
            f"unexpected nonterminal check: {type(resolved)}"
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
        assert type(required) is CheckCommitResult, (
            f"unexpected nonterminal check: {type(required)}"
        )
        assert required.outcome == "committed"
        assert required.semantic_status.value == "not_configured"
        assert required.semantic_reason.value == "provider_not_configured"
        assert required.verdict.value == "incomplete_check"

        # Issue #185: the stop-rule fallback. An agent whose semantic attempt the environment
        # refused re-checks with deterministic_only, and that successor replaces
        # latest_tested_state wholesale. Without the carry-forward the only surviving disclosure
        # is semantic_review_not_requested, which reads as the agent never having asked.
        frontier = required.result_frontier
        fallback = await app.check(
            CheckRequest.model_validate(
                {
                    **common,
                    "request_id": "req_00000000-0000-4000-8000-000000000206",
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
        assert type(fallback) is CheckCommitResult, (
            f"unexpected nonterminal check: {type(fallback)}"
        )
        assert fallback.outcome == "committed"
        assert fallback.semantic_status.value == "not_requested"
        assert "semantic_review_not_requested" in fallback.coverage.known_gaps
        assert "semantic_review_not_configured" in fallback.coverage.known_gaps
    finally:
        if app is not None:
            await app.close()
        await vault.close()
        memory.close()
        await lifecycle.close()


@pytest.mark.anyio
async def test_ready_composition_reports_exact_configured_credential_presence(
    tmp_path: Path,
) -> None:
    """Status can read exact vault presence without inventing repository authority."""

    tmp_path.chmod(0o700)
    clock = _Clock()
    memory = LocalSecretMemory()
    lifecycle = ServiceLifecycle(
        clock,
        generation_store=_GenerationStore(),
        process_start_identity_commitment="sha256:" + "d" * 64,
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
        pristine_state_digest="sha256:" + "e" * 64,
    )
    initialize = memory.capture(SecretPurpose.VAULT_INITIALIZE, bytearray(b"correct horse battery"))
    await vault.initialize_passphrase(initialize, "sha256:" + "f" * 64)
    provider = fireworks_provider(model="accounts/fireworks/models/minimax-m3")
    config = YoetzConfig(profile="local-openai", provider=provider)

    def application_factory(provider_config: YoetzConfig):
        return build_ready_application_factory(
            lifecycle=lifecycle,
            vault=vault,
            config=provider_config,
            paths=_Paths(tmp_path),
            clock=clock,
            secret_memory=memory,
            diagnostics=_Diagnostics(),
        )

    app = None
    try:
        app = await application_factory(config)(1, vault.generation)
        assert app.provider_credential_connected is False
        await app.close()
        app = None

        credential_binding = provider_credential_profile_binding(
            provider.provider_id,
            provider.model,
            provider.endpoint_profile_id,
            provider.endpoint_profile_version,
        )
        proof = HumanAuthorizationProof(
            "provider-proof-before-recomposition",
            "provider_credential_set",
            credential_binding.target_digest("set"),
            1,
            vault.generation,
            None,
            1.0,
            60.0,
        )
        credential = memory.capture(
            SecretPurpose.PROVIDER_CREDENTIAL,
            bytearray(b"test-provider-token-for-status"),
        )
        await vault.store_provider_credential("set", credential_binding, credential, proof, 2.0)

        app = await application_factory(config)(1, vault.generation)
        assert app.provider_credential_connected is True
        await app.close()
        app = None

        other_provider = fireworks_provider(model="accounts/fireworks/models/a-different-model")
        other_config = YoetzConfig(profile="local-openai", provider=other_provider)
        app = await application_factory(other_config)(1, vault.generation)
        assert app.provider_credential_connected is False
    finally:
        if app is not None:
            await app.close()
        await vault.close()
        memory.close()
        await lifecycle.close()


@pytest.mark.anyio
async def test_observation_provider_fact_tracks_live_credential_within_one_generation(
    tmp_path: Path,
) -> None:
    """The standing-advice provider fact follows the vault, not the READY snapshot (#265).

    The incident session was advised connect_provider at Stop right after a
    successful semantic dispatch because the advice fact froze
    ``semantic_ready=False`` at composition. The fact source must observe a
    credential stored or discarded mid-generation without recomposition, and
    must not fire the advice from registry lag alone.
    """

    tmp_path.chmod(0o700)
    clock = _Clock()
    memory = LocalSecretMemory()
    lifecycle = ServiceLifecycle(
        clock,
        generation_store=_GenerationStore(),
        process_start_identity_commitment="sha256:" + "d" * 64,
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
        pristine_state_digest="sha256:" + "e" * 64,
    )
    initialize = memory.capture(SecretPurpose.VAULT_INITIALIZE, bytearray(b"correct horse battery"))
    await vault.initialize_passphrase(initialize, "sha256:" + "f" * 64)
    provider = fireworks_provider(model="accounts/fireworks/models/minimax-m3")
    config = YoetzConfig(profile="local-openai", provider=provider)
    factory = build_ready_application_factory(
        lifecycle=lifecycle,
        vault=vault,
        config=config,
        paths=_Paths(tmp_path),
        clock=clock,
        secret_memory=memory,
        diagnostics=_Diagnostics(),
    )

    def provider_rules(fact: object) -> set[str]:
        assert type(fact) is ObservationCompositionFact
        return {
            item.rule_code
            for item in observation_advice_findings(
                ObservationAdviceContext(
                    envelopes=(),
                    lifecycle=ObservationLifecycle.ACTIVE,
                    gaps=(),
                    composition=fact,
                )
            )
        }

    app = None
    try:
        context = await factory.context_provider(1, vault.generation)
        assert context.rediscover_pending_verification is not None
        coordinator: object = getattr(context.rediscover_pending_verification, "__self__")
        builder_composition: object = getattr(coordinator, "advice_context_builder").composition
        assert callable(builder_composition), "the advice provider fact must be sourced per build"
        composition = cast(
            Callable[[], Awaitable[ObservationCompositionFact | None]], builder_composition
        )
        app = await factory.open(context)

        # Genuinely missing credential: the advice stays visible.
        fact = await composition()
        assert type(fact) is ObservationCompositionFact
        assert fact.semantic_configured is True
        assert fact.semantic_ready is False
        assert "provider_not_ready" in provider_rules(fact)

        # Credential ceremony lands mid-generation: the next build sees it and
        # the connect_provider recommendation becomes inapplicable, even though
        # the lazy connected registry has still never listed the provider.
        credential_binding = provider_credential_profile_binding(
            provider.provider_id,
            provider.model,
            provider.endpoint_profile_id,
            provider.endpoint_profile_version,
        )
        proof = HumanAuthorizationProof(
            "provider-proof-mid-generation",
            "provider_credential_set",
            credential_binding.target_digest("set"),
            1,
            vault.generation,
            None,
            1.0,
            60.0,
        )
        credential = memory.capture(
            SecretPurpose.PROVIDER_CREDENTIAL,
            bytearray(b"test-provider-token-mid-generation"),
        )
        await vault.store_provider_credential("set", credential_binding, credential, proof, 2.0)

        connected = await composition()
        assert type(connected) is ObservationCompositionFact
        assert connected.semantic_ready is True
        assert "fireworks" not in connected.connected_provider_ids
        assert provider_rules(connected) == set()

        # Revocation resurfaces the advice from current evidence.
        await vault.discard_provider_credential(credential_binding)
        revoked = await composition()
        assert type(revoked) is ObservationCompositionFact
        assert revoked.semantic_ready is False
        assert "provider_not_ready" in provider_rules(revoked)
    finally:
        if app is not None:
            await app.close()
        await vault.close()
        memory.close()
        await lifecycle.close()


@pytest.mark.anyio
async def test_ready_check_never_activates_provider_from_machine_policy_without_repository_grant(
    tmp_path: Path,
) -> None:
    """A configured provider and machine policy never outrun exact repository authority."""

    tmp_path.chmod(0o700)
    clock = _Clock()
    memory = LocalSecretMemory()
    lifecycle = ServiceLifecycle(
        clock,
        generation_store=_GenerationStore(),
        process_start_identity_commitment="sha256:" + "d" * 64,
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
        pristine_state_digest="sha256:" + "e" * 64,
    )
    initialize = memory.capture(SecretPurpose.VAULT_INITIALIZE, bytearray(b"correct horse battery"))
    await vault.initialize_passphrase(initialize, "sha256:" + "f" * 64)
    provider = fireworks_provider(model="accounts/fireworks/models/minimax-m3")
    config = YoetzConfig(profile="local-openai", provider=provider)
    app = None
    try:
        factory = build_ready_application_factory(
            lifecycle=lifecycle,
            vault=vault,
            config=config,
            paths=_Paths(tmp_path),
            clock=clock,
            secret_memory=memory,
            diagnostics=_Diagnostics(),
        )
        app = await factory(1, vault.generation)
        object.__setattr__(app, "enforce_repository_identity", False)
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
                    "request_id": "req_00000000-0000-4000-8000-000000000401",
                    "mode": "create",
                    "task_title": "Resolve provider binding after ready composition",
                    "requested_view": "compact",
                }
            )
        )
        original_request = CheckRequest.model_validate(
            {
                **common,
                "request_id": "req_00000000-0000-4000-8000-000000000402",
                "session_id": started.session_id,
                "writer_id": started.writer_id,
                "expected_frontier": {
                    "sequence": str(started.frontier.sequence),
                    "head_digest": started.frontier.head_digest,
                },
                "mode": "semantic_required",
                "max_findings": "3",
                "policy_packs": ["work-integrity/0.1.0"],
            }
        )
        first = await app.check(original_request)
        assert type(first) is CheckCommitResult
        assert first.semantic_status.value == "blocked_by_policy"
        assert first.semantic_reason.value == "scope_not_authorized"

        evaluator = app.semantic_evaluator
        widened = replace(
            minimal_external_policy(),
            effective_scope=AuthorizationScope(AuthorizationScopeKind.MACHINE, _INSTALLATION_ID),
            created_at=clock.now_utc(),
        )
        gateway = cast(OutboundGatewayPort, getattr(app.privacy, "_gateway"))
        await gateway.reconcile_policy(
            EffectivePrivacyPolicy(widened, 2, widened.policy_digest),
            HumanAuthorityCapability(
                "established_passphrase",
                canonical_digest({"source": "test"}),
                1,
                str(getattr(vault.mode, "value", vault.mode)),
                vault.generation,
                True,
            ),
        )
        assert "fireworks" not in cast(
            tuple[str, ...], tuple(getattr(gateway, "connected_provider_ids")())
        )
        credential_binding = provider_credential_profile_binding(
            provider.provider_id,
            provider.model,
            provider.endpoint_profile_id,
            provider.endpoint_profile_version,
        )
        assert await vault.has_provider_credential(credential_binding) is False
        proof = HumanAuthorizationProof(
            "provider-proof-after-composition",
            "provider_credential_set",
            credential_binding.target_digest("set"),
            1,
            vault.generation,
            None,
            1.0,
            60.0,
        )
        credential = memory.capture(
            SecretPurpose.PROVIDER_CREDENTIAL,
            bytearray(b"test-provider-token-after-composition"),
        )
        await vault.store_provider_credential("set", credential_binding, credential, proof, 2.0)
        assert await vault.has_provider_credential(credential_binding) is True

        second = await app.check(original_request)
        assert type(second) is CheckCommitResult

        assert app.semantic_evaluator is evaluator
        # Credential rotation/storage does not create repository authority or activate the
        # provider. The second check fails at the same exact grant fence without a provider call.
        assert second.outcome == "replayed"
        assert second.semantic_status == first.semantic_status
        assert second.semantic_reason == first.semantic_reason

        third = await app.check(
            CheckRequest.model_validate(
                {
                    **common,
                    "request_id": "req_00000000-0000-4000-8000-000000000403",
                    "session_id": started.session_id,
                    "writer_id": started.writer_id,
                    "expected_frontier": {
                        "sequence": str(second.result_frontier.sequence),
                        "head_digest": second.result_frontier.head_digest,
                    },
                    "mode": "semantic_required",
                    "max_findings": "3",
                    "policy_packs": ["work-integrity/0.1.0"],
                }
            )
        )
        assert type(third) is CheckCommitResult
        assert third.outcome == "committed"
        assert third.semantic_status.value == "blocked_by_policy"
        assert third.semantic_reason.value == "scope_not_authorized"
        assert "fireworks" not in cast(
            tuple[str, ...], tuple(getattr(gateway, "connected_provider_ids")())
        )
    finally:
        if app is not None:
            await app.close()
        await vault.close()
        memory.close()
        await lifecycle.close()
