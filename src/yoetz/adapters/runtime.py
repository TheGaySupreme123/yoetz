"""Ready-service composition for generation-fenced local task bundles.

Concrete storage selection is deliberately absent from this module.  The daemon supplies one
fully explicit :class:`RuntimeAdapterFactories` value after its startup gate has verified the
installed adapter cell.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, cast

from yoetz.domain.values import Frontier
from yoetz.ports.diagnostics import DiagnosticsPort, RuntimeCapability
from yoetz.ports.importer import ImporterPort, ImportStatusSnapshot
from yoetz.ports.keys import BundleKeys, KeyStoreError, KeyStoreReason
from yoetz.ports.ledger import (
    CaseAvailabilityFacts,
    LedgerPort,
    LedgerRecord,
    ProjectionPage,
    ProjectionQuery,
    ProjectionState,
    ProjectionView,
    StoredProjection,
)
from yoetz.ports.objects import ObjectRef, ObjectStorePort
from yoetz.ports.observation import TaskObservationPort
from yoetz.ports.runtime import (
    BundleProvisionCommand,
    BundleProvisionMode,
    BundleRuntimePort,
    OwnershipFence,
    RouteAccess,
    RouteCommand,
    ServiceRuntimeContext,
    StartCompletionEvidence,
    StartMilestoneExpectation,
    TaskRuntime,
)
from yoetz.ports.start_catalog import TaskRoute, TaskRouteState
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError

__all__ = [
    "LocalBundleRuntime",
    "RuntimeAdapterFactories",
    "RuntimeCachePolicy",
    "open_local_bundle_runtime",
]


class _VaultService(Protocol):
    @property
    def generation(self) -> int: ...

    @property
    def ready(self) -> bool: ...

    async def load_bundle_keys(self, bundle_id: str) -> BundleKeys: ...

    async def create_bundle_keys(self, bundle_id: str) -> BundleKeys: ...


class _GenerationCatalog(Protocol):
    @property
    def generation(self) -> int: ...

    async def resolve_route(self, session_id: str) -> TaskRoute | None: ...


class _RouteInspection(Protocol):
    """Private structural result of exact path/schema/recovery inspection."""

    @property
    def route(self) -> TaskRoute: ...

    @property
    def admitted_writer_ids(self) -> frozenset[str]: ...

    @property
    def fresh_allocation(self) -> bool: ...


type CurrentGeneration = Callable[[], int]
type InspectRoute = Callable[[TaskRoute, RouteAccess], Awaitable[object]]
type InspectProvision = Callable[[BundleProvisionCommand], Awaitable[object]]
type AcquireFence = Callable[[object, bool], Awaitable[OwnershipFence]]
type ValidateFence = Callable[[object, OwnershipFence], Awaitable[None]]
type OpenObjects = Callable[
    [object, BundleKeys | None, OwnershipFence, RouteAccess],
    Awaitable[ObjectStorePort],
]
type OpenLedger = Callable[
    [object, ObjectStorePort, OwnershipFence, RouteAccess],
    Awaitable[LedgerPort],
]
type OpenImporter = Callable[
    [object, ObjectStorePort, LedgerPort, OwnershipFence, RouteAccess],
    Awaitable[ImporterPort],
]
type VerifyStart = Callable[
    [object, TaskRuntime, StartMilestoneExpectation],
    Awaitable[StartCompletionEvidence],
]
type CloseEntry = Callable[
    [
        object,
        ObjectStorePort | None,
        LedgerPort | None,
        ImporterPort | None,
        OwnershipFence | None,
    ],
    Awaitable[None],
]


@dataclass(frozen=True, slots=True)
class RuntimeAdapterFactories:
    """One verified, explicit adapter composition selected by the ready daemon."""

    current_service_generation: CurrentGeneration
    inspect_route: InspectRoute
    inspect_provision: InspectProvision
    acquire_fence: AcquireFence
    validate_fence: ValidateFence
    open_objects: OpenObjects
    open_ledger: OpenLedger
    open_importer: OpenImporter
    verify_start: VerifyStart
    close_entry: CloseEntry

    def __post_init__(self) -> None:
        values = (
            self.current_service_generation,
            self.inspect_route,
            self.inspect_provision,
            self.acquire_fence,
            self.validate_fence,
            self.open_objects,
            self.open_ledger,
            self.open_importer,
            self.verify_start,
            self.close_entry,
        )
        if any(not callable(value) for value in values):
            raise TypeError("runtime_factory_invalid")


@dataclass(frozen=True, slots=True)
class RuntimeCachePolicy:
    max_idle_tasks: int = 8
    max_opening_tasks: int = 4

    def __post_init__(self) -> None:
        if (
            type(self.max_idle_tasks) is not int
            or not 1 <= self.max_idle_tasks <= 64
            or type(self.max_opening_tasks) is not int
            or not 1 <= self.max_opening_tasks <= 16
        ):
            raise ValueError("runtime_cache_policy_invalid")


@dataclass(slots=True, repr=False)
class _Entry:
    inspection: _RouteInspection
    keys: BundleKeys | None
    fence: OwnershipFence
    objects: ObjectStorePort
    ledger: LedgerPort
    importer: ImporterPort
    authority: frozenset[RuntimeCapability]
    usages: int = 0
    poisoned: bool = False
    closed: bool = False


class _ObservationCapableLedger(Protocol):
    def open_observation_store(self) -> TaskObservationPort: ...


def _observation_for(ledger: LedgerPort) -> TaskObservationPort | None:
    """Expose the durable observation seam for WRITE-capable concrete ledgers.

    Read facades and ledger adapters without the public accessor simply have no
    observation seam; production ingest treats that as an unavailable store.
    """

    opener = getattr(ledger, "open_observation_store", None)
    if opener is None:
        return None
    return cast(_ObservationCapableLedger, ledger).open_observation_store()


class _ReadLedger:
    """A structural read facade with no mutator attributes."""

    __slots__ = ("_value",)

    def __init__(self, value: LedgerPort) -> None:
        self._value = value

    def load_events(
        self, session_id: str, *, after: int = 0, through: int | None = None
    ) -> AsyncIterator[LedgerRecord]:
        return self._value.load_events(session_id, after=after, through=through)

    async def load_projection(
        self, session_id: str, view: ProjectionView
    ) -> StoredProjection | None:
        return await self._value.load_projection(session_id, view)

    async def load_case_availability(
        self,
        session_id: str,
        frontier: object,
        projection: ProjectionState,
    ) -> CaseAvailabilityFacts:
        return await self._value.load_case_availability(
            session_id, cast(Frontier, frontier), projection
        )

    async def query_projection(self, query: ProjectionQuery) -> ProjectionPage:
        return await self._value.query_projection(query)


class _PayloadObjects:
    __slots__ = ("_value",)

    def __init__(self, value: ObjectStorePort) -> None:
        self._value = value

    def open_verified(self, ref: ObjectRef) -> AsyncIterator[bytes]:
        return self._value.open_verified(ref)


class _StructuralObjects:
    __slots__ = ()


class _StatusImporter:
    __slots__ = ("_value",)

    def __init__(self, value: ImporterPort) -> None:
        self._value = value

    async def status(self, session_id: str) -> ImportStatusSnapshot:
        return await self._value.status(session_id)


def _error(code: PublicErrorCode, message: str, *, retryable: bool) -> PublicOperationError:
    return PublicOperationError(code, message, retryable)


_STALE = "The ready service generation changed."
_BUSY = "The task is temporarily busy."


class LocalBundleRuntime(BundleRuntimePort):
    """One ready service's lazy task-runtime cache and writer authority."""

    def __init__(
        self,
        context: ServiceRuntimeContext,
        catalog: _GenerationCatalog,
        vault: _VaultService,
        factories: RuntimeAdapterFactories,
        diagnostics: DiagnosticsPort,
        cache_policy: RuntimeCachePolicy,
    ) -> None:
        self._context = context
        self._catalog = catalog
        self._vault = vault
        self._factories = factories
        self._diagnostics = diagnostics
        self._policy = cache_policy
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._opening: dict[str, asyncio.Task[_Entry]] = {}
        self._usages: dict[int, _Entry] = {}
        self._lock = asyncio.Lock()
        self._opening_limit = asyncio.Semaphore(cache_policy.max_opening_tasks)
        self._closed = False
        self._require_ready()
        self._versions()

    def _require_ready(self) -> None:
        current_service = self._factories.current_service_generation()
        if (
            self._closed
            or type(current_service) is not int
            or current_service != self._context.service_generation
        ):
            raise _error(PublicErrorCode.SERVICE_UNAVAILABLE, _STALE, retryable=True)
        if not self._vault.ready:
            raise _error(
                PublicErrorCode.VAULT_LOCKED,
                "The service vault is locked.",
                retryable=True,
            )
        if self._vault.generation != self._context.vault_generation:
            raise _error(PublicErrorCode.SERVICE_UNAVAILABLE, _STALE, retryable=True)
        if self._catalog.generation != self._context.catalog_generation:
            raise _error(PublicErrorCode.STORAGE_UNSAFE, _STALE, retryable=True)

    def _require_capabilities(self, required: frozenset[RuntimeCapability]) -> None:
        if not required.issubset(self._context.capabilities):
            raise _error(
                PublicErrorCode.STORAGE_UNSAFE,
                "The requested task capability is unavailable.",
                retryable=False,
            )

    async def route(self, command: RouteCommand) -> TaskRuntime:
        if type(command) is not RouteCommand:
            raise _error(PublicErrorCode.INVALID_REQUEST, "The route is invalid.", retryable=False)
        self._require_ready()
        self._require_capabilities(
            command.required_capabilities | self._authority_for(command.access)
        )
        route = await self._catalog.resolve_route(command.session_id)
        if route is None:
            raise _error(
                PublicErrorCode.SESSION_NOT_FOUND,
                "The requested task attachment was not found.",
                retryable=False,
            )
        if route.session_id != command.session_id or route.state is not TaskRouteState.ACTIVE:
            raise _error(
                PublicErrorCode.SESSION_CONFLICT,
                "The requested task attachment conflicts.",
                retryable=False,
            )
        inspection = cast(
            _RouteInspection, await self._factories.inspect_route(route, command.access)
        )
        self._validate_inspection(route, inspection, command.writer_id)
        entry = await self._entry_for(inspection, command.access)
        return await self._lease(
            entry, command.required_capabilities, command.access, command.writer_id
        )

    async def provision_start(self, command: BundleProvisionCommand) -> TaskRuntime:
        if type(command) is not BundleProvisionCommand:
            raise _error(
                PublicErrorCode.INVALID_REQUEST, "The start route is invalid.", retryable=False
            )
        self._require_ready()
        required = frozenset(
            {
                RuntimeCapability.STRUCTURAL_READ,
                RuntimeCapability.PAYLOAD_READ,
                RuntimeCapability.WRITE,
            }
        )
        self._require_capabilities(required)
        inspection = cast(_RouteInspection, await self._factories.inspect_provision(command))
        try:
            route = inspection.route
        except (AttributeError, TypeError) as exc:
            raise _error(
                PublicErrorCode.STORAGE_CORRUPT,
                "The start inspection is invalid.",
                retryable=False,
            ) from exc
        if (
            route.task_id != command.task_id
            or route.session_id != command.session_id
            or route.route_generation != command.route_generation
            or route.route_identity_digest != command.route_identity_digest
        ):
            raise _error(
                PublicErrorCode.STORAGE_CORRUPT,
                "The start route identity is inconsistent.",
                retryable=False,
            )
        self._validate_inspection(route, inspection, command.writer_id)
        entry = await self._entry_for(
            inspection,
            RouteAccess.WRITE,
            provision_mode=command.mode,
        )
        return await self._lease(entry, required, RouteAccess.WRITE, command.writer_id)

    def _validate_inspection(
        self,
        expected: TaskRoute,
        inspection: _RouteInspection,
        writer_id: str | None,
    ) -> None:
        try:
            inspected_route = inspection.route
            writers = inspection.admitted_writer_ids
            fresh = inspection.fresh_allocation
        except (AttributeError, TypeError) as exc:
            raise _error(
                PublicErrorCode.STORAGE_CORRUPT,
                "The task inspection is invalid.",
                retryable=False,
            ) from exc
        if type(inspected_route) is not TaskRoute or inspected_route != expected:
            raise _error(
                PublicErrorCode.STORAGE_CORRUPT,
                "The task route identity is inconsistent.",
                retryable=False,
            )
        if type(writers) is not frozenset or any(type(item) is not str for item in writers):
            raise _error(
                PublicErrorCode.STORAGE_CORRUPT, "The writer set is invalid.", retryable=False
            )
        if type(fresh) is not bool:
            raise _error(
                PublicErrorCode.STORAGE_CORRUPT,
                "The task allocation state is invalid.",
                retryable=False,
            )
        if writer_id is not None and writer_id not in writers:
            raise _error(
                PublicErrorCode.SESSION_CONFLICT,
                "The requested writer is not attached.",
                retryable=False,
            )

    async def _entry_for(
        self,
        inspection: _RouteInspection,
        access: RouteAccess,
        *,
        provision_mode: BundleProvisionMode | None = None,
    ) -> _Entry:
        task_id = inspection.route.task_id
        required_authority = self._authority_for(access)
        while True:
            self._require_ready()
            close_before_open: _Entry | None = None
            async with self._lock:
                entry = self._entries.get(task_id)
                if entry is not None and not entry.poisoned:
                    if self._same_route(
                        entry.inspection.route, inspection.route
                    ) and required_authority.issubset(entry.authority):
                        self._entries.move_to_end(task_id)
                        return entry
                    if entry.usages:
                        raise _error(PublicErrorCode.BUNDLE_BUSY, _BUSY, retryable=True)
                    entry.poisoned = True
                    self._entries.pop(task_id, None)
                    close_before_open = entry
                opening = self._opening.get(task_id)
                if opening is None:
                    if len(self._entries) + len(self._opening) >= (
                        self._policy.max_idle_tasks + self._policy.max_opening_tasks
                    ):
                        raise _error(PublicErrorCode.BUNDLE_BUSY, _BUSY, retryable=True)
                    opening = asyncio.create_task(
                        self._open_entry(inspection, access, provision_mode=provision_mode)
                    )
                    self._opening[task_id] = opening
            if close_before_open is not None:
                await self._close_entry(close_before_open)
            try:
                opened = await asyncio.shield(opening)
            except asyncio.CancelledError:
                # The opener continues so another follower can observe its durable outcome.
                raise
            except BaseException:
                async with self._lock:
                    if opening.done() and self._opening.get(task_id) is opening:
                        self._opening.pop(task_id, None)
                raise
            ready = True
            try:
                self._require_ready()
            except PublicOperationError:
                ready = False
            async with self._lock:
                if self._opening.get(task_id) is not opening:
                    continue
                self._opening.pop(task_id, None)
                if self._closed or not ready:
                    opened.poisoned = True
                else:
                    self._entries[task_id] = opened
                    self._entries.move_to_end(task_id)
                    return opened
            await self._close_entry(opened)
            raise _error(PublicErrorCode.SERVICE_UNAVAILABLE, _STALE, retryable=True)

    async def _open_entry(
        self,
        inspection: _RouteInspection,
        access: RouteAccess,
        *,
        provision_mode: BundleProvisionMode | None,
    ) -> _Entry:
        async with self._opening_limit:
            self._require_ready()
            keys: BundleKeys | None = None
            fence: OwnershipFence | None = None
            objects: ObjectStorePort | None = None
            ledger: LedgerPort | None = None
            importer: ImporterPort | None = None
            try:
                if access is not RouteAccess.STRUCTURAL_READ:
                    try:
                        keys = await self._vault.load_bundle_keys(inspection.route.task_id)
                    except KeyStoreError as exc:
                        if (
                            provision_mode is BundleProvisionMode.CREATED
                            and inspection.fresh_allocation
                            and exc.reason is KeyStoreReason.KEY_MISSING
                        ):
                            keys = await self._vault.create_bundle_keys(inspection.route.task_id)
                        else:
                            raise
                fence = await self._factories.acquire_fence(
                    inspection,
                    access
                    in {RouteAccess.WRITE, RouteAccess.IMPORT_REVIEW, RouteAccess.MAINTENANCE},
                )
                if (
                    type(fence) is not OwnershipFence
                    or fence.service_instance_id != self._context.service_instance_id
                    or fence.service_generation != self._context.service_generation
                ):
                    raise _error(
                        PublicErrorCode.STORAGE_UNSAFE,
                        "The task ownership fence is invalid.",
                        retryable=False,
                    )
                objects = await self._factories.open_objects(inspection, keys, fence, access)
                ledger = await self._factories.open_ledger(inspection, objects, fence, access)
                importer = await self._factories.open_importer(
                    inspection, objects, ledger, fence, access
                )
                self._require_ready()
                return _Entry(
                    inspection,
                    keys,
                    fence,
                    objects,
                    ledger,
                    importer,
                    self._authority_for(access),
                )
            except KeyStoreError as exc:
                if (
                    objects is not None
                    or ledger is not None
                    or importer is not None
                    or fence is not None
                ):
                    await self._factories.close_entry(inspection, objects, ledger, importer, fence)
                code = (
                    PublicErrorCode.VAULT_LOCKED
                    if exc.reason is KeyStoreReason.VAULT_LOCKED
                    else PublicErrorCode.STORAGE_CORRUPT
                )
                raise _error(
                    code,
                    "The bundle key is unavailable.",
                    retryable=code is PublicErrorCode.VAULT_LOCKED,
                ) from exc
            except BaseException:
                if (
                    objects is not None
                    or ledger is not None
                    or importer is not None
                    or fence is not None
                ):
                    await self._factories.close_entry(inspection, objects, ledger, importer, fence)
                raise

    @staticmethod
    def _same_route(left: TaskRoute, right: TaskRoute) -> bool:
        return (
            left.task_id == right.task_id
            and left.session_id == right.session_id
            and left.route_generation == right.route_generation
            and left.route_identity_digest == right.route_identity_digest
        )

    @staticmethod
    def _authority_for(access: RouteAccess) -> frozenset[RuntimeCapability]:
        if access is RouteAccess.STRUCTURAL_READ:
            return frozenset({RuntimeCapability.STRUCTURAL_READ})
        if access is RouteAccess.PAYLOAD_READ:
            return frozenset({RuntimeCapability.STRUCTURAL_READ, RuntimeCapability.PAYLOAD_READ})
        return frozenset(
            {
                RuntimeCapability.STRUCTURAL_READ,
                RuntimeCapability.PAYLOAD_READ,
                RuntimeCapability.WRITE,
            }
        )

    async def _lease(
        self,
        entry: _Entry,
        admitted: frozenset[RuntimeCapability],
        access: RouteAccess,
        writer_id: str | None,
    ) -> TaskRuntime:
        self._require_ready()
        await self._factories.validate_fence(entry.inspection, entry.fence)
        self._require_ready()
        route = entry.inspection.route
        ledger: LedgerPort
        objects: ObjectStorePort
        importer: ImporterPort
        observation: TaskObservationPort | None = None
        if access in {RouteAccess.WRITE, RouteAccess.IMPORT_REVIEW, RouteAccess.MAINTENANCE}:
            ledger = entry.ledger
            objects = entry.objects
            importer = entry.importer
            if RuntimeCapability.WRITE in admitted:
                observation = _observation_for(entry.ledger)
        else:
            ledger = cast(LedgerPort, _ReadLedger(entry.ledger))
            objects = cast(
                ObjectStorePort,
                _PayloadObjects(entry.objects)
                if access is RouteAccess.PAYLOAD_READ
                else _StructuralObjects(),
            )
            importer = cast(ImporterPort, _StatusImporter(entry.importer))
        versions = self._versions()
        runtime = TaskRuntime(
            task_id=route.task_id,
            session_id=route.session_id,
            writer_id=writer_id,
            capabilities=admitted,
            ledger=ledger,
            objects=objects,
            importer=importer,
            projection_version=versions["projection_version"],
            engine_version=versions["engine_version"],
            protocol_version=versions["protocol_version"],
            bundle_schema_version=versions["bundle_schema_version"],
            fence=entry.fence,
            observation=observation,
        )
        async with self._lock:
            if entry.poisoned or self._closed:
                raise _error(PublicErrorCode.SERVICE_UNAVAILABLE, _STALE, retryable=True)
            entry.usages += 1
            self._usages[id(runtime)] = entry
        return runtime

    def _versions(self) -> dict[str, str]:
        names = (
            "projection_version",
            "engine_version",
            "protocol_version",
            "bundle_schema_version",
        )
        result: dict[str, str] = {}
        for name in names:
            value = self._context.version_manifest.get(name)
            if type(value) is not str:
                raise _error(
                    PublicErrorCode.STORAGE_UNSAFE,
                    "The runtime version manifest is incomplete.",
                    retryable=False,
                )
            result[name] = value
        return result

    async def verify_start(
        self,
        runtime: TaskRuntime,
        expectation: StartMilestoneExpectation,
    ) -> StartCompletionEvidence:
        if type(runtime) is not TaskRuntime or type(expectation) is not StartMilestoneExpectation:
            raise _error(
                PublicErrorCode.INVALID_REQUEST, "The start proof is invalid.", retryable=False
            )
        self._require_ready()
        async with self._lock:
            entry = self._usages.get(id(runtime))
            if entry is None or entry.poisoned or runtime.fence != entry.fence:
                raise _error(PublicErrorCode.STORAGE_UNSAFE, _STALE, retryable=True)
        if (
            runtime.task_id != expectation.task_id
            or runtime.session_id != expectation.session_id
            or runtime.writer_id != expectation.writer_id
            or entry.inspection.route.route_generation != expectation.route_generation
            or entry.inspection.route.route_identity_digest != expectation.route_identity_digest
        ):
            raise _error(
                PublicErrorCode.SESSION_CONFLICT,
                "The start proof identity conflicts.",
                retryable=False,
            )
        evidence = await asyncio.shield(
            self._factories.verify_start(entry.inspection, runtime, expectation)
        )
        await self._factories.validate_fence(entry.inspection, entry.fence)
        self._require_ready()
        if evidence.owner_generation != entry.fence.owner_generation:
            raise _error(PublicErrorCode.STORAGE_UNSAFE, _STALE, retryable=True)
        return evidence

    async def release(self, runtime: TaskRuntime) -> None:
        if type(runtime) is not TaskRuntime:
            return
        evictions: list[_Entry] = []
        async with self._lock:
            entry = self._usages.pop(id(runtime), None)
            if entry is None:
                return
            entry.usages -= 1
            idle = [candidate for candidate in self._entries.values() if candidate.usages == 0]
            while len(idle) > self._policy.max_idle_tasks:
                candidate = idle.pop(0)
                candidate.poisoned = True
                self._entries.pop(candidate.inspection.route.task_id, None)
                evictions.append(candidate)
        for entry in evictions:
            await self._close_entry(entry)

    async def _close_entry(self, entry: _Entry) -> None:
        if entry.closed:
            return
        entry.closed = True
        try:
            await asyncio.shield(
                self._factories.close_entry(
                    entry.inspection,
                    entry.objects,
                    entry.ledger,
                    entry.importer,
                    entry.fence,
                )
            )
        except Exception:
            # Close is best-effort here; lifecycle decides whether inability to prove drain is fatal.
            return

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            entries = list(self._entries.values())
            openings = list(self._opening.values())
            self._entries.clear()
            self._usages.clear()
            for entry in entries:
                entry.poisoned = True
        if openings:
            results = await asyncio.gather(
                *(asyncio.shield(task) for task in openings), return_exceptions=True
            )
            entries.extend(result for result in results if type(result) is _Entry)
        for entry in entries:
            await self._close_entry(entry)


async def open_local_bundle_runtime(
    context: ServiceRuntimeContext,
    catalog: _GenerationCatalog,
    vault: _VaultService,
    factories: RuntimeAdapterFactories,
    diagnostics: DiagnosticsPort,
    versions: object,
    cache_policy: RuntimeCachePolicy = RuntimeCachePolicy(),
) -> LocalBundleRuntime:
    """Validate the ready composition without opening a task bundle."""

    del versions  # Context carries the immutable version manifest; no ambient lookup is allowed.
    if (
        type(context) is not ServiceRuntimeContext
        or type(factories) is not RuntimeAdapterFactories
        or type(cache_policy) is not RuntimeCachePolicy
        or not hasattr(catalog, "resolve_route")
        or not hasattr(catalog, "generation")
        or not hasattr(vault, "load_bundle_keys")
        or not hasattr(vault, "generation")
        or not hasattr(diagnostics, "record")
    ):
        raise TypeError("local_bundle_runtime_construction_invalid")
    return LocalBundleRuntime(context, catalog, vault, factories, diagnostics, cache_policy)
