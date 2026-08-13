"""Shared observation-outbox routing and in-process service sweeps."""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass
from enum import Enum
from typing import Final, Protocol

from yoetz.adapters.integrations.observation_local import (
    LocalObservationStore,
    ObservationOutboxRow,
)
from yoetz.domain.observation import (
    ObservationGapCode,
    ObservationIngestDisposition,
    ObservationIngestRequest,
    ObservationIngestResult,
)

__all__ = [
    "DEFAULT_OBSERVATION_SWEEP_LIMIT",
    "ObservationDrainAction",
    "ObservationDrainDecision",
    "ObservationDrainSummary",
    "ObservationOutboxSweeper",
    "RETRYABLE_OBSERVATION_REJECTIONS",
    "route_observation_ingest",
]

DEFAULT_OBSERVATION_SWEEP_LIMIT: Final = 64
RETRYABLE_OBSERVATION_REJECTIONS: Final = frozenset(
    {
        ObservationGapCode.SERVICE_UNAVAILABLE.value,
        ObservationGapCode.VAULT_LOCKED.value,
        ObservationGapCode.MAPPING_MISSING.value,
        "observation_disabled",
        "paused",
    }
)
SAFE_OBSERVATION_REJECTION_REASONS: Final = frozenset(
    {item.value for item in ObservationGapCode} | RETRYABLE_OBSERVATION_REJECTIONS | {"duplicate"}
)


class ObservationDrainAction(str, Enum):  # noqa: UP042 - stable internal value
    ACKNOWLEDGE = "acknowledge"
    RETRY = "retry"
    QUARANTINE = "quarantine"


@dataclass(frozen=True, slots=True)
class ObservationDrainDecision:
    action: ObservationDrainAction
    reason: str | None


def route_observation_ingest(result: ObservationIngestResult) -> ObservationDrainDecision:
    """Classify one typed ingest result without performing storage side effects."""

    if result.disposition in {
        ObservationIngestDisposition.ACCEPTED,
        ObservationIngestDisposition.DUPLICATE,
    }:
        return ObservationDrainDecision(ObservationDrainAction.ACKNOWLEDGE, None)
    supplied_reason = result.reason
    reason = (
        supplied_reason
        if supplied_reason in SAFE_OBSERVATION_REJECTION_REASONS
        else ObservationGapCode.SERVICE_UNAVAILABLE.value
    )
    action = (
        ObservationDrainAction.RETRY
        if reason in RETRYABLE_OBSERVATION_REJECTIONS
        else ObservationDrainAction.QUARANTINE
    )
    return ObservationDrainDecision(action, reason)


class ObservationIngestCoordinator(Protocol):
    def ingest_request(
        self, request: ObservationIngestRequest
    ) -> Awaitable[ObservationIngestResult]: ...


@dataclass(frozen=True, slots=True)
class ObservationDrainSummary:
    attempted: int
    acknowledged: int
    retry_pending: int
    quarantined: int
    reasons: tuple[tuple[str, int], ...]


@dataclass(slots=True)
class ObservationOutboxSweeper:
    """Drain a fair bounded pass directly through the READY coordinator."""

    local: LocalObservationStore
    coordinator: ObservationIngestCoordinator
    limit: int = DEFAULT_OBSERVATION_SWEEP_LIMIT

    def __post_init__(self) -> None:
        if type(self.limit) is not int or isinstance(self.limit, bool) or self.limit < 1:
            raise ValueError("observation_sweep_limit_invalid")

    async def sweep(self) -> ObservationDrainSummary:
        rows = self._fair_pending_rows()
        attempted = 0
        acknowledged = 0
        retry_pending = 0
        quarantined = 0
        reasons: dict[str, int] = {}

        workspaces = tuple(dict.fromkeys(workspace for workspace, _row in rows))
        for workspace in workspaces:
            with self.local.drain_lease(workspace) as owned:
                if not owned:
                    continue
                for selected_workspace, row in rows:
                    if selected_workspace != workspace:
                        continue
                    attempted += 1
                    request = ObservationIngestRequest(
                        codex_session_id=row.codex_session_id,
                        envelope=row.envelope,
                    )
                    try:
                        result = await self.coordinator.ingest_request(request)
                    except Exception:
                        result = ObservationIngestResult(
                            ObservationIngestDisposition.REJECTED,
                            ObservationGapCode.SERVICE_UNAVAILABLE.value,
                            None,
                        )
                    decision = route_observation_ingest(result)
                    attempted_row = self.local.bump_outbox_row_attempt(
                        workspace,
                        row,
                        reason=decision.reason,
                    )
                    if attempted_row is None:
                        continue
                    if decision.reason is not None:
                        reasons[decision.reason] = reasons.get(decision.reason, 0) + 1
                        self.local.note_coverage_gap(workspace, decision.reason)

                    if decision.action is ObservationDrainAction.RETRY:
                        retry_pending += 1
                        continue
                    if decision.action is ObservationDrainAction.QUARANTINE:
                        if self.local.quarantine_outbox_row(
                            workspace,
                            attempted_row,
                            decision.reason or ObservationGapCode.SERVICE_UNAVAILABLE.value,
                        ):
                            quarantined += 1
                        continue
                    if self.local.acknowledge_outbox_row(workspace, attempted_row):
                        acknowledged += 1

        return ObservationDrainSummary(
            attempted=attempted,
            acknowledged=acknowledged,
            retry_pending=retry_pending,
            quarantined=quarantined,
            reasons=tuple(sorted(reasons.items(), key=lambda item: item[0].encode())),
        )

    def _fair_pending_rows(self) -> tuple[tuple[str, ObservationOutboxRow], ...]:
        lanes: dict[tuple[str, str], list[ObservationOutboxRow]] = {}
        for workspace in self.local.pending_workspaces():
            indexed_by_session: dict[str, list[tuple[int, ObservationOutboxRow]]] = {}
            for index, row in enumerate(self.local.list_pending_outbox_rows(workspace)):
                indexed_by_session.setdefault(row.codex_session_id, []).append((index, row))
            for session, rows in indexed_by_session.items():
                lanes[(workspace, session)] = [
                    row for _, row in sorted(rows, key=lambda item: (item[1].attempts, item[0]))
                ]
        selected_per_lane = {lane: 0 for lane in lanes}
        selected: list[tuple[str, ObservationOutboxRow]] = []
        while lanes and len(selected) < self.limit:
            lane = min(
                lanes,
                key=lambda item: (
                    lanes[item][0].attempts,
                    selected_per_lane[item],
                    item[0].encode(),
                    item[1].encode(),
                ),
            )
            workspace, _ = lane
            queue = lanes[lane]
            selected.append((workspace, queue.pop(0)))
            selected_per_lane[lane] += 1
            if not queue:
                lanes.pop(lane)
        return tuple(selected)
