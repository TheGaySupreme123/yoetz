"""Daemon-private production ready-application composition."""

from __future__ import annotations

import base64
import hashlib
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Final, Protocol, cast

import apsw

import yoetz.adapters.sqlite.connection as connection_module
import yoetz.adapters.sqlite.recovery as recovery_module
from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.adapters.objects.encrypted_files import EncryptedFilesObjectStore
from yoetz.adapters.privacy.catalog import CatalogPrivacyAudit, CatalogPrivacyPolicyStore
from yoetz.adapters.privacy.gateway import PolicyEnforcingOutboundGateway
from yoetz.adapters.privacy.local_enforcer import LocalPrivacyEnforcer
from yoetz.adapters.providers.factory import external_factory_builders_from_config
from yoetz.adapters.providers.local_model import InstalledLocalModelProfileRegistry
from yoetz.adapters.providers.openai_responses_factory import provider_binding_from_config
from yoetz.adapters.runtime import RuntimeAdapterFactories, open_local_bundle_runtime
from yoetz.adapters.sqlite.connection import (
    open_catalog_writer,
    open_read_only,
    open_writer,
    verify_schema_identity,
)
from yoetz.adapters.sqlite.migrations import initialize_bundle, initialize_catalog
from yoetz.adapters.sqlite.repository import SqliteLedger
from yoetz.adapters.sqlite.start_catalog import SqliteStartCatalog
from yoetz.application.check import FinalSemanticEvaluation
from yoetz.application.egress import (
    PrivacyCoordinator,
    SemanticEgressAwaitingHuman,
    SemanticEgressBlocked,
    SemanticEgressProviderOutcome,
    SemanticEgressSuccess,
)
from yoetz.application.observation_advice import (
    ObservationAdviceContextBuilder,
    ObservationAdviceSemanticAddon,
    minimized_semantic_evidence_packet,
    stable_advice_finding_id,
)
from yoetz.application.observation_control import build_observation_support_handlers
from yoetz.application.observation_coordinator import ObservationCoordinator
from yoetz.application.observation_verification import ObservationVerificationSupervisor
from yoetz.application.privacy_control import build_privacy_support_handlers
from yoetz.application.privacy_policy import PrivacyPolicyApplication
from yoetz.application.service import (
    ControlProjectionBinding,
    ReadyApplicationFactory,
    ServiceReadyContext,
    VerificationPolicy,
)
from yoetz.config.models import YoetzConfig
from yoetz.config.paths import ensure_owner_only_dir, verify_private_local_bundle
from yoetz.config.privacy import safe_privacy_bootstrap, seed_policy_if_absent
from yoetz.domain.events import RuntimeProfile
from yoetz.domain.findings import SemanticDispatchKind, SemanticProvenance
from yoetz.domain.privacy import (
    AuthorizationScope,
    AuthorizationScopeKind,
    CandidateContext,
    CandidateContextItem,
    ChannelPolicy,
    DataCategory,
    DataClass,
    EgressChannel,
    PrivacyOutcome,
    PrivacyPolicy,
    PrivacyProfile,
    ProviderBinding,
    ReviewContextProfile,
    ReviewSelectionPolicy,
)
from yoetz.domain.receipts import PolicyVersionEntry, ReceiptVersionSlice, SchemaVersionEntry
from yoetz.domain.values import (
    Frontier,
    format_rfc3339_millis,
    session_id,
)
from yoetz.domain.values import (
    JsonValue as DomainJsonValue,
)
from yoetz.kernel.policies.observation_advice import (
    ObservationAdviceCandidate,
    ObservationCompositionFact,
)
from yoetz.observability.logging import record_unexpected_exception_without_raising
from yoetz.ports.clock import ClockPort
from yoetz.ports.control import ControlError
from yoetz.ports.diagnostics import DiagnosticsPort, RuntimeCapability, StartupCheckResult
from yoetz.ports.importer import ImporterPort, ImportStatusSnapshot
from yoetz.ports.keys import BundleKeys, MacKeyHandle, MacKeyPurpose
from yoetz.ports.ledger import FrozenCase, LedgerPort
from yoetz.ports.objects import (
    ObjectMetadata,
    ObjectRef,
    ObjectRootSnapshot,
    ObjectSource,
    ObjectStorePort,
    StagedObject,
)
from yoetz.ports.privacy import HumanAuthorityCapability
from yoetz.ports.runtime import (
    OwnershipFence,
    RouteAccess,
    ServiceRuntimeContext,
    StartCompletionEvidence,
    StartMilestone,
    StartMilestoneExpectation,
    TaskRuntime,
)
from yoetz.ports.secret_memory import ProviderAttemptAuthBinding, ProviderCredentialHandle
from yoetz.ports.semantic import (
    Deadline,
    SemanticResultInvalid,
    SemanticResultLate,
    SemanticResultRefused,
    SemanticResultTimeout,
    SemanticResultUnavailable,
)
from yoetz.ports.start_catalog import (
    WORKSPACE_REF_DOMAIN,
    StartCatalogPort,
    TaskRoute,
    TaskRouteState,
)
from yoetz.protocol.canonical import JsonValue as CanonicalJsonValue
from yoetz.protocol.canonical import canonical_digest, canonical_encode, strict_json_parse
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError
from yoetz.protocol.ids import IdKind, new_id, validate_id
from yoetz.protocol.models import SemanticReason, SemanticStatus
from yoetz.version import build_version_manifest, version_manifest_json

__all__ = [
    "IdPort",
    "build_privacy_coordinator",
    "build_ready_application_factory",
    "build_runtime_adapter_factories",
    "open_ready_catalog",
    "provide_service_ready_context",
]

_CATALOG_NAME = "catalog.sqlite3"
_LEDGER_NAME = "ledger.sqlite3"
_ZERO_DIGEST = "sha256:" + "0" * 64


class _Lifecycle(Protocol):
    @property
    def instance(self) -> object: ...


class _Vault(Protocol):
    @property
    def ready(self) -> bool: ...

    @property
    def generation(self) -> int: ...

    @property
    def mode(self) -> object: ...

    async def load_bundle_keys(self, bundle_id: str) -> BundleKeys: ...

    async def create_bundle_keys(self, bundle_id: str) -> BundleKeys: ...

    def installation_mac_handle(self, purpose: MacKeyPurpose) -> MacKeyHandle: ...

    async def provider_credential(
        self, binding: ProviderAttemptAuthBinding
    ) -> ProviderCredentialHandle: ...


class _Paths(Protocol):
    @property
    def bundle(self) -> Path: ...


class _SqliteSupportPolicyFactory(Protocol):
    def __call__(
        self,
        *,
        manifest_id: str,
        required_options: frozenset[str],
        denied_options: frozenset[str],
    ) -> object: ...


class IdPort:
    """Production ID port bound to the frozen protocol ID generator."""

    def new(self, kind: IdKind) -> str:
        if type(kind) is not IdKind:
            raise TypeError("id_kind_invalid")
        return new_id(kind)


class _NullDiagnostics:
    def record(self, result: StartupCheckResult) -> None:
        if type(result) is not StartupCheckResult:
            raise TypeError("startup_diagnostic_invalid")


class _CredentialMinter:
    def __init__(self, vault: _Vault) -> None:
        self._vault = vault

    async def mint(self, binding: ProviderAttemptAuthBinding) -> ProviderCredentialHandle:
        return await self._vault.provider_credential(binding)


class _PrivacyContentObjectStore:
    """Process-local content store for privacy disclosure proposals (catalog refs only)."""

    def __init__(self, ids: IdPort) -> None:
        self._ids = ids
        self._objects: dict[str, bytes] = {}

    async def stage(self, source: ObjectSource, metadata: ObjectMetadata) -> StagedObject:
        if type(source) is not ObjectSource or source.data is None:
            raise ValueError("invalid_object_source")
        object_id = self._ids.new(IdKind.OBJECT)
        digest = "sha256:" + hashlib.sha256(source.data).hexdigest()
        commitment = "hmac-sha256:" + ("a" * 64)
        return StagedObject(
            object_id,
            len(source.data),
            commitment,
            digest,
            "yoetz-object/1",
            "privacy-audit",
            metadata,
            source.data,
        )

    async def finalize(self, staged: StagedObject) -> ObjectRef:
        handle = staged.staging_handle
        if type(handle) is not bytes:
            raise ValueError("privacy_audit_stage_invalid")
        self._objects[staged.object_id] = handle
        return ObjectRef(
            staged.object_id,
            staged.plaintext_size,
            staged.commitment,
            staged.envelope_digest,
            staged.encryption_format,
            staged.key_slot,
            staged.metadata,
        )


