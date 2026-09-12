"""Adversarial public recovery checks for the durable delegation phase machine.

Each row injects a failure at one external delegation boundary, then retries the exact public
request.  The assertions inspect the durable operation, route, handle, and parent event so a
successful retry cannot hide a duplicate child or a second parent declaration.
"""

from __future__ import annotations

import subprocess
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import cast

import pytest

import yoetz.application.start as start_module
from builders.multi_agent import MultiAgentService, multi_agent_service
from yoetz.adapters.sqlite.lineage_catalog import SqliteLineageStore
from yoetz.adapters.sqlite.start_catalog import SqliteStartCatalog
from yoetz.application.lineage import (
    DelegationOperation,
    DelegationOperationState,
    DelegationPhase,
    LineageCoordinator,
)
from yoetz.application.service import Application
from yoetz.application.start import StartInternalResult
from yoetz.config.models import LineageSettings, YoetzConfig
from yoetz.domain.coordination import LineageAcceptance
from yoetz.domain.events import AcceptedEvent, DelegationDeclaredPayload
from yoetz.ports.control import RepositoryPrivacyContext
from yoetz.ports.diagnostics import RuntimeCapability
from yoetz.ports.runtime import RouteAccess, RouteCommand
from yoetz.ports.start_catalog import SafeReason, StartPhase
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError
from yoetz.protocol.ids import IdKind, new_id
from yoetz.protocol.models import StartRequest

pytestmark = pytest.mark.anyio

_REPOSITORY = RepositoryPrivacyContext("hmac-sha256:" + "d" * 64, "git_common_root")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _identity() -> dict[str, object]:
    return {
        "protocol_version": "0.1",
        "schema_version": "1.0.0",
        "request_id": new_id(IdKind.REQUEST),
        "actor": {"actor_id": "harness:delegation-phase-matrix", "actor_type": "harness"},
        "client": {
            "kind": "cooperative_agent",
            "version": "0.3.0",
            "integration": "cooperative_mcp",
        },
    }


def _workspace(root: Path) -> Path:
    root.mkdir()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True, capture_output=True)
    return root.resolve()


def _delegate_request(parent_session_id: str, *, title: str = "phase child") -> StartRequest:
    return StartRequest.model_validate(
        {
            **_identity(),
            "mode": "delegate",
            "task_title": title,
            "session_id": parent_session_id,
            "requested_view": "compact",
        }
    )


async def _delegation_events(
    app: Application, session_id: str, writer_id: str
) -> tuple[AcceptedEvent, ...]:
    runtime = await app.runtime.route(
        RouteCommand(
            session_id,
            writer_id,
            RouteAccess.PAYLOAD_READ,
            frozenset({RuntimeCapability.STRUCTURAL_READ, RuntimeCapability.PAYLOAD_READ}),
        )
    )
    try:
        events: list[AcceptedEvent] = []
        records = cast(AsyncIterator[object], runtime.ledger.load_events(session_id))
        async for record in records:
            if isinstance(record, AcceptedEvent) and isinstance(
                record.payload, DelegationDeclaredPayload
            ):
                events.append(record)
        return tuple(events)
    finally:
        await app.runtime.release(runtime)


async def _assert_single_delegation_state(
    service: MultiAgentService,
    parent: StartInternalResult,
    request: StartRequest,
    result: StartInternalResult,
) -> None:
    app = service.app
    lineage = app.lineage
    assert lineage is not None
    store = cast(SqliteLineageStore, lineage.store)
    operations = await store.list_operations()
    assert len(operations) == 1
    operation = operations[0]
    assert operation.operation_id == request.request_id
    assert operation.state is DelegationOperationState.COMPLETE
    assert operation.phase is DelegationPhase.TERMINAL
    assert operation.child_task_id == result.task_id

    handle = await store.get_handle(operation.handle_digest)
    assert handle is not None
    assert handle.task_id == operation.child_task_id
    assert result.attach_handle is not None
    assert handle.value == result.attach_handle.value

    catalog = cast(SqliteStartCatalog, app.start_catalog)
    routes = await catalog.recovery_routes()
    assert {route.task_id for route in routes} == {
        parent.task_id,
        operation.child_task_id,
    }
    children = await store.list_children(parent.task_id)
    assert tuple(child.task_id for child in children) == (operation.child_task_id,)
    child = await store.get_task(operation.child_task_id)
    assert child is not None
    assert child.acceptance is LineageAcceptance.ACCEPTED

    events = await _delegation_events(app, parent.session_id, parent.writer_id)
    assert len(events) == 1
    payload = events[0].payload
    assert isinstance(payload, DelegationDeclaredPayload)
    assert str(payload.child_task_id) == operation.child_task_id
    assert payload.handle_digest == operation.handle_digest


