"""Local-control live Codex observation boundary."""

from __future__ import annotations

from typing import Protocol

from yoetz.domain.observation import (
    AdviceItem,
    AdviceSnapshot,
    ObservationControlCommand,
    ObservationCursor,
    ObservationEnvelope,
    ObservationGapCode,
    ObservationIngestDisposition,
    ObservationIngestResult,
    ObservationLifecycle,
    ObservationRevokeCommand,
    ObservationSource,
    ObservationStatus,
    ObservationStatusQuery,
    observation_earns_hook_observed,
    workspace_commitment_from_path,
)
from yoetz.domain.values import Timestamp

__all__ = [
    "AdviceItem",
    "AdviceSnapshot",
    "ObservationControlCommand",
    "ObservationCursor",
    "ObservationEnvelope",
    "ObservationGapCode",
    "ObservationIngestDisposition",
    "ObservationIngestResult",
    "ObservationLifecycle",
    "ObservationPort",
    "ObservationRevokeCommand",
    "ObservationSource",
    "ObservationStatus",
    "ObservationStatusQuery",
    "TaskObservationPort",
    "observation_earns_hook_observed",
    "workspace_commitment_from_path",
]


class ObservationPort(Protocol):
    async def ingest(self, envelope: ObservationEnvelope) -> ObservationIngestResult: ...

    async def status(self, query: ObservationStatusQuery) -> ObservationStatus: ...

    async def pause(self, command: ObservationControlCommand) -> ObservationStatus: ...

    async def resume(self, command: ObservationControlCommand) -> ObservationStatus: ...

    async def revoke(self, command: ObservationRevokeCommand) -> ObservationStatus: ...


class TaskObservationPort(Protocol):
    """Durable, mapped-task observation store exposed on a WRITE ``TaskRuntime``.

    This is the least-authority seam production code uses instead of reaching
    into a ledger adapter's private connection. It carries exactly the durable
    observation-store operations the coordinator needs to persist envelopes,
    read back coverage, and record advice snapshots for one mapped task bundle.
    """

    def grant_consent(self, workspace_commitment: str, granted_at: Timestamp) -> None: ...

    def bind_session(self, workspace_commitment: str, session_commitment: str) -> None: ...

    async def ingest(self, envelope: ObservationEnvelope) -> ObservationIngestResult: ...

    async def status(self, query: ObservationStatusQuery) -> ObservationStatus: ...

    def list_envelopes(self, workspace: str) -> tuple[ObservationEnvelope, ...]: ...

    def set_advice_snapshot(
        self, workspace: str, snapshot: AdviceSnapshot, updated_at: Timestamp
    ) -> None: ...

    def load_advice_snapshot(self, workspace: str) -> AdviceSnapshot | None: ...