class _MinimalImporter:
    async def status(self, value: str) -> ImportStatusSnapshot:
        return ImportStatusSnapshot(session_id(value), 0, 0, (), ())

    def __getattr__(self, name: str) -> object:
        raise PublicOperationError(
            PublicErrorCode.INVALID_REQUEST,
            f"Importer method unavailable: {name}",
            False,
        )


@dataclass(frozen=True, slots=True)
class _BundleInspection:
    route: object
    bundle_root: Path
    ledger_path: Path
    catalog_path: Path
    admitted_writer_ids: frozenset[str]
    fresh_allocation: bool
    recovery_state: object
    recovery_verdict: object


def _install_sqlite_support_policy() -> None:
    db = apsw.Connection(":memory:")
    try:
        raw_options: object = db.pragma("compile_options")
    finally:
        db.close(force=True)
    if type(raw_options) is not list:
        raise RuntimeError("sqlite_compile_options_invalid")
    raw_option_items = cast(list[object], raw_options)
    if any(type(item) is not str for item in raw_option_items):
        raise RuntimeError("sqlite_compile_options_invalid")
    options = frozenset(cast(list[str], raw_option_items))
    factory = cast(_SqliteSupportPolicyFactory, getattr(connection_module, "_SqliteSupportPolicy"))
    installer = cast(
        Callable[[object | None], None], getattr(connection_module, "_install_support_policy")
    )
    policy = factory(
        manifest_id=build_version_manifest().resource_manifest_digest,
        required_options=options,
        denied_options=frozenset({"OMIT_FOREIGN_KEY", "OMIT_WAL", "THREADSAFE=0"}),
    )
    installer(policy)


def _install_recovery_persistence(persistence: object) -> None:
    installer = cast(
        Callable[[object | None], None],
        getattr(recovery_module, "_install_recovery_persistence"),
    )
    installer(persistence)


def _open_recovery_writer(path: Path) -> apsw.Connection:
    opener = cast(
        Callable[[Path], apsw.Connection], getattr(connection_module, "_open_recovery_writer")
    )
    return opener(path)


def _nonce() -> str:
    return base64.urlsafe_b64encode(os.urandom(18)).decode("ascii").rstrip("=")


def _close_db(db: apsw.Connection | None) -> None:
    if db is None:
        return
    try:
        db.close(force=True)
    except Exception:
        return


def _catalog_path(paths: _Paths) -> Path:
    return paths.bundle / _CATALOG_NAME


def _safe_bundle_root(base: Path, relpath: str, task_id: str) -> Path:
    validate_id(IdKind.TASK, task_id)
    if type(relpath) is not str:
        raise ValueError("bundle_relpath_invalid")
    parts = Path(relpath).parts
    if parts != ("tasks", task_id):
        raise ValueError("bundle_relpath_invalid")
    root = (base / relpath).resolve(strict=False)
    if root.parent != (base / "tasks").resolve(strict=False):
        raise ValueError("bundle_relpath_invalid")
    return root


def _seed_catalog_meta(
    db: apsw.Connection, *, installation_id: str, service_generation: int
) -> None:
    validate_id(IdKind.INSTALLATION, installation_id)
    if type(service_generation) is not int or service_generation <= 0:
        raise ValueError("service_generation_invalid")
    with db:
        existing = db.execute(
            "SELECT value FROM catalog_meta WHERE key = 'installation_id'"
        ).fetchone()
        if existing is not None and existing != (installation_id,):
            raise ValueError("catalog_installation_mismatch")
        db.execute(
            "INSERT INTO catalog_meta(key, value) VALUES ('installation_id', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (installation_id,),
        )
        db.execute(
            "INSERT INTO catalog_meta(key, value) VALUES ('owner_generation', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(service_generation),),
        )


async def open_ready_catalog(
    path: Path,
    *,
    installation_id: str,
    service_generation: int,
    lookup: object,
    clock: ClockPort,
    ids: IdPort,
) -> SqliteStartCatalog:
    """Open, initialize if needed, and generation-bind the ready catalog."""

    _install_sqlite_support_policy()
    db = open_catalog_writer(path)
    try:
        identity = verify_schema_identity(db)
        if identity.state == "uninitialized":
            initialize_catalog(db)
        elif identity.state != "current":
            raise ValueError("catalog_schema_unsupported")
        _seed_catalog_meta(
            db,
            installation_id=installation_id,
            service_generation=service_generation,
        )
        return SqliteStartCatalog(
            db,
            installation_id=installation_id,
            lookup=lookup,  # pyright: ignore[reportArgumentType]
            clock=clock,
            ids=ids,
        )
    except BaseException:
        _close_db(db)
        raise


