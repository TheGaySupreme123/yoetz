"""Adversarial tests for the public attach capability compare-and-set."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from builders.ledger_adapters import FixedClock, FixedIds
from yoetz.application.lineage import (
    DelegationRequest,
    LineageConfig,
    LineageCoordinator,
    LineageProjectAdmission,
    MemoryLineageStore,
)
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError
from yoetz.protocol.ids import IdKind


def _digest(seed: str) -> str:
    return "sha256:" + seed * 64


def test_lineage_config_caps_fanout_at_child_manifest_capacity() -> None:
    assert LineageConfig(max_fanout=64).max_fanout == 64
    with pytest.raises(ValueError, match="invalid_lineage_value"):
        LineageConfig(max_fanout=65)


@pytest.mark.anyio
async def test_reused_handle_rejects_before_child_start_and_preserves_route() -> None:
    ids = FixedIds()
    store = MemoryLineageStore()
    coordinator = LineageCoordinator(
        store=store,
        clock=FixedClock(),
        ids=ids,
        handle_key=b"attach-test-key-which-is-long-enough",
    )
    parent_task_id = ids.new(IdKind.TASK)
    parent_session_id = ids.new(IdKind.SESSION)
    await coordinator.register_root(task_id=parent_task_id, session_id=parent_session_id)

    operation_id = ids.new(IdKind.REQUEST)
    reservation = await coordinator.reserve_delegation(
        DelegationRequest(
            operation_id=operation_id,
            request_digest=_digest("1"),
            parent_task_id=parent_task_id,
            parent_session_id=parent_session_id,
        )
    )
    handle = reservation.attach_handle
    child_session_id = ids.new(IdKind.SESSION)
    result = SimpleNamespace(task_id=reservation.task_id, session_id=child_session_id)

    async def start_child(_handle: object) -> object:
        raise AssertionError("a fresh request must not start an already attached child")

    attached, snapshot = await coordinator.attach_with_operation(
        handle_value=handle.value,
        request_id=ids.new(IdKind.REQUEST),
        replay_check=lambda _handle: _false(),
        operation=lambda _handle: _result(result),
    )
    assert attached is result
    assert snapshot.active_session_id == child_session_id

    with pytest.raises(PublicOperationError) as caught:
        await coordinator.attach_with_operation(
            handle_value=handle.value,
            request_id=ids.new(IdKind.REQUEST),
            replay_check=lambda _handle: _false(),
            operation=start_child,
        )
    assert caught.value.code is PublicErrorCode.SESSION_CONFLICT
    current = await store.get_task(reservation.task_id)
    assert current is not None and current.active_session_id == child_session_id


@pytest.mark.anyio
async def test_cross_repository_admission_authority_survives_replay_and_rejects_new_operation() -> (
    None
):
    ids = FixedIds()
    store = MemoryLineageStore()
    parent_task_id = ids.new(IdKind.TASK)
    parent_session_id = ids.new(IdKind.SESSION)
    parent_repository = "hmac-sha256:" + "a" * 64
    child_repository = "hmac-sha256:" + "b" * 64
    workspace = "hmac-sha256:" + "c" * 64
    external = "hmac-sha256:" + "d" * 64
    project_id = "prj_00000000-0000-4000-8000-000000000901"
    await LineageCoordinator(
        store=store,
        clock=FixedClock(),
        ids=ids,
        handle_key=b"cross-repository-admission-test-key",
    ).register_root(
        task_id=parent_task_id,
        session_id=parent_session_id,
        repository_commitment=parent_repository,
    )

    resolver_calls: list[tuple[str, str]] = []
    allow_new_operations = True

    async def resolve(parent: str, child: str) -> LineageProjectAdmission | None:
        resolver_calls.append((parent, child))
        if not allow_new_operations:
            return None
        return LineageProjectAdmission(project_id=project_id, membership_generation=4)

    coordinator = LineageCoordinator(
        store=store,
        clock=FixedClock(),
        ids=ids,
        handle_key=b"cross-repository-admission-test-key",
        project_admission_resolver=resolve,
    )
    operation_id = ids.new(IdKind.REQUEST)
    request = DelegationRequest(
        operation_id=operation_id,
        request_digest=_digest("2"),
        parent_task_id=parent_task_id,
        parent_session_id=parent_session_id,
        repository_commitment=child_repository,
        workspace_commitment=workspace,
        external_commitment=external,
    )
    reservation = await coordinator.reserve_delegation(request)
    assert reservation.operation.project_id == project_id
    assert reservation.operation.membership_generation == 4
    assert resolver_calls == [(parent_task_id, child_repository)]

    # A restarted coordinator returns the durable operation and handle without re-checking a
    # grant that may have been revoked since the original admission.
    allow_new_operations = False
    restarted = LineageCoordinator(
        store=store,
        clock=FixedClock(),
        ids=FixedIds(),
        handle_key=b"cross-repository-admission-test-key",
        owner_generation=2,
        project_admission_resolver=resolve,
    )
    replay = await restarted.reserve_delegation(request)
    assert replay.task_id == reservation.task_id
    assert replay.operation.project_id == project_id
    assert replay.operation.membership_generation == 4
    assert resolver_calls == [(parent_task_id, child_repository)]

    with pytest.raises(PublicOperationError) as caught:
        await restarted.reserve_delegation(
            DelegationRequest(
                operation_id=ids.new(IdKind.REQUEST),
                request_digest=_digest("3"),
                parent_task_id=parent_task_id,
                parent_session_id=parent_session_id,
                repository_commitment=child_repository,
                workspace_commitment=workspace,
                external_commitment=external,
            )
        )
    assert caught.value.code is PublicErrorCode.SESSION_CONFLICT
    assert caught.value.safe_details["reason_code"] == "cross_repository_lineage_requires_grant"
    assert resolver_calls == [
        (parent_task_id, child_repository),
        (parent_task_id, child_repository),
    ]


@pytest.mark.anyio
async def test_self_registration_records_the_same_admission_authority_for_replay() -> None:
    ids = FixedIds()
    store = MemoryLineageStore()
    parent_task_id = ids.new(IdKind.TASK)
    parent_session_id = ids.new(IdKind.SESSION)
    parent_repository = "hmac-sha256:" + "e" * 64
    child_repository = "hmac-sha256:" + "f" * 64
    workspace = "hmac-sha256:" + "1" * 64
    external = "hmac-sha256:" + "2" * 64
    await LineageCoordinator(
        store=store,
        clock=FixedClock(),
        ids=ids,
        handle_key=b"self-registration-admission-test-key",
    ).register_root(
        task_id=parent_task_id,
        session_id=parent_session_id,
        repository_commitment=parent_repository,
    )

    calls = 0
    allow = True

    async def resolve(_parent: str, _child: str) -> LineageProjectAdmission | None:
        nonlocal calls
        calls += 1
        if not allow:
            return None
        return LineageProjectAdmission(
            project_id="prj_00000000-0000-4000-8000-000000000902",
            membership_generation=9,
        )

    coordinator = LineageCoordinator(
        store=store,
        clock=FixedClock(),
        ids=ids,
        handle_key=b"self-registration-admission-test-key",
        project_admission_resolver=resolve,
    )
    operation_id = ids.new(IdKind.REQUEST)
    request_digest = _digest("5")
    child = await coordinator.self_register(
        operation_id=operation_id,
        request_digest=request_digest,
        parent_session_id=parent_session_id,
        repository_commitment=child_repository,
        workspace_commitment=workspace,
        external_commitment=external,
    )
    operation = await store.get_operation(operation_id)
    assert operation is not None
    assert operation.child_task_id == child.task_id
    assert operation.project_id == "prj_00000000-0000-4000-8000-000000000902"
    assert operation.membership_generation == 9
    assert calls == 1

    allow = False
    replay = await coordinator.self_register(
        operation_id=operation_id,
        request_digest=request_digest,
        parent_session_id=parent_session_id,
        repository_commitment=child_repository,
        workspace_commitment=workspace,
        external_commitment=external,
    )
    assert replay == child
    assert calls == 1


async def _false() -> bool:
    return False


async def _result(value: object) -> object:
    return value
