"""Ready-service runtime generation, capability, and facade tests."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast

import pytest

from yoetz.adapters.runtime import (
    RuntimeAdapterFactories,
    RuntimeCachePolicy,
    open_local_bundle_runtime,
)
from yoetz.adapters.session_events import (
    LinuxLogin1Backend,
    SessionEventMonitor,
)
from yoetz.ports.diagnostics import RuntimeCapability, StartupCheckResult
from yoetz.ports.importer import ImporterPort
from yoetz.ports.keys import BundleKeys
from yoetz.ports.ledger import LedgerPort
from yoetz.ports.objects import ObjectStorePort
from yoetz.ports.runtime import (
    OwnershipFence,
    RouteAccess,
    RouteCommand,
    ServiceRuntimeContext,
    StartCompletionEvidence,
    StartMilestoneExpectation,
    TaskRuntime,
)
from yoetz.ports.start_catalog import TaskRoute, TaskRouteState
from yoetz.protocol.canonical import canonical_digest
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError
from yoetz.protocol.ids import PREFIX_BY_KIND, IdKind
from yoetz.service.lifecycle import SessionSecurityEvent


def _id(kind: IdKind, value: int) -> str:
    raw = bytearray(value.to_bytes(16, "big"))
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    return PREFIX_BY_KIND[kind] + str(uuid.UUID(bytes=bytes(raw)))


@dataclass(frozen=True, slots=True)
class _Inspection:
    route: TaskRoute
    admitted_writer_ids: frozenset[str]
    fresh_allocation: bool = False


class _Catalog:
    def __init__(self, route: TaskRoute) -> None:
        self.generation = 3
        self.route_value = route
        self.lookups = 0

    async def resolve_route(self, session_id: str) -> TaskRoute | None:
        self.lookups += 1
        return self.route_value if session_id == self.route_value.session_id else None


class _Vault:
    def __init__(self) -> None:
        self.generation = 2
        self.ready = True
        self.loads = 0

    async def load_bundle_keys(self, bundle_id: str) -> BundleKeys:
        del bundle_id
        self.loads += 1
        return cast(BundleKeys, object())

    async def create_bundle_keys(self, bundle_id: str) -> BundleKeys:
        del bundle_id
        raise AssertionError("route_must_not_create_keys")


class _Diagnostics:
    def record(self, result: StartupCheckResult) -> None:
        del result


class _Port:
    pass


@dataclass(slots=True)
class _Harness:
    service_generation: int
    route: TaskRoute
    writer_id: str
    opens: int = 0
    closes: int = 0
    inspections: int = 0

    def factories(self) -> RuntimeAdapterFactories:
        async def inspect(route: TaskRoute, access: RouteAccess) -> _Inspection:
            del access
            self.inspections += 1
            return _Inspection(route, frozenset({self.writer_id}))

        async def inspect_provision(command: object) -> _Inspection:
            del command
            return _Inspection(self.route, frozenset({self.writer_id}), True)

        async def acquire(value: object, writer: bool) -> OwnershipFence:
            del value, writer
            self.opens += 1
            return OwnershipFence(
                _id(IdKind.SERVICE_INSTANCE, 11), self.service_generation, 4, "nonce_value_123456"
            )

        async def validate(value: object, fence: OwnershipFence) -> None:
            del value
            assert fence.owner_generation == 4

        async def objects(
            value: object,
            keys: BundleKeys | None,
            fence: OwnershipFence,
            access: RouteAccess,
        ) -> ObjectStorePort:
            del value, keys, fence, access
            return cast(ObjectStorePort, _Port())

        async def ledger(
            value: object,
            objects: ObjectStorePort,
            fence: OwnershipFence,
            access: RouteAccess,
        ) -> LedgerPort:
            del value, objects, fence, access
            return cast(LedgerPort, _Port())

        async def importer(
            value: object,
            objects: ObjectStorePort,
            ledger: LedgerPort,
            fence: OwnershipFence,
            access: RouteAccess,
        ) -> ImporterPort:
            del value, objects, ledger, fence, access
            return cast(ImporterPort, _Port())

        async def verify(
            value: object,
            runtime: TaskRuntime,
            expectation: StartMilestoneExpectation,
        ) -> StartCompletionEvidence:
            del value, runtime, expectation
            raise AssertionError("not_used")

        async def close(*args: object) -> None:
            del args
            self.closes += 1

        return RuntimeAdapterFactories(
            current_service_generation=lambda: self.service_generation,
            inspect_route=inspect,
            inspect_provision=inspect_provision,
            acquire_fence=acquire,
            validate_fence=validate,
            open_objects=objects,
            open_ledger=ledger,
            open_importer=importer,
            verify_start=verify,
            close_entry=close,
        )


def _route() -> tuple[TaskRoute, str]:
    task = _id(IdKind.TASK, 1)
    session = _id(IdKind.SESSION, 2)
    writer = _id(IdKind.WRITER, 3)
    generation = 1
    return (
        TaskRoute(
            task,
            session,
            f"tasks/{task}",
            generation,
            TaskRouteState.ACTIVE,
            canonical_digest(
                {
                    "task_id": task,
                    "bundle_relpath": f"tasks/{task}",
                    "route_generation": generation,
                }
            ),
        ),
        writer,
    )


def _context(capabilities: frozenset[RuntimeCapability]) -> ServiceRuntimeContext:
    return ServiceRuntimeContext(
        service_instance_id=_id(IdKind.SERVICE_INSTANCE, 11),
        service_generation=1,
        vault_generation=2,
        catalog_generation=3,
        capabilities=capabilities,
        version_manifest={
            "bundle_schema_version": "1",
            "engine_version": "0.1.0",
            "projection_version": "1",
            "protocol_version": "0.1",
        },
        shutdown_token=object(),
    )


@pytest.mark.anyio
async def test_stale_service_rejects_before_catalog_route_io() -> None:
    route, writer = _route()
    catalog = _Catalog(route)
    vault = _Vault()
    harness = _Harness(1, route, writer)
    runtime = await open_local_bundle_runtime(
        _context(frozenset(RuntimeCapability)),
        catalog,
        vault,
        harness.factories(),
        _Diagnostics(),
        object(),
    )
    harness.service_generation = 2
    with pytest.raises(PublicOperationError) as caught:
        await runtime.route(
            RouteCommand(
                route.session_id,
                None,
                RouteAccess.STRUCTURAL_READ,
                frozenset({RuntimeCapability.STRUCTURAL_READ}),
            )
        )
    assert caught.value.code is PublicErrorCode.SERVICE_UNAVAILABLE
    assert catalog.lookups == 0


@pytest.mark.anyio
async def test_capability_ceiling_and_writer_membership_fail_closed() -> None:
    route, writer = _route()
    catalog = _Catalog(route)
    vault = _Vault()
    harness = _Harness(1, route, writer)
    runtime = await open_local_bundle_runtime(
        _context(frozenset({RuntimeCapability.STRUCTURAL_READ, RuntimeCapability.WRITE})),
        catalog,
        vault,
        harness.factories(),
        _Diagnostics(),
        object(),
    )
    with pytest.raises(PublicOperationError) as unavailable:
        await runtime.route(
            RouteCommand(
                route.session_id,
                None,
                RouteAccess.PAYLOAD_READ,
                frozenset({RuntimeCapability.PAYLOAD_READ}),
            )
        )
    assert unavailable.value.code is PublicErrorCode.STORAGE_UNSAFE
    assert catalog.lookups == 0
    await runtime.close()

    foreign_writer = _id(IdKind.WRITER, 99)
    runtime = await open_local_bundle_runtime(
        _context(
            frozenset(
                {
                    RuntimeCapability.STRUCTURAL_READ,
                    RuntimeCapability.PAYLOAD_READ,
                    RuntimeCapability.WRITE,
                }
            )
        ),
        catalog,
        vault,
        harness.factories(),
        _Diagnostics(),
        object(),
    )
    with pytest.raises(PublicOperationError) as conflict:
        await runtime.route(
            RouteCommand(
                route.session_id,
                foreign_writer,
                RouteAccess.WRITE,
                frozenset({RuntimeCapability.WRITE}),
            )
        )
    assert conflict.value.code is PublicErrorCode.SESSION_CONFLICT
    assert harness.opens == 0


@pytest.mark.anyio
async def test_read_facade_has_no_mutators_and_close_poisons_handle() -> None:
    route, writer = _route()
    catalog = _Catalog(route)
    vault = _Vault()
    harness = _Harness(1, route, writer)
    runtime = await open_local_bundle_runtime(
        _context(frozenset({RuntimeCapability.STRUCTURAL_READ})),
        catalog,
        vault,
        harness.factories(),
        _Diagnostics(),
        object(),
        RuntimeCachePolicy(max_idle_tasks=1, max_opening_tasks=1),
    )
    task_runtime = await runtime.route(
        RouteCommand(
            route.session_id,
            None,
            RouteAccess.STRUCTURAL_READ,
            frozenset({RuntimeCapability.STRUCTURAL_READ}),
        )
    )
    assert not hasattr(task_runtime.ledger, "append_batch")
    assert not hasattr(task_runtime.objects, "stage")
    assert not hasattr(task_runtime.importer, "capture")
    assert vault.loads == 0
    await runtime.close()
    assert harness.closes == 1
    with pytest.raises(PublicOperationError) as closed:
        await runtime.route(
            RouteCommand(
                route.session_id,
                None,
                RouteAccess.STRUCTURAL_READ,
                frozenset({RuntimeCapability.STRUCTURAL_READ}),
            )
        )
    assert closed.value.code is PublicErrorCode.SERVICE_UNAVAILABLE


def test_context_is_constant_redacted_and_not_serializable() -> None:
    context = _context(frozenset({RuntimeCapability.STRUCTURAL_READ}))
    assert repr(context) == "ServiceRuntimeContext(<redacted>)"
    with pytest.raises(TypeError, match="not_serializable"):
        context.__reduce__()
    assert "path" not in context.version_manifest
    assert hashlib.sha256(repr(context).encode()).digest()


@pytest.mark.anyio
async def test_session_monitor_normalizes_duplicates_and_never_unlocks_itself() -> None:
    sink: Callable[[SessionSecurityEvent], Awaitable[None]] | None = None
    unsubscribed = 0

    async def subscribe(
        value: Callable[[SessionSecurityEvent], Awaitable[None]],
    ) -> Callable[[], Awaitable[None]]:
        nonlocal sink
        sink = value
        await value(SessionSecurityEvent.USER_SESSION_LOCKED)

        async def unsubscribe() -> None:
            nonlocal unsubscribed
            unsubscribed += 1

        return unsubscribe

    events: list[SessionSecurityEvent] = []

    async def lifecycle(event: SessionSecurityEvent) -> None:
        events.append(event)

    monitor = SessionEventMonitor(LinuxLogin1Backend(subscribe))
    await monitor.start(lifecycle)
    assert monitor.capability.active
    assert callable(sink)
    emit = sink
    await emit(SessionSecurityEvent.USER_SESSION_LOCKED)
    await emit(SessionSecurityEvent.USER_SESSION_UNLOCKED)
    await emit(SessionSecurityEvent.SYSTEM_SUSPEND)
    await emit(SessionSecurityEvent.SYSTEM_RESUME)
    assert events == [
        SessionSecurityEvent.USER_SESSION_LOCKED,
        SessionSecurityEvent.USER_SESSION_UNLOCKED,
        SessionSecurityEvent.SYSTEM_SUSPEND,
        SessionSecurityEvent.SYSTEM_RESUME,
    ]
    await emit(SessionSecurityEvent.MONITOR_LOST)
    assert events[-1] is SessionSecurityEvent.MONITOR_LOST
    assert not monitor.capability.active
    await monitor.close()
    assert unsubscribed == 1