class _RecoveryPersistence:
    def __init__(self, catalog_path: Path, clock: ClockPort) -> None:
        self._catalog_path = catalog_path
        self._clock = clock

    def inspect(
        self,
        bundle_root: Path,
        *,
        catalog_path: Path,
        task_id: str,
        route_generation: int,
        route_identity_digest: str,
    ) -> object:
        if catalog_path != self._catalog_path:
            raise ValueError("recovery_catalog_mismatch")
        ledger_path = bundle_root / _LEDGER_NAME
        db = open_read_only(ledger_path)
        catalog = open_read_only(catalog_path)
        try:
            metadata = self._bundle_meta(db)
            if (
                metadata.get("task_id") != task_id
                or metadata.get("route_generation") != str(route_generation)
                or metadata.get("route_identity_digest") != route_identity_digest
            ):
                raise ValueError("recovery_route_mismatch")
            frontier = self._frontier(db)
            privacy_generation, privacy_digest = self._privacy_root(catalog, task_id)
            return recovery_module.RecoveryState(
                bundle_root=bundle_root,
                catalog_path=catalog_path,
                task_id=task_id,
                route_generation=route_generation,
                route_identity_digest=route_identity_digest,
                storage_schema_version=int(metadata.get("storage_schema_version", "0"), 10),
                owner_generation=int(metadata.get("owner_generation", "0"), 10),
                owner_nonce=metadata["owner_nonce"],
                last_verified_frontier=frontier,
                tail_state=recovery_module.RecoveryTailState.CLEAN,
                object_state=recovery_module.RecoveryObjectState.VERIFIED,
                key_state=recovery_module.RecoveryKeyState.READY,
                marker_state=recovery_module.RecoveryMarkerState.ABSENT,
                projection_state=recovery_module.RecoveryProjectionState.CURRENT,
                privacy_root_generation=privacy_generation,
                privacy_root_digest=privacy_digest,
            )
        finally:
            _close_db(catalog)
            _close_db(db)

    def acquire_ownership(
        self,
        state: object,
        *,
        service_instance_id: str,
        service_generation: int,
        owner_nonce: str,
        now: datetime,
    ) -> int:
        if type(state) is not recovery_module.RecoveryState:
            raise ValueError("recovery_value_invalid")
        validate_id(IdKind.SERVICE_INSTANCE, service_instance_id)
        format_rfc3339_millis(now)
        db = _open_recovery_writer(state.bundle_root / _LEDGER_NAME)
        try:
            with db:
                self._set_meta(db, "owner_generation", str(service_generation))
                self._set_meta(db, "owner_nonce", owner_nonce)
                self._set_meta(db, "updated_at", format_rfc3339_millis(now))
            return service_generation
        finally:
            _close_db(db)

    def verify_fence(self, state: object, fence: OwnershipFence) -> None:
        if type(state) is not recovery_module.RecoveryState or type(fence) is not OwnershipFence:
            raise ValueError("recovery_value_invalid")
        db = open_read_only(state.bundle_root / _LEDGER_NAME)
        try:
            metadata = self._bundle_meta(db)
            if (
                metadata.get("owner_generation") != str(fence.owner_generation)
                or metadata.get("owner_nonce") != fence.nonce
                or metadata.get("task_id") != state.task_id
                or metadata.get("route_identity_digest") != state.route_identity_digest
                or metadata.get("route_generation") != str(state.route_generation)
            ):
                raise ValueError("recovery_fence_invalid")
        finally:
            _close_db(db)

    def complete_interrupted(
        self, state: object, fence: OwnershipFence, *, now: datetime
    ) -> object:
        del fence, now
        if type(state) is not recovery_module.RecoveryState:
            raise ValueError("recovery_value_invalid")
        return replace(state, tail_state=recovery_module.RecoveryTailState.CLEAN)

    def rebuild_projection(self, state: object, fence: OwnershipFence, *, now: datetime) -> object:
        del fence, now
        if type(state) is not recovery_module.RecoveryState:
            raise ValueError("recovery_value_invalid")
        return replace(state, projection_state=recovery_module.RecoveryProjectionState.CURRENT)

    def persist_quarantine(
        self,
        state: object,
        reason: object,
        fence: OwnershipFence,
        *,
        now: datetime,
    ) -> None:
        del fence
        if type(state) is not recovery_module.RecoveryState:
            raise ValueError("recovery_value_invalid")
        catalog = open_catalog_writer(state.catalog_path)
        try:
            with catalog:
                catalog.execute(
                    "UPDATE task_routes SET state='quarantined', quarantine_code=?, updated_at=? "
                    "WHERE task_id=? AND active_route_identity_digest=?",
                    (
                        getattr(reason, "value", "recovery_quarantined"),
                        format_rfc3339_millis(now),
                        state.task_id,
                        state.route_identity_digest,
                    ),
                )
        finally:
            _close_db(catalog)

    def activate_restore(
        self, state: object, manifest: object, fence: OwnershipFence, *, now: datetime
    ) -> str:
        del state, manifest, fence, now
        return "provenance_invalid"

    @staticmethod
    def _bundle_meta(db: apsw.Connection) -> dict[str, str]:
        rows = db.execute("SELECT key, value FROM bundle_meta").fetchall()
        if any(len(row) != 2 or type(row[0]) is not str or type(row[1]) is not str for row in rows):
            raise ValueError("bundle_meta_invalid")
        return {cast(str, row[0]): cast(str, row[1]) for row in rows}

    @staticmethod
    def _set_meta(db: apsw.Connection, key: str, value: str) -> None:
        db.execute(
            "INSERT INTO bundle_meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    @staticmethod
    def _frontier(db: apsw.Connection) -> Frontier:
        row = db.execute(
            "SELECT ingestion_seq, entry_digest FROM events ORDER BY ingestion_seq DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return Frontier.genesis()
        if len(row) != 2 or type(row[0]) is not int or type(row[1]) is not str:
            raise ValueError("ledger_frontier_invalid")
        return Frontier(row[0], row[1])

    @staticmethod
    def _privacy_root(catalog: apsw.Connection, task_id: str) -> tuple[int, str]:
        row = catalog.execute(
            "SELECT root_generation, root_digest FROM privacy_root_sets WHERE task_id=?",
            (task_id,),
        ).fetchone()
        if row is None:
            return 0, _ZERO_DIGEST
        if len(row) != 2 or type(row[0]) is not int or type(row[1]) is not str:
            raise ValueError("privacy_root_invalid")
        return row[0], row[1]


async def _root_snapshot(
    inspection: _BundleInspection,
    db: apsw.Connection,
    clock: ClockPort,
) -> ObjectRootSnapshot:
    object_ids = tuple(
        row[0]
        for row in db.execute(
            "SELECT object_id FROM objects WHERE state='present' ORDER BY object_id"
        ).fetchall()
        if len(row) == 1 and type(row[0]) is str
    )
    object_digest = canonical_digest(object_ids)
    state = cast(recovery_module.RecoveryState, inspection.recovery_state)
    return ObjectRootSnapshot(
        task_id=state.task_id,
        route_identity_digest=state.route_identity_digest,
        route_generation=state.route_generation,
        bundle_generation=max(1, state.owner_generation),
        privacy_root_generation=state.privacy_root_generation,
        ledger_roots_digest=object_digest,
        importer_roots_digest=_ZERO_DIGEST,
        privacy_roots_digest=state.privacy_root_digest,
        maintenance_pin_digest=_ZERO_DIGEST,
        captured_at=clock.now_utc(),
        live_object_ids=object_ids,
    )


def _initialize_fresh_bundle(path: Path, *, command: object, clock: ClockPort) -> None:
    db = _open_recovery_writer(path)
    try:
        initialize_bundle(
            db,
            {
                "owner_generation": "0",
                "owner_nonce": _nonce(),
                "protocol_version": "0.1",
                "route_generation": str(getattr(command, "route_generation")),
                "route_identity_digest": cast(str, getattr(command, "route_identity_digest")),
                "storage_schema_version": "1",
                "task_id": cast(str, getattr(command, "task_id")),
                "updated_at": format_rfc3339_millis(clock.now_utc()),
            },
        )
    finally:
        _close_db(db)


def _admitted_writers_for_session(catalog_db: apsw.Connection, session_id: str) -> frozenset[str]:
    """Load durable writer IDs attached to one active session from completed starts."""

    rows = catalog_db.execute(
        "SELECT DISTINCT writer_id FROM start_operations "
        "WHERE session_id = ? AND state = 'complete'",
        (session_id,),
    ).fetchall()
    writers = frozenset(cast(str, row[0]) for row in rows)
    if any(type(item) is not str for item in writers):
        raise ValueError("admitted_writer_ids_invalid")
    return writers


def _inspect_common(
    *,
    catalog_path: Path,
    bundle_base: Path,
    route: object,
    admitted_writer_ids: frozenset[str],
    fresh_allocation: bool,
) -> _BundleInspection:
    task_id = cast(str, getattr(route, "task_id"))
    bundle_root = _safe_bundle_root(
        bundle_base, cast(str, getattr(route, "bundle_relpath")), task_id
    )
    state = recovery_module.inspect_recovery_state(
        bundle_root,
        catalog_path=catalog_path,
        task_id=task_id,
        route_generation=cast(int, getattr(route, "route_generation")),
        route_identity_digest=cast(str, getattr(route, "route_identity_digest")),
    )
    verdict = recovery_module.validate_recovery_tail(state)
    if type(admitted_writer_ids) is not frozenset or any(
        type(item) is not str for item in admitted_writer_ids
    ):
        raise ValueError("admitted_writer_ids_invalid")
    return _BundleInspection(
        route,
        bundle_root,
        bundle_root / _LEDGER_NAME,
        catalog_path,
        admitted_writer_ids,
        fresh_allocation,
        state,
        verdict,
    )


def build_runtime_adapter_factories(
    *,
    paths: _Paths,
    service_instance_id: str,
    service_generation: int,
    clock: ClockPort,
    ids: IdPort,
    secret_memory: object,
    catalog_db: apsw.Connection,
) -> RuntimeAdapterFactories:
    """Build durable local runtime adapter callbacks for one ready generation."""

    _install_sqlite_support_policy()
    catalog_path = _catalog_path(paths)
    _install_recovery_persistence(_RecoveryPersistence(catalog_path, clock))
    opened_dbs: dict[int, apsw.Connection] = {}
    object_root_dbs: dict[int, apsw.Connection] = {}

    async def inspect_route(route: object, access: RouteAccess) -> object:
        del access
        session_id = cast(str, getattr(route, "session_id"))
        return _inspect_common(
            catalog_path=catalog_path,
            bundle_base=paths.bundle,
            route=route,
            admitted_writer_ids=_admitted_writers_for_session(catalog_db, session_id),
            fresh_allocation=False,
        )

    async def inspect_provision(command: object) -> object:
        route = TaskRoute(
            task_id=cast(str, getattr(command, "task_id")),
            session_id=cast(str, getattr(command, "session_id")),
            bundle_relpath=cast(str, getattr(command, "bundle_relpath")),
            route_generation=cast(int, getattr(command, "route_generation")),
            route_identity_digest=cast(str, getattr(command, "route_identity_digest")),
            state=TaskRouteState.ACTIVE,
        )
        bundle_root = _safe_bundle_root(
            paths.bundle,
            cast(str, getattr(command, "bundle_relpath")),
            cast(str, getattr(command, "task_id")),
        )
        ledger_path = bundle_root / _LEDGER_NAME
        fresh = not ledger_path.exists()
        if fresh:
            bundle_root.mkdir(mode=0o700, parents=True, exist_ok=False)
            bundle_root.chmod(0o700)
            _initialize_fresh_bundle(ledger_path, command=command, clock=clock)
        writer_id = cast(str, getattr(command, "writer_id"))
        return _inspect_common(
            catalog_path=catalog_path,
            bundle_base=paths.bundle,
            route=route,
            admitted_writer_ids=frozenset({writer_id}),
            fresh_allocation=fresh,
        )

    async def acquire_fence(inspection: object, write: bool) -> OwnershipFence:
        if type(inspection) is not _BundleInspection:
            raise ValueError("runtime_inspection_invalid")
        if not write:
            state = cast(recovery_module.RecoveryState, inspection.recovery_state)
            fence = OwnershipFence(
                service_instance_id=service_instance_id,
                service_generation=service_generation,
                owner_generation=max(1, state.owner_generation),
                nonce=state.owner_nonce,
            )
            registrar = cast(
                Callable[[Path, OwnershipFence], None],
                getattr(connection_module, "_register_active_fence"),
            )
            registrar(inspection.ledger_path, fence)
            return fence
        return recovery_module.acquire_bundle_ownership(
            cast(recovery_module.RecoveryState, inspection.recovery_state),
            cast(recovery_module.RecoveryTailVerdict, inspection.recovery_verdict),
            service_instance_id=service_instance_id,
            service_generation=service_generation,
            owner_nonce=_nonce(),
            now=clock.now_utc(),
        )

    async def validate_fence(inspection: object, fence: OwnershipFence) -> None:
        if type(inspection) is not _BundleInspection:
            raise ValueError("runtime_inspection_invalid")
        cast(_RecoveryPersistence, recovery_module._backend()).verify_fence(  # pyright: ignore[reportPrivateUsage]
            inspection.recovery_state,
            fence,
        )

    async def open_objects(
        inspection: object,
        keys: BundleKeys | None,
        fence: OwnershipFence,
        access: RouteAccess,
    ) -> ObjectStorePort:
        del fence, access
        if type(inspection) is not _BundleInspection or keys is None:
            raise ValueError("runtime_object_store_invalid")
        db = open_read_only(inspection.ledger_path)
        try:
            store = EncryptedFilesObjectStore(
                bundle_root=inspection.bundle_root,
                bundle_keys=keys,
                secret_memory=secret_memory,  # pyright: ignore[reportArgumentType]
                id_port=ids,
                current_root_snapshot=lambda: _root_snapshot(inspection, db, clock),
            )
            object_root_dbs[id(store)] = db
            return store
        except BaseException:
            _close_db(db)
            raise

    async def open_ledger(
        inspection: object,
        objects: ObjectStorePort,
        fence: OwnershipFence,
        access: RouteAccess,
    ) -> LedgerPort:
        del access
        if type(inspection) is not _BundleInspection:
            raise ValueError("runtime_ledger_invalid")
        db = open_writer(inspection.ledger_path)
        try:
            ledger = SqliteLedger(
                db=db,
                task_id=cast(str, getattr(inspection.route, "task_id")),
                ownership_fence=fence,
                clock=clock,
                ids=ids,
                objects=objects,
            )
            opened_dbs[id(ledger)] = db
            return ledger
        except BaseException:
            _close_db(db)
            raise

    async def open_importer(
        inspection: object,
        objects: ObjectStorePort,
        ledger: LedgerPort,
        fence: OwnershipFence,
        access: RouteAccess,
    ) -> ImporterPort:
        del inspection, objects, ledger, fence, access
        return cast(ImporterPort, _MinimalImporter())

    async def verify_start(
        inspection: object,
        runtime: TaskRuntime,
        expectation: StartMilestoneExpectation,
    ) -> StartCompletionEvidence:
        if type(inspection) is not _BundleInspection:
            raise ValueError("runtime_inspection_invalid")
        frontier: Frontier | None = None
        if expectation.milestone is not StartMilestone.BUNDLE_READY:
            frontier = await runtime.ledger.load_frontier()
        owner_generation = runtime.fence.owner_generation
        frontier_value: CanonicalJsonValue = (
            None if frontier is None else cast(CanonicalJsonValue, dict(frontier.as_wire()))
        )
        value: dict[str, CanonicalJsonValue] = {
            "lifecycle_event_id": expectation.lifecycle_event_id,
            "lifecycle_frontier": frontier_value,
            "milestone": expectation.milestone.value,
            "owner_generation": owner_generation,
            "response_envelope_digest": expectation.response_envelope_digest,
            "response_object_id": expectation.response_object_id,
            "result_digest": expectation.result_digest,
            "route_generation": expectation.route_generation,
            "route_identity_digest": expectation.route_identity_digest,
            "session_id": expectation.session_id,
            "task_id": expectation.task_id,
            "writer_id": expectation.writer_id,
        }
        return StartCompletionEvidence(
            expectation.milestone,
            expectation.task_id,
            expectation.session_id,
            expectation.writer_id,
            expectation.lifecycle_event_id,
            expectation.route_generation,
            expectation.route_identity_digest,
            owner_generation,
            frontier,
            expectation.response_object_id,
            expectation.response_envelope_digest,
            expectation.result_digest,
            canonical_digest(value),
        )

    async def close_entry(
        inspection: object,
        objects: ObjectStorePort | None,
        ledger: LedgerPort | None,
        importer: object | None,
        fence: OwnershipFence | None,
    ) -> None:
        del importer
        if objects is not None:
            _close_db(object_root_dbs.pop(id(objects), None))
        if ledger is not None:
            _close_db(opened_dbs.pop(id(ledger), None))
        if fence is not None:
            if type(inspection) is not _BundleInspection:
                return
            clearer = cast(
                Callable[[Path, OwnershipFence | None], None],
                getattr(connection_module, "_clear_active_fence"),
            )
            clearer(inspection.ledger_path, fence)

    return RuntimeAdapterFactories(
        current_service_generation=lambda: service_generation,
        inspect_route=inspect_route,
        inspect_provision=inspect_provision,
        acquire_fence=acquire_fence,
        validate_fence=validate_fence,
        open_objects=open_objects,
        open_ledger=open_ledger,
        open_importer=open_importer,
        verify_start=verify_start,
        close_entry=close_entry,
    )


# The agent-context allowlist shipped before ADR-009 permitted verification output by default.
# Kept only to recognize an installation still carrying that untouched seed; see
# `_reseed_untouched_default_policy`.
_LEGACY_AGENT_CONTEXT_CATEGORIES: Final = (
    DataCategory.BOUNDED_STRUCTURAL_METADATA,
    DataCategory.DECLARED_FILE_TYPE,
)
_LEGACY_AGENT_CONTEXT_DATA_CLASSES: Final = (DataClass.PUBLIC_STRUCTURAL,)

# Bootstrap seed identity. The revision names the shipped default's contents, so widening the
# default mints a distinct `policy_digest` instead of two different payloads sharing one digest:
# that digest is the CAS precondition for later tightenings and is exposed as `effective_digest`.
# `None` reproduces the pre-ADR-009 payload and exists only to recognize an untouched old seed.
_BOOTSTRAP_SEED_SCHEMA: Final = "yoetz.privacy-policy.bootstrap/1"
_BOOTSTRAP_DEFAULT_REVISION: Final = "2026-07-24-verification-output"


def _bootstrap_seed_digest(installation_id: str, *, revision: str | None) -> str:
    payload: dict[str, CanonicalJsonValue] = {
        "installation_id": installation_id,
        "profile": "local_only",
        "schema": _BOOTSTRAP_SEED_SCHEMA,
    }
    if revision is not None:
        payload["default_revision"] = revision
    return canonical_digest(payload)


def _shipped_default_policy(policy: PrivacyPolicy, *, revision: str | None) -> PrivacyPolicy:
    """Rebuild a shipped default policy at one revision under an existing policy's identity."""

    rebuilt = _denied_policy(
        installation_id=policy.effective_scope.installation_id,
        policy_id=policy.policy_id,
        policy_digest=_bootstrap_seed_digest(
            policy.effective_scope.installation_id, revision=revision
        ),
        created_at=policy.created_at,
    )
    if revision is not None:
        return rebuilt
    return replace(
        rebuilt,
        agent_context_categories=_LEGACY_AGENT_CONTEXT_CATEGORIES,
        agent_context_data_classes=_LEGACY_AGENT_CONTEXT_DATA_CLASSES,
    )


async def _reseed_untouched_default_policy(
    policies: CatalogPrivacyPolicyStore,
    scope: AuthorizationScope,
    policy: PrivacyPolicy,
) -> PrivacyPolicy:
    """Carry an untouched pre-ADR-009 default forward to the current shipped default.

    Without this, an installation seeded before the default widened keeps the narrow
    agent-context allowlist and still cannot read its own receipts, so the receipt fix would
    reach only new installations. Recognition is exact in every field, including the bootstrap
    seed digest, and the store additionally requires first-run seed provenance, so an owner
    policy that reproduces the old default's contents is left alone.
    """

    legacy_default = replace(_shipped_default_policy(policy, revision=None), version=policy.version)
    if policy != legacy_default:
        return policy
    replacement = replace(
        _shipped_default_policy(policy, revision=_BOOTSTRAP_DEFAULT_REVISION),
        version=policy.version + 1,
    )
    return await policies.reseed_untouched_bootstrap_default(
        scope, expected_current=policy, replacement=replacement
    )


def _denied_policy(
    *,
    installation_id: str,
    policy_id: str,
    policy_digest: str,
    created_at: datetime,
) -> PrivacyPolicy:
    safe = safe_privacy_bootstrap()
    channels = tuple(
        ChannelPolicy(
            channel=channel,
            enabled=False,
            allowed_categories=(),
            allowed_data_classes=(),
            provider_binding=None,
            allowed_purposes=(),
            scope_ceiling=AuthorizationScopeKind.MACHINE,
            preview_required=False,
            max_bytes=0,
            max_tokens=0,
            authorization_ttl_seconds=0,
        )
        for channel in sorted(EgressChannel, key=lambda item: item.value)
    )
    if safe.network_egress_permitted or safe.local_model_enabled:
        raise ValueError("privacy_bootstrap_unsafe")
    return PrivacyPolicy(
        policy_id=policy_id,
        version=1,
        policy_digest=policy_digest,
        profile=PrivacyProfile.LOCAL_ONLY,
        review_context_profile=ReviewContextProfile.STRUCTURAL,
        review_selection=ReviewSelectionPolicy.for_profile(ReviewContextProfile.STRUCTURAL),
        require_current_provider_data_use_evidence=False,
        network_egress_permitted=False,
        effective_scope=AuthorizationScope(AuthorizationScopeKind.MACHINE, installation_id),
        channel_policies=channels,
        local_model_enabled=False,
        local_model_binding=None,
        local_model_categories=(),
        local_model_data_classes=(),
        # Default LOCAL_ONLY: agent context may receive Yoetz-authored verification
        # projection content (findings, obligations, receipt sections/human_text) for the
        # requesting agent's own task. Observation-derived and vault material stay blocked.
        # See ADR-009 default agent-context disclosure of verification output.
        agent_context_categories=(
            DataCategory.BOUNDED_STRUCTURAL_METADATA,
            DataCategory.DECLARED_FILE_TYPE,
            DataCategory.FINDING_SUMMARY,
            DataCategory.OBLIGATION_TEXT,
        ),
        agent_context_data_classes=(
            DataClass.PUBLIC_STRUCTURAL,
            DataClass.ORDINARY_USER_CONTENT,
        ),
        trusted_human_control_categories=tuple(DataCategory),
        trusted_human_control_data_classes=(
            DataClass.ORDINARY_USER_CONTENT,
            DataClass.PUBLIC_STRUCTURAL,
            DataClass.SENSITIVE_CONFIDENTIAL,
        ),
        created_at=created_at,
    )


async def build_privacy_coordinator(
    *,
    catalog_db: apsw.Connection,
    installation_id: str,
    service_generation: int,
    vault_generation: int,
    vault: _Vault,
    clock: ClockPort,
    ids: IdPort,
    config: YoetzConfig | None = None,
) -> tuple[object, PrivacyPolicy, object]:
    """Build and reconcile the fail-closed local privacy coordinator."""

    policies = CatalogPrivacyPolicyStore(catalog_db, clock)
    classifier = LocalPrivacyEnforcer()
    audit_key = vault.installation_mac_handle(MacKeyPurpose.PRIVACY_AUDIT)
    audit = CatalogPrivacyAudit(
        catalog_db,
        cast(ObjectStorePort, _PrivacyContentObjectStore(ids)),
        audit_key,  # pyright: ignore[reportArgumentType]
        clock,
        service_generation=service_generation,
    )
    builders = external_factory_builders_from_config(
        None if config is None else config.provider, clock=clock
    )
    gateway = PolicyEnforcingOutboundGateway(
        external_factory_builders=builders,  # type: ignore[arg-type]
        local_model_registry=InstalledLocalModelProfileRegistry(),
        local_model_resolver=None,
        credential_minter=_CredentialMinter(vault),
        audit=audit,
        classifier=classifier,
        audit_mac=audit_key,  # pyright: ignore[reportArgumentType]
        clock=clock,
        ids=ids,
    )
    machine_scope = AuthorizationScope(AuthorizationScopeKind.MACHINE, installation_id)
    # Bootstrap seed is first-run only. Later unlocks must reuse the durable machine policy;
    # minting a fresh policy_id/created_at each ready build would conflict with seed_if_absent's
    # identity-equal check and fail unlock after the first successful ready activation.
    try:
        effective = await policies.effective_policy(machine_scope)
        policy = await _reseed_untouched_default_policy(policies, machine_scope, effective.policy)
        if policy is not effective.policy:
            effective = await policies.effective_policy(machine_scope)
    except ValueError as exc:
        if exc.args != ("privacy_policy_missing",):
            raise
        seed_digest = _bootstrap_seed_digest(installation_id, revision=_BOOTSTRAP_DEFAULT_REVISION)
        try:
            policy = await seed_policy_if_absent(
                _denied_policy(
                    installation_id=installation_id,
                    policy_id=ids.new(IdKind.PRIVACY_POLICY),
                    policy_digest=seed_digest,
                    created_at=clock.now_utc(),
                ),
                policies,
            )
        except ValueError as seed_exc:
            # Concurrent first-run race: another ready build committed a different identity.
            # Never overwrite; load the durable winner.
            if seed_exc.args != ("privacy_policy_seed_conflict",):
                raise
            effective = await policies.effective_policy(machine_scope)
            policy = effective.policy
        else:
            effective = await policies.effective_policy(machine_scope)
    authority = HumanAuthorityCapability(
        "established_passphrase",
        canonical_digest(
            {
                "service_generation": str(service_generation),
                "source": "established_passphrase",
                "vault_generation": str(vault_generation),
                "vault_mode": str(getattr(vault.mode, "value", vault.mode)),
            }
        ),
        service_generation,
        str(getattr(vault.mode, "value", vault.mode)),
        vault_generation,
        True,
    )
    await gateway.reconcile_policy(effective, authority)
    coordinator = PrivacyCoordinator(
        policies,
        classifier,
        audit,
        gateway,
        clock,
        ids,
        service_generation=service_generation,
    )
    policy_app = PrivacyPolicyApplication(
        policies,
        audit,
        gateway,
        clock,
        ids,
        machine_scope,
    )
    coordinator.bind_policy_application(policy_app)
    return (
        coordinator,
        policy,
        gateway,
    )


def _version_json() -> Mapping[str, CanonicalJsonValue]:
    value = strict_json_parse(
        version_manifest_json(build_version_manifest(), include_resources=False)
    )
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        raise ValueError("version_manifest_invalid")
    return cast(Mapping[str, CanonicalJsonValue], value)


def _receipt_versions(manifest: Mapping[str, CanonicalJsonValue]) -> ReceiptVersionSlice:
    policies = manifest["policy_versions"]
    schemas = manifest["request_result_schema_versions"]
    if type(policies) is not list or not isinstance(schemas, Mapping):
        raise ValueError("version_manifest_invalid")
    policy_versions: list[PolicyVersionEntry] = []
    for item in policies:
        if type(item) is not str:
            raise ValueError("version_manifest_invalid")
        parts = item.split("/", 1)
        if len(parts) != 2:
            raise ValueError("version_manifest_invalid")
        policy_versions.append(PolicyVersionEntry(parts[0], parts[1]))
    schema_versions: list[SchemaVersionEntry] = []
    for schema_id, schema_version in schemas.items():
        if type(schema_id) is not str or type(schema_version) is not str:
            raise ValueError("version_manifest_invalid")
        schema_versions.append(SchemaVersionEntry(schema_id, schema_version))
    return ReceiptVersionSlice(
        package_name="yoetz",
        package_version=cast(str, manifest["package_version"]),
        protocol_version=cast(str, manifest["protocol_version"]),
        engine_version=cast(str, manifest["engine_version"]),
        projection_version=cast(str, manifest["projection_version"]),
        object_format_version=cast(str, manifest["object_format_version"]),
        catalog_schema_version=cast(str, manifest["catalog_schema_version"]),
        bundle_schema_version=cast(str, manifest["bundle_schema_version"]),
        policy_versions=tuple(sorted(policy_versions, key=lambda item: item.policy_id.encode())),
        schema_versions=tuple(sorted(schema_versions, key=lambda item: item.schema_id.encode())),
        resource_manifest_digest=cast(str, manifest["resource_manifest_digest"]),
    )


async def _semantic_not_configured(
    frozen: FrozenCase, findings: tuple[object, ...]
) -> FinalSemanticEvaluation:
    """Explicit path when semantic review is enabled but no provider endpoint is bound."""

    del frozen, findings
    return FinalSemanticEvaluation(
        SemanticStatus.NOT_CONFIGURED, SemanticReason.PROVIDER_NOT_CONFIGURED
    )


async def _semantic_credential_unavailable(
    frozen: FrozenCase, findings: tuple[object, ...]
) -> FinalSemanticEvaluation:
    """Endpoint is bound but no provider credential is connected in this generation."""

    del frozen, findings
    return FinalSemanticEvaluation(
        SemanticStatus.UNAVAILABLE, SemanticReason.CREDENTIAL_UNAVAILABLE
    )


def _map_blocked(outcome: PrivacyOutcome, reason: object) -> FinalSemanticEvaluation:
    """Map pre-dispatch privacy blocks to exact semantic status/reason pairs."""

    if outcome is PrivacyOutcome.CHANNEL_UNAVAILABLE:
        # Distinct from missing credential/endpoint: the channel is present but policy forbids it.
        return FinalSemanticEvaluation(
            SemanticStatus.BLOCKED_BY_POLICY, SemanticReason.CHANNEL_DISABLED
        )
    if outcome is PrivacyOutcome.BLOCKED_FORBIDDEN_DATA:
        return FinalSemanticEvaluation(
            SemanticStatus.BLOCKED_FORBIDDEN_DATA, SemanticReason.NEVER_SEND_DETECTED
        )
    if outcome is PrivacyOutcome.CLASSIFICATION_UNCERTAIN:
        return FinalSemanticEvaluation(
            SemanticStatus.CLASSIFICATION_UNCERTAIN, SemanticReason.CLASSIFICATION_UNCERTAIN
        )
    if outcome is PrivacyOutcome.HUMAN_DENIED:
        return FinalSemanticEvaluation(SemanticStatus.HUMAN_DENIED, SemanticReason.HUMAN_DENIED)
    if outcome is PrivacyOutcome.APPROVAL_EXPIRED:
        return FinalSemanticEvaluation(
            SemanticStatus.APPROVAL_EXPIRED, SemanticReason.HUMAN_APPROVAL_EXPIRED
        )
    if outcome is PrivacyOutcome.TIMEOUT:
        return FinalSemanticEvaluation(SemanticStatus.TIMEOUT, SemanticReason.PROVIDER_TIMEOUT)
    if outcome is PrivacyOutcome.AUDIT_FAILED:
        return FinalSemanticEvaluation(
            SemanticStatus.UNAVAILABLE, SemanticReason.AUDIT_RESERVATION_UNAVAILABLE
        )
    if outcome is PrivacyOutcome.TRANSPORT_FAILED:
        return FinalSemanticEvaluation(
            SemanticStatus.UNAVAILABLE, SemanticReason.TRANSPORT_UNAVAILABLE
        )
    if outcome is PrivacyOutcome.BLOCKED_BY_POLICY:
        reason_name = getattr(reason, "name", None)
        if reason_name == "PURPOSE_NOT_ALLOWED":
            return FinalSemanticEvaluation(
                SemanticStatus.BLOCKED_BY_POLICY, SemanticReason.CHANNEL_DISABLED
            )
        if reason_name == "DESTINATION_NOT_ALLOWED":
            return FinalSemanticEvaluation(
                SemanticStatus.BLOCKED_BY_POLICY,
                SemanticReason.PROVIDER_BINDING_NOT_AUTHORIZED,
            )
        if reason_name == "SCOPE_MISMATCH":
            return FinalSemanticEvaluation(
                SemanticStatus.BLOCKED_BY_POLICY, SemanticReason.SCOPE_NOT_AUTHORIZED
            )
        if reason_name == "CATEGORY_NOT_ALLOWED":
            return FinalSemanticEvaluation(
                SemanticStatus.BLOCKED_BY_POLICY,
                SemanticReason.CONTENT_CATEGORY_NOT_AUTHORIZED,
            )
        return FinalSemanticEvaluation(
            SemanticStatus.BLOCKED_BY_POLICY, SemanticReason.NETWORK_EGRESS_DENIED
        )
    return FinalSemanticEvaluation(SemanticStatus.FAILED, SemanticReason.COORDINATOR_FAILURE)


def _provider_provenance(
    result: SemanticEgressSuccess | SemanticEgressProviderOutcome,
    *,
    status: SemanticStatus,
    reason: SemanticReason,
    ids: IdPort,
) -> SemanticProvenance | None:
    """Bind a completed attempt to its dispatch-specific durable privacy authority."""

    if result.privacy_receipt_id is None:
        return None
    if result.dispatch_kind is SemanticDispatchKind.EXTERNAL:
        if result.authorization_id is None or result.request_commitment is None:
            return None
        egress_authorization_id = result.authorization_id
        local_disclosure_reservation_id = None
        request_commitment = result.request_commitment
    elif result.dispatch_kind is SemanticDispatchKind.LOCAL_MODEL:
        if result.authorization_id is not None or result.request_commitment is not None:
            return None
        egress_authorization_id = None
        local_disclosure_reservation_id = result.privacy_proposal_id
        request_commitment = None
    else:
        return None
    attempt = result.result.provenance
    return SemanticProvenance(
        provider=attempt.provider,
        endpoint_profile_id=attempt.endpoint_profile_id,
        endpoint_profile_version=attempt.endpoint_profile_version,
        model=attempt.model,
        sdk_version=attempt.sdk_version,
        prompt_digest=attempt.prompt_digest,
        schema_digest=attempt.schema_digest,
        policy_digest=attempt.policy_digest,
        privacy_policy_digest=attempt.privacy_policy_digest,
        sampling_params=attempt.sampling_params,
        latency_ms=attempt.latency_ms,
        semantic_attempt_id=ids.new(IdKind.SEMANTIC_ATTEMPT),
        dispatch_kind=result.dispatch_kind,
        privacy_receipt_id=result.privacy_receipt_id,
        status=status,
        reason=reason,
        provider_request_id=attempt.provider_request_id,
        token_usage=attempt.token_usage,
        cost_fields=attempt.cost_fields,
        failure_class=attempt.failure_class,
        egress_authorization_id=egress_authorization_id,
        local_disclosure_reservation_id=local_disclosure_reservation_id,
        request_commitment=request_commitment,
    )


def _map_provider_outcome(
    result: SemanticEgressProviderOutcome, ids: IdPort
) -> FinalSemanticEvaluation:
    provider = result.result
    status: SemanticStatus
    reason: SemanticReason
    if type(provider) is SemanticResultRefused:
        status, reason = SemanticStatus.REFUSED, SemanticReason.PROVIDER_REFUSED
    elif type(provider) is SemanticResultTimeout:
        status, reason = SemanticStatus.TIMEOUT, SemanticReason.PROVIDER_TIMEOUT
    elif type(provider) is SemanticResultInvalid:
        status, reason = SemanticStatus.INVALID, SemanticReason.RESPONSE_SCHEMA_INVALID
    elif type(provider) is SemanticResultLate:
        status, reason = SemanticStatus.LATE, SemanticReason.DEADLINE_AUTHORITY_LOST
    elif type(provider) is SemanticResultUnavailable:
        status, reason = SemanticStatus.UNAVAILABLE, SemanticReason.TRANSPORT_UNAVAILABLE
    else:
        return FinalSemanticEvaluation(SemanticStatus.FAILED, SemanticReason.COORDINATOR_FAILURE)
    provenance = _provider_provenance(result, status=status, reason=reason, ids=ids)
    if provenance is None:
        return FinalSemanticEvaluation(
            SemanticStatus.UNAVAILABLE, SemanticReason.RECEIPT_PERSISTENCE_UNKNOWN
        )
    return FinalSemanticEvaluation(status, reason, provenance=provenance)


def _map_egress_to_final(result: object, ids: IdPort) -> FinalSemanticEvaluation:
    """Map privacy egress outcomes to check FinalSemanticEvaluation without inventing findings."""

    if type(result) is SemanticEgressSuccess:
        provenance = _provider_provenance(
            result,
            status=SemanticStatus.SUCCEEDED,
            reason=SemanticReason.SEMANTIC_COMPLETED,
            ids=ids,
        )
        if provenance is None:
            return FinalSemanticEvaluation(
                SemanticStatus.UNAVAILABLE, SemanticReason.RECEIPT_PERSISTENCE_UNKNOWN
            )
        return FinalSemanticEvaluation(
            SemanticStatus.SUCCEEDED,
            SemanticReason.SEMANTIC_COMPLETED,
            judgment=result.result.judgment,
            provenance=provenance,
        )
    if type(result) is SemanticEgressAwaitingHuman:
        return FinalSemanticEvaluation(
            SemanticStatus.AWAITING_HUMAN, SemanticReason.HUMAN_APPROVAL_REQUIRED
        )
    if type(result) is SemanticEgressBlocked:
        return _map_blocked(result.outcome, result.reason)
    if type(result) is SemanticEgressProviderOutcome:
        return _map_provider_outcome(result, ids)
    return FinalSemanticEvaluation(SemanticStatus.FAILED, SemanticReason.COORDINATOR_FAILURE)


def _privacy_gated_semantic_evaluator(
    privacy: PrivacyCoordinator,
    clock: ClockPort,
    installation_id: str,
    provider: ProviderBinding | None,
    catalog: StartCatalogPort,
    lookup: MacKeyHandle,
    ids: IdPort,
):
    async def _evaluate(
        frozen: FrozenCase, findings: tuple[object, ...]
    ) -> FinalSemanticEvaluation:
        if provider is None:
            return await _semantic_not_configured(frozen, findings)
        try:
            route = await catalog.resolve_route(frozen.lease.session_id)
            if route is None or route.state is not TaskRouteState.ACTIVE:
                return await _semantic_not_configured(frozen, findings)
            workspace = lookup.mac(
                WORKSPACE_REF_DOMAIN,
                f"{route.route_identity_digest}\x00{route.task_id}".encode("ascii"),
            )
            scope = AuthorizationScope(
                AuthorizationScopeKind.TASK,
                installation_id,
                workspace,
                route.task_id,
            )
            finding_ids: list[str] = []
            for finding in findings:
                finding_id = getattr(finding, "finding_id", None)
                if type(finding_id) is str:
                    finding_ids.append(finding_id)
            case = frozen.case
            payload = canonical_encode(
                cast(
                    CanonicalJsonValue,
                    {
                        "dependency_digest": frozen.lease.dependency_digest,
                        "findings": finding_ids,
                        "frontier": {
                            "head_digest": case.frontier.head_digest,
                            "sequence": case.frontier.sequence,
                        },
                        "schema": "yoetz.semantic-check-candidate/1",
                    },
                )
            )
            candidate = CandidateContext(
                request_id=frozen.lease.operation_id,
                channel=EgressChannel.LLM_INFERENCE,
                local_sink=None,
                purpose="semantic-review",
                scope=scope,
                subject_digest=canonical_digest(
                    cast(
                        CanonicalJsonValue,
                        {
                            "frontier": {
                                "head_digest": case.frontier.head_digest,
                                "sequence": case.frontier.sequence,
                            },
                            "schema": "yoetz.semantic-check-subject/1",
                        },
                    )
                ),
                provider_binding=provider,
                items=(
                    CandidateContextItem(
                        "case-packet",
                        DataCategory.BOUNDED_STRUCTURAL_METADATA,
                        scope,
                        "/case",
                        payload,
                    ),
                ),
            )
            deadline = Deadline(clock.now_utc(), clock.monotonic_seconds() + 60.0)
            result = await privacy.evaluate_semantic(candidate, deadline)
            return _map_egress_to_final(result, ids)
        except Exception as exc:
            record_unexpected_exception_without_raising(
                exc,
                component="semantic_composition",
                operation="semantic_evaluation_failed",
                request_id=frozen.lease.operation_id,
            )
            return FinalSemanticEvaluation(
                SemanticStatus.FAILED, SemanticReason.COORDINATOR_FAILURE
            )

    return _evaluate


def _profile(config: YoetzConfig) -> RuntimeProfile:
    return RuntimeProfile(config.profile)


def _policy_packs(manifest: Mapping[str, CanonicalJsonValue]) -> tuple[str, ...]:
    values = manifest["policy_versions"]
    if type(values) is not list or any(type(item) is not str for item in values):
        raise ValueError("version_manifest_invalid")
    return tuple(cast(list[str], values))


async def provide_service_ready_context(
    service_generation: int,
    vault_generation: int,
    *,
    lifecycle: _Lifecycle,
    vault: _Vault,
    config: YoetzConfig,
    paths: _Paths,
    clock: ClockPort,
    secret_memory: object,
    diagnostics: DiagnosticsPort | None = None,
) -> ServiceReadyContext:
    """Compose one generation-bound ready application context."""

    if not vault.ready or vault.generation != vault_generation:
        raise ControlError("vault_locked", retryable=True)
    ensure_owner_only_dir(paths.bundle)
    verify_private_local_bundle(paths.bundle)
    ids = IdPort()
    lookup = vault.installation_mac_handle(MacKeyPurpose.CATALOG_LOOKUP)
    installation_id = cast(str, getattr(vault, "_installation_id"))
    catalog = await open_ready_catalog(
        _catalog_path(paths),
        installation_id=installation_id,
        service_generation=service_generation,
        lookup=lookup,
        clock=clock,
        ids=ids,
    )
    manifest = _version_json()
    privacy, policy, gateway = await build_privacy_coordinator(
        catalog_db=cast(apsw.Connection, getattr(catalog, "_db")),
        installation_id=installation_id,
        service_generation=service_generation,
        vault_generation=vault_generation,
        vault=vault,
        clock=clock,
        ids=ids,
        config=config,
    )
    provider_factory_ids = cast(
        tuple[str, ...], tuple(getattr(gateway, "configured_provider_ids", lambda: ())())
    )
    connected_provider_ids = cast(
        tuple[str, ...], tuple(getattr(gateway, "connected_provider_ids", lambda: ())())
    )
    semantic_configured = config.verification.semantic != "disabled"
    provider_endpoint_bound = config.provider is not None
    # Resolve the binding before readiness: a connected provider that is not the *configured* one
    # leaves dispatch on the credential-unavailable path, so readiness must track the exact binding
    # rather than "any provider connected".
    provider_binding: ProviderBinding | None = None
    if config.provider is not None:
        candidate_binding = provider_binding_from_config(config.provider)
        if candidate_binding.provider_id in connected_provider_ids:
            provider_binding = candidate_binding
    provider_credential_connected = provider_binding is not None
    semantic_ready = (
        semantic_configured and provider_endpoint_bound and provider_credential_connected
    )
    capabilities = {
        RuntimeCapability.STRUCTURAL_READ,
        RuntimeCapability.PAYLOAD_READ,
        RuntimeCapability.WRITE,
    }
    # Expose SEMANTIC when the operator has not disabled it so check can report *why* it is
    # unusable (no endpoint vs no credential vs policy), instead of one opaque not_configured.
    if semantic_configured:
        capabilities.add(RuntimeCapability.SEMANTIC)
    runtime_context = ServiceRuntimeContext(
        service_instance_id=cast(str, getattr(lifecycle.instance, "instance_id")),
        service_generation=service_generation,
        vault_generation=vault_generation,
        catalog_generation=catalog.generation,
        capabilities=frozenset(capabilities),
        version_manifest=cast(Mapping[str, DomainJsonValue], manifest),
        shutdown_token=object(),
    )
    factories = build_runtime_adapter_factories(
        paths=paths,
        service_instance_id=runtime_context.service_instance_id,
        service_generation=service_generation,
        clock=clock,
        ids=ids,
        secret_memory=secret_memory,
        catalog_db=cast(apsw.Connection, getattr(catalog, "_db")),
    )
    runtime = await open_local_bundle_runtime(
        runtime_context,
        catalog,
        vault,
        factories,
        diagnostics or _NullDiagnostics(),
        manifest,
    )

    def generation_is_current(current_service: int, current_vault: int) -> bool:
        return (
            current_service == service_generation
            and current_vault == vault_generation
            and vault.ready
            and vault.generation == vault_generation
        )

    def disclosure_scope_for(
        binding: ControlProjectionBinding, source: Mapping[str, CanonicalJsonValue]
    ) -> AuthorizationScope:
        task = source.get("task_id")
        if type(task) is str and binding.route_identity_digest is not None:
            workspace = lookup.mac(
                WORKSPACE_REF_DOMAIN,
                f"{binding.route_identity_digest}\x00{task}".encode("ascii"),
            )
            return AuthorizationScope(
                AuthorizationScopeKind.TASK,
                installation_id,
                workspace,
                task,
            )
        return AuthorizationScope(
            AuthorizationScopeKind.MACHINE,
            installation_id,
        )

    versions = _receipt_versions(manifest)
    if not semantic_configured:
        semantic_evaluator = _semantic_not_configured
    elif not provider_endpoint_bound:
        semantic_evaluator = _semantic_not_configured
    elif provider_binding is None:
        semantic_evaluator = _semantic_credential_unavailable
    else:
        semantic_evaluator = _privacy_gated_semantic_evaluator(
            cast(PrivacyCoordinator, privacy),
            clock,
            installation_id,
            provider_binding,
            catalog,
            lookup,
            ids,
        )

    async def _semantic_review(
        candidates: tuple[ObservationAdviceCandidate, ...],
        basis: str,
        gaps: tuple[str, ...],
    ) -> ObservationAdviceSemanticAddon | None:
        # Privacy-gated observation semantic path: authorize/dispatch through the coordinator.
        # Provider failure or no-discrepancy leaves deterministic advice intact (no upgrade).
        del gaps
        if not semantic_ready or provider_binding is None:
            return None
        packet = minimized_semantic_evidence_packet(
            candidates,
            basis,
            coverage_gaps=(),
            finding_summaries=tuple(str(item.rule_code) for item in candidates),
        )
        try:
            payload = canonical_encode(cast(CanonicalJsonValue, dict(packet)))
            subject = (
                basis
                if basis.startswith("sha256:")
                else canonical_digest(cast(CanonicalJsonValue, {"basis": basis}))
            )
            machine_scope = AuthorizationScope(AuthorizationScopeKind.MACHINE, installation_id)
            candidate = CandidateContext(
                request_id=ids.new(IdKind.REQUEST),
                channel=EgressChannel.LLM_INFERENCE,
                local_sink=None,
                purpose="semantic-review",
                scope=machine_scope,
                subject_digest=subject,
                provider_binding=provider_binding,
                items=(
                    CandidateContextItem(
                        "observation-advice-packet",
                        DataCategory.BOUNDED_STRUCTURAL_METADATA,
                        machine_scope,
                        "/observation-advice",
                        payload,
                    ),
                ),
            )
            deadline = Deadline(clock.now_utc(), clock.monotonic_seconds() + 60.0)
            result = await cast(PrivacyCoordinator, privacy).evaluate_semantic(candidate, deadline)
        except Exception:
            return None
        if type(result) is not SemanticEgressSuccess:
            return None
        judgment = result.result.judgment
        if judgment.conclusion != "challenges_returned" or not judgment.challenges:
            # Honest attempt receipt without inventing additive findings.
            return ObservationAdviceSemanticAddon(
                finding_ids=(),
                evidence_digest=basis,
                next_action=None,
                summaries=(),
                details=(),
                provider_identity=provider_binding.provider_id,
                attempt_receipt=result.privacy_receipt_id or result.authorization_id,
                failure_reason=None,
            )
        # Additive note only when the provider returned post-validated challenges.
        detail = f"challenges:{len(judgment.challenges)}"
        digest = canonical_digest(
            cast(
                CanonicalJsonValue,
                {
                    "basis": basis,
                    "authorization_id": result.authorization_id,
                    "challenges": len(judgment.challenges),
                },
            )
        )
        finding = stable_advice_finding_id("semantic_additive_review", detail, digest)
        return ObservationAdviceSemanticAddon(
            finding_ids=(finding,),
            evidence_digest=digest,
            next_action=None,
            summaries=("Privacy-gated semantic observation review",),
            details=(
                "Additive semantic note recorded after authorized provider attempt; "
                "deterministic findings unchanged.",
            ),
            provider_identity=provider_binding.provider_id,
            attempt_receipt=result.privacy_receipt_id or result.authorization_id,
            failure_reason=None,
        )

    verification_supervisor = ObservationVerificationSupervisor(
        service_generation=service_generation
    )
    observation_coordinator = ObservationCoordinator(
        runtime=runtime,
        local=LocalObservationStore(),
        clock=clock,
        ids=ids,
        advice_context_builder=ObservationAdviceContextBuilder(
            composition=ObservationCompositionFact(
                semantic_configured=semantic_configured,
                semantic_ready=semantic_ready,
                provider_factory_ids=provider_factory_ids,
                connected_provider_ids=connected_provider_ids,
            ),
            semantic_review=_semantic_review if semantic_configured else None,
        ),
        verification_supervisor=verification_supervisor,
    )
    observation_handlers = build_observation_support_handlers(observation_coordinator)
    support_handlers = dict(observation_handlers)
    privacy_app = cast(PrivacyCoordinator, privacy).policy_application
    if privacy_app is not None:
        support_handlers.update(build_privacy_support_handlers(privacy_app))
    return ServiceReadyContext(
        service_generation=service_generation,
        vault_generation=vault_generation,
        generation_is_current=generation_is_current,
        start_catalog=catalog,
        runtime=runtime,
        clock=clock,
        ids=ids,
        verification_policy=VerificationPolicy(
            semantic=config.verification.semantic,
            max_findings=config.verification.max_findings,
        ),
        privacy=privacy,  # pyright: ignore[reportArgumentType]
        status_cursor_key=os.urandom(32),
        waiver_policy_digest=policy.policy_digest,
        semantic_evaluator=semantic_evaluator,
        disclosure_scope_for=disclosure_scope_for,
        receipt_version_resolver=lambda _: versions,
        waiver_authorizer=lambda _: False,
        import_publication_authorizer=lambda _: False,
        profile=_profile(config),
        policy_packs=_policy_packs(manifest),
        version_manifest=manifest,
        support_handlers=support_handlers,
        verification_supervisor=verification_supervisor,
        rediscover_pending_verification=observation_coordinator.rediscover_pending_verification,
        connected_provider_ids=connected_provider_ids,
        provider_credential_connected=provider_credential_connected,
        semantic_ready=semantic_ready,
    )


def build_ready_application_factory(
    *,
    lifecycle: _Lifecycle,
    vault: _Vault,
    config: YoetzConfig,
    paths: _Paths,
    clock: ClockPort,
    secret_memory: object,
    diagnostics: DiagnosticsPort | None = None,
) -> ReadyApplicationFactory:
    return ReadyApplicationFactory(
        context_provider=lambda service_generation, vault_generation: provide_service_ready_context(
            service_generation,
            vault_generation,
            lifecycle=lifecycle,
            vault=vault,
            config=config,
            paths=paths,
            clock=clock,
            secret_memory=secret_memory,
            diagnostics=diagnostics,
        )
    )
