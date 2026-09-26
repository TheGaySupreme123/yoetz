"""Coordinator-side guards for frozen manifest capture."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import cast

import pytest

from yoetz.application.lineage_coordinator import LineageManifestCoordinator
from yoetz.domain.events import LedgerRecord
from yoetz.domain.values import task_id
from yoetz.ports.clock import ClockPort
from yoetz.ports.diagnostics import RuntimeCapability
from yoetz.ports.ids import IdPort
from yoetz.ports.importer import ImporterPort
from yoetz.ports.ledger import LedgerPort
from yoetz.ports.objects import ObjectStorePort
from yoetz.ports.runtime import (
    BundleRuntimePort,
    OwnershipFence,
    TaskRuntime,
)
from yoetz.ports.start_catalog import StartCatalogPort
from yoetz.protocol.ids import IdKind

pytestmark = pytest.mark.anyio


class _Clock:
    def now_utc(self):
        from datetime import UTC, datetime

        return datetime(2026, 9, 5, 12, tzinfo=UTC)

    def monotonic_seconds(self) -> float:
        return 1.0


class _Ids:
    def new(self, kind: IdKind) -> str:
        del kind
        return "evt_00000000-0000-4000-8000-000000000999"


class _Ledger:
    async def load_events(
        self, _session_id: str, *, through: int | None = None
    ) -> AsyncIterator[LedgerRecord]:
        del through
        if False:
            yield cast(LedgerRecord, None)


class _Catalog:
    async def list_child_task_ids(self, _parent_task_id: str) -> tuple[str, ...]:
        raise RuntimeError("inventory_unavailable")


def _runtime() -> TaskRuntime:
    return TaskRuntime(
        task_id=task_id("tsk_00000000-0000-4000-8000-000000000901"),
        session_id="ses_00000000-0000-4000-8000-000000000901",
        writer_id="wri_00000000-0000-4000-8000-000000000901",
        capabilities=frozenset({RuntimeCapability.STRUCTURAL_READ}),
        ledger=cast(LedgerPort, _Ledger()),
        objects=cast(ObjectStorePort, object()),
        importer=cast(ImporterPort, object()),
        projection_version="1.0.0",
        engine_version="1.0.0",
        protocol_version="0.1",
        bundle_schema_version="1.0.0",
        fence=OwnershipFence(
            "svc_00000000-0000-4000-8000-000000000901",
            1,
            1,
            "0123456789abcdef",
        ),
    )


async def test_inventory_failure_does_not_replace_a_recorded_manifest_with_empty_state() -> None:
    coordinator = LineageManifestCoordinator(
        runtime=cast(BundleRuntimePort, object()),
        catalog=cast(StartCatalogPort, _Catalog()),
        clock=cast(ClockPort, _Clock()),
        ids=cast(IdPort, _Ids()),
    )

    result = await coordinator.sweep(_runtime())

    assert result.inventory_read_gap is True
    assert result.changed is False
    assert result.append_result is None
    assert result.manifest.source_event_id is None
    assert result.manifest.children == ()