@pytest.mark.parametrize(
    "boundary",
    (
        "lineage_reserved",
        "child_bundle_ready",
        "parent_event_committed",
        "handle_published",
    ),
)
async def test_public_delegation_retries_every_phase_boundary_without_duplicates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    """A crash after each boundary is recovered by the same request identity."""

    workspace = _workspace(tmp_path / "workspace")
    async with multi_agent_service(tmp_path / "state") as service:
        app = service.app
        parent = await app.start(
            StartRequest.model_validate(
                {
                    **_identity(),
                    "mode": "create",
                    "task_title": "phase parent",
                    "workspace_ref": str(workspace),
                    "external_ref": "phase-parent",
                    "requested_view": "compact",
                }
            ),
            repository_privacy_context=_REPOSITORY,
        )
        request = _delegate_request(parent.session_id)

        if boundary in {"lineage_reserved", "child_bundle_ready"}:
            original = cast(
                Callable[[Application, DelegationOperation], Awaitable[None]],
                start_module._provision_delegated_child,  # pyright: ignore[reportPrivateUsage]
            )

            async def fail_at_bundle(app_arg: Application, operation: DelegationOperation) -> None:
                if boundary == "child_bundle_ready":
                    await original(app_arg, operation)
                raise RuntimeError(f"simulated_{boundary}")

            monkeypatch.setattr(start_module, "_provision_delegated_child", fail_at_bundle)
        elif boundary == "parent_event_committed":
            original = cast(
                Callable[[Application, DelegationOperation], Awaitable[None]],
                start_module._append_delegation_event,  # pyright: ignore[reportPrivateUsage]
            )

            async def fail_at_event(app_arg: Application, operation: DelegationOperation) -> None:
                await original(app_arg, operation)
                raise RuntimeError(f"simulated_{boundary}")

            monkeypatch.setattr(start_module, "_append_delegation_event", fail_at_event)
        else:
            lineage_type = LineageCoordinator
            original = lineage_type.publish_attach_handle

            async def fail_at_handle(
                coordinator: LineageCoordinator, operation_id: str, request_digest: str
            ) -> object:
                await original(coordinator, operation_id, request_digest)
                raise RuntimeError(f"simulated_{boundary}")

            monkeypatch.setattr(lineage_type, "publish_attach_handle", fail_at_handle)

        with pytest.raises(RuntimeError, match=f"simulated_{boundary}"):
            await app.start(request, repository_privacy_context=_REPOSITORY)

        lineage = app.lineage
        assert lineage is not None
        pending = await lineage.store.get_operation(request.request_id)
        assert pending is not None
        expected_phase = {
            "lineage_reserved": DelegationPhase.LINEAGE_RESERVED,
            "child_bundle_ready": DelegationPhase.LINEAGE_RESERVED,
            "parent_event_committed": DelegationPhase.CHILD_BUNDLE_READY,
            "handle_published": DelegationPhase.HANDLE_PUBLISHED,
        }[boundary]
        assert pending.phase is expected_phase
        assert pending.state is DelegationOperationState.PENDING

        # Restore the injected failure before retrying the exact public request.  The original
        # request id and body must reclaim the durable operation rather than mint a sibling.
        monkeypatch.undo()
        retried = await app.start(request, repository_privacy_context=_REPOSITORY)
        assert retried.task_id == pending.child_task_id
        assert retried.attach_handle is not None
        await _assert_single_delegation_state(service, parent, request, retried)

        # A following recovery sweep must be a no-op for the completed operation and preserve the
        # same child and parent event.  This also exercises the public recovery entry point after
        # the retry path has closed the operation.
        await app.recover_lineage()
        replayed = await app.start(request, repository_privacy_context=_REPOSITORY)
        assert replayed.task_id == retried.task_id
        assert replayed.attach_handle == retried.attach_handle
        await _assert_single_delegation_state(service, parent, request, replayed)


async def test_public_attach_handle_expiry_is_typed_and_does_not_rotate_child(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "workspace")
    config = YoetzConfig(lineage=LineageSettings(attach_handle_ttl_seconds=1))
    async with multi_agent_service(tmp_path / "state", config=config) as service:
        parent = await service.app.start(
            StartRequest.model_validate(
                {
                    **_identity(),
                    "mode": "create",
                    "task_title": "expiry parent",
                    "workspace_ref": str(workspace),
                    "external_ref": "expiry-parent",
                    "requested_view": "compact",
                }
            ),
            repository_privacy_context=_REPOSITORY,
        )
        delegated = await service.app.start(
            _delegate_request(parent.session_id, title="expiry child"),
            repository_privacy_context=_REPOSITORY,
        )
        assert delegated.attach_handle is not None
        catalog = cast(SqliteStartCatalog, service.app.start_catalog)
        before_routes = await catalog.recovery_routes()
        service.clock.advance(seconds=2)
        with pytest.raises(PublicOperationError) as expired:
            await service.app.start(
                StartRequest.model_validate(
                    {
                        **_identity(),
                        "mode": "attach",
                        "task_title": "expiry child",
                        "attach_handle": delegated.as_wire()["attach_handle"],
                        "requested_view": "compact",
                    }
                ),
                repository_privacy_context=_REPOSITORY,
            )
        assert expired.value.code is PublicErrorCode.SESSION_CONFLICT
        assert expired.value.safe_details["reason_code"] == "attach_handle_expired"
        after_routes = await catalog.recovery_routes()
        assert tuple(route.task_id for route in after_routes) == tuple(
            route.task_id for route in before_routes
        )


