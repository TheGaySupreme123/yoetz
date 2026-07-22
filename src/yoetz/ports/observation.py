"""Local-control live Codex observation boundary."""

from __future__ import annotations

from typing import Protocol

from yoetz.domain.observation import (
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

__all__ = [
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
    "observation_earns_hook_observed",
    "workspace_commitment_from_path",
]


class ObservationPort(Protocol):
    async def ingest(self, envelope: ObservationEnvelope) -> ObservationIngestResult: ...

    async def status(self, query: ObservationStatusQuery) -> ObservationStatus: ...

    async def pause(self, command: ObservationControlCommand) -> ObservationStatus: ...

    async def resume(self, command: ObservationControlCommand) -> ObservationStatus: ...

    async def revoke(self, command: ObservationRevokeCommand) -> ObservationStatus: ...
