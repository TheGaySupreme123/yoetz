"""Local-control live Codex observation boundary."""

from __future__ import annotations

from typing import Protocol

from yoetz.domain.observation import (
    AdviceItem,
    AdviceSnapshot,
    ObservationContentChunk,
    ObservationContentKind,
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
from yoetz.kernel.policies.observation_advice import ObservationCheckFact
from yoetz.ports.objects import ObjectRef

__all__ = [
    "AdviceItem",
    "AdviceSnapshot",
    "ObservationControlCommand",
    "ObservationContentChunk",
    "ObservationContentKind",
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

    async def revoke(self, command: ObservationRevokeCommand) -> ObservationStatus: ...

    def list_envelopes(self, workspace: str) -> tuple[ObservationEnvelope, ...]: ...

    def set_advice_snapshot(
        self, workspace: str, snapshot: AdviceSnapshot, updated_at: Timestamp
    ) -> None: ...

    def load_advice_snapshot(self, workspace: str) -> AdviceSnapshot | None: ...

    def load_latest_advice_snapshot(self) -> AdviceSnapshot | None: ...

    def record_advice_history(
        self,
        *,
        workspace: str,
        snapshot: AdviceSnapshot,
        verification_state: str,
        semantic_state: str,
        freshness: str,
        recorded_at: Timestamp,
    ) -> None: ...

    def record_content_manifest(
        self,
        *,
        workspace: str,
        logical_identity: str,
        chunk: ObservationContentChunk,
        ref: ObjectRef,
        recorded_at: Timestamp,
    ) -> None: ...

    def content_manifest_object_id(
        self,
        *,
        workspace: str,
        logical_identity: str,
        chunk: ObservationContentChunk,
    ) -> str | None: ...

    def bind_workspace_locator(
        self,
        *,
        workspace: str,
        locator_ref: ObjectRef,
        bound_at: Timestamp,
    ) -> None: ...

    def workspace_locator_descriptor(self, workspace: str) -> tuple[str, str] | None: ...

    def record_trusted_check_policy(
        self,
        *,
        workspace: str,
        policy_digest: str,
        trust_ref: ObjectRef,
        trusted_at: Timestamp,
    ) -> None: ...

    def policy_digest_is_trusted(self, workspace: str, policy_digest: str) -> bool: ...

    def latest_verification_subject_digest(self, workspace: str) -> str | None: ...

    def load_check_facts(self, workspace: str) -> tuple[ObservationCheckFact, ...]: ...

    def verification_repository(self) -> object: ...

    def record_logical_identity_claim(
        self,
        *,
        workspace: str,
        logical_identity: str,
        materialization_digest: str,
        operation_id: str,
        source_mask: int,
        mapping_version: str,
        materialized_at: Timestamp,
    ) -> None: ...