async def test_same_session_attach_retry_is_idempotent_after_handle_consumption(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "workspace")
    async with multi_agent_service(tmp_path / "state") as service:
        parent = await service.app.start(
            StartRequest.model_validate(
                {
                    **_identity(),
                    "mode": "create",
                    "task_title": "attach retry parent",
                    "workspace_ref": str(workspace),
                    "external_ref": "attach-retry-parent",
                    "requested_view": "compact",
                }
            ),
            repository_privacy_context=_REPOSITORY,
        )
        delegated = await service.app.start(
            _delegate_request(parent.session_id, title="attach retry child"),
            repository_privacy_context=_REPOSITORY,
        )
        assert delegated.attach_handle is not None
        attach = StartRequest.model_validate(
            {
                **_identity(),
                "mode": "attach",
                "task_title": "attach retry child",
                "attach_handle": delegated.as_wire()["attach_handle"],
                "requested_view": "compact",
            }
        )
        first = await service.app.start(attach, repository_privacy_context=_REPOSITORY)
        second = await service.app.start(attach, repository_privacy_context=_REPOSITORY)
        assert (second.task_id, second.session_id, second.writer_id) == (
            first.task_id,
            first.session_id,
            first.writer_id,
        )
        lineage = service.app.lineage
        assert lineage is not None
        store = cast(SqliteLineageStore, lineage.store)
        operation = (await store.list_operations())[0]
        handle = await store.get_handle(operation.handle_digest)
        assert handle is not None and handle.consumed_session_id == first.session_id
        routes = await cast(SqliteStartCatalog, service.app.start_catalog).recovery_routes()
        assert len(routes) == 2


@pytest.mark.parametrize("ref_kind", ("forged", "dangling", "quarantined"))
async def test_parent_reference_failures_are_typed_and_do_not_mint_children(
    tmp_path: Path,
    ref_kind: str,
) -> None:
    workspace = _workspace(tmp_path / "workspace")
    async with multi_agent_service(tmp_path / "state") as service:
        app = service.app
        if ref_kind == "forged":
            parent_session_id = new_id(IdKind.SESSION)
        elif ref_kind == "dangling":
            parent = await app.start(
                StartRequest.model_validate(
                    {
                        **_identity(),
                        "mode": "create",
                        "task_title": "dangling parent",
                        "workspace_ref": str(workspace),
                        "external_ref": "dangling-parent",
                        "requested_view": "compact",
                    }
                ),
                repository_privacy_context=_REPOSITORY,
            )
            rotated = await app.start(
                StartRequest.model_validate(
                    {
                        **_identity(),
                        "mode": "create_or_attach",
                        "task_title": "dangling parent",
                        "workspace_ref": str(workspace),
                        "external_ref": "dangling-parent",
                        "requested_view": "compact",
                    }
                ),
                repository_privacy_context=_REPOSITORY,
            )
            assert rotated.task_id == parent.task_id
            assert rotated.session_id != parent.session_id
            parent_session_id = parent.session_id
        else:
            command_request = StartRequest.model_validate(
                {
                    **_identity(),
                    "mode": "create",
                    "task_title": "quarantined parent",
                    "workspace_ref": str(workspace),
                    "external_ref": "quarantined-parent",
                    "requested_view": "compact",
                }
            )
            command = await start_module._command(  # pyright: ignore[reportPrivateUsage]
                app, command_request, _REPOSITORY.commitment
            )
            allocation = await app.start_catalog.reserve_or_resume(command)
            assert allocation.phase is StartPhase.ROUTE_RESERVED
            await app.start_catalog.quarantine(allocation, SafeReason("start_route_contradiction"))
            parent_session_id = allocation.session_id

        with pytest.raises(PublicOperationError) as refused:
            await app.start(
                _delegate_request(parent_session_id, title=f"{ref_kind} child"),
                repository_privacy_context=_REPOSITORY,
            )
        assert refused.value.code is PublicErrorCode.SESSION_NOT_FOUND
        assert refused.value.safe_details["reason_code"] == "lineage_parent_not_found"
        lineage = app.lineage
        assert lineage is not None
        assert await cast(SqliteLineageStore, lineage.store).list_operations() == ()
