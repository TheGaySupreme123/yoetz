"""Recovery ordering and authenticated native activity policy."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from builders.ledger_adapters import FixedClock, FixedIds
from yoetz.application.lineage import LineageCoordinator, LineageSnapshot, MemoryLineageStore
from yoetz.domain.coordination import SessionHealth, WorkState
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError
from yoetz.protocol.ids import IdKind

pytestmark = pytest.mark.anyio


async def test_retryable_abandonment_failure_does_not_starve_other_tasks() -> None:
    store = MemoryLineageStore()
    clock = FixedClock()
    ids = FixedIds()
    coordinator = LineageCoordinator(store=store, clock=clock, ids=ids, handle_key=b"k" * 32)
    tasks: list[str] = []
    for _ in range(2):
        task = ids.new(IdKind.TASK)
        tasks.append(task)
        snapshot = await coordinator.register_root(task_id=task, session_id=ids.new(IdKind.SESSION))
        await store.save_task(
            replace(
                snapshot,
                session_health=SessionHealth.CONTACT_LOST,
                contact_lost_at=clock.now_utc() - timedelta(seconds=301),
                abandonment_deadline=clock.now_utc() - timedelta(seconds=1),
            )
        )
    committed: list[str] = []
    blocked_task = min(tasks)

    async def append(snapshot: LineageSnapshot, _reason: str, _deadline: datetime) -> None:
        if snapshot.task_id == blocked_task:
            raise PublicOperationError(PublicErrorCode.BUNDLE_BUSY, "Retry later.", True)
        committed.append(snapshot.task_id)

    with pytest.raises(PublicOperationError) as failure:
        await coordinator.recover_abandoned(append)
    assert failure.value.retryable is True
    assert len(committed) == 1
    blocked = await store.get_task(blocked_task)
    completed = await store.get_task(committed[0])
    assert blocked is not None and blocked.work_state is WorkState.OPEN
    assert completed is not None and completed.work_state is WorkState.ABANDONED


async def test_native_activity_renews_contact_but_never_revives_ended_session_or_work() -> None:
    store = MemoryLineageStore()
    clock = FixedClock()
    ids = FixedIds()
    coordinator = LineageCoordinator(store=store, clock=clock, ids=ids, handle_key=b"k" * 32)
    task = ids.new(IdKind.TASK)
    session = ids.new(IdKind.SESSION)
    snapshot = await coordinator.register_root(task_id=task, session_id=session)
    await store.save_task(
        replace(
            snapshot,
            work_state=WorkState.ABANDONED,
            session_health=SessionHealth.CONTACT_LOST,
            contact_lost_at=clock.now_utc() - timedelta(seconds=301),
            abandonment_deadline=clock.now_utc() - timedelta(seconds=1),
        )
    )
    renewals: list[bool] = []

    async def renew() -> None:
        renewals.append(True)

    await coordinator.renew_observed_activity(task_id=task, session_id=session, renew_lease=renew)
    renewed = await store.get_task(task)
    assert renewed is not None
    assert renewed.work_state is WorkState.ABANDONED
    assert renewed.session_health is SessionHealth.ACTIVE
    assert renewed.contact_lost_at is None and renewed.abandonment_deadline is None
    await coordinator.end_session(session_id=session)
    await coordinator.renew_observed_activity(task_id=task, session_id=session, renew_lease=renew)
    ended = await store.get_task(task)
    assert ended is not None and ended.session_health is SessionHealth.ENDED
    assert renewals == [True]
