"""Service-level observation coordinator: local routing → task SQLite → ledger."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from yoetz.adapters.integrations.codex_lifecycle import (
    LifecycleMapping,
    load_mapping,
    validate_codex_session_id,
)
from yoetz.adapters.integrations.observation_local import (
    LocalObservationStore,
    session_commitment_from_codex_id,
)
from yoetz.application.observation_advice import (
    ObservationAdviceBuildInput,
    build_observation_advice_snapshot,
)
from yoetz.application.observation_materialize import (
    MaterializedObservationBatch,
    canonical_logical_identity,
    materialize_observation_envelope,
    media_type_for_schema,
    observation_author,
    observation_operation_digest,
)
from yoetz.application.unit_of_work import PreparedMutation, run_prepared_append
from yoetz.domain.observation import (
    ObservationControlCommand,
    ObservationEnvelope,
    ObservationGapCode,
    ObservationIngestDisposition,
    ObservationIngestRequest,
    ObservationIngestResult,
    ObservationRevokeCommand,
    ObservationStatus,
    ObservationStatusQuery,
)
from yoetz.domain.values import timestamp_from_datetime
from yoetz.ports.clock import ClockPort
from yoetz.ports.ids import IdPort
from yoetz.ports.ledger import AppendCommand, AppendEntry, OperationKind
from yoetz.ports.objects import ObjectKind, ObjectMetadata, ObjectRef, ObjectSource
from yoetz.ports.observation import TaskObservationPort
from yoetz.ports.runtime import (
    BundleRuntimePort,
    RouteAccess,
    RouteCommand,
    RuntimeCapability,
    TaskRuntime,
)
from yoetz.protocol.errors import ProtocolValueError, PublicErrorCode, PublicOperationError
from yoetz.protocol.ids import IdKind

__all__ = [
    "ObservationAdviceHook",
    "ObservationCoordinator",
    "ObservationMappingLoader",
]


class ObservationMappingLoader(Protocol):
    def __call__(
        self, codex_session_id: str, *, _state: Path | None = None
    ) -> LifecycleMapping | None: ...


class ObservationAdviceHook(Protocol):
    """Optional post-commit advice hook for the advice/health agent."""

    def __call__(
        self,
        *,
        workspace_commitment: str,
        task_id: str,
        store: TaskObservationPort,
        envelopes: tuple[ObservationEnvelope, ...],
        frontier: str | None,
    ) -> Awaitable[None] | None: ...


def _reject(reason: str, cursor: object | None = None) -> ObservationIngestResult:
    return ObservationIngestResult(
        ObservationIngestDisposition.REJECTED,
        reason,
        cast(Any, cursor),
    )


@dataclass
class ObservationCoordinator:
    """Resolve Codex session → mapped task SQLite observation store + ledger."""

    runtime: BundleRuntimePort
    local: LocalObservationStore
    clock: ClockPort
    ids: IdPort
    mapping_loader: ObservationMappingLoader = load_mapping
    state_root: Path | None = None
    advice_hook: ObservationAdviceHook | None = None

    def __post_init__(self) -> None:
        self._lock = asyncio.Lock()

    async def ingest_request(self, request: ObservationIngestRequest) -> ObservationIngestResult:
        """Coordinator ingest path used by ordinary-control ``observation_ingest``."""

        if type(request) is not ObservationIngestRequest:
            return _reject(ObservationGapCode.CONSENT_MISSING.value)
        try:
            codex_session_id = validate_codex_session_id(request.codex_session_id)
        except ProtocolValueError:
            return _reject(ObservationGapCode.CONSENT_MISSING.value)

        expected = session_commitment_from_codex_id(self.local.key_material(), codex_session_id)
        if request.envelope.session_commitment != expected:
            return _reject(ObservationGapCode.CONSENT_MISSING.value)

        mapping = self.mapping_loader(codex_session_id, _state=self.state_root)
        if mapping is None:
            return _reject(ObservationGapCode.MAPPING_MISSING.value)

        workspace = self.local.find_workspace_for_codex_session(codex_session_id)
        if workspace is None:
            return _reject(ObservationGapCode.CONSENT_MISSING.value)
        consent = self.local.consent_for(workspace)
        if consent is None or not consent.active:
            reason = (
                ObservationGapCode.CONSENT_REVOKED.value
                if consent is not None and consent.revoked_at is not None
                else (
                    "paused"
                    if consent is not None and consent.paused
                    else ObservationGapCode.CONSENT_MISSING.value
                )
            )
            return _reject(reason)

        async with self._lock:
            runtime: TaskRuntime | None = None
            try:
                runtime = await self.runtime.route(
                    RouteCommand(
                        session_id=mapping.yoetz_session_id,
                        writer_id=mapping.yoetz_writer_id,
                        access=RouteAccess.WRITE,
                        required_capabilities=frozenset(
                            {
                                RuntimeCapability.STRUCTURAL_READ,
                                RuntimeCapability.PAYLOAD_READ,
                                RuntimeCapability.WRITE,
                            }
                        ),
                    )
                )
                store = self._observation_store(runtime)
                store.grant_consent(workspace, consent.granted_at)
                store.bind_session(workspace, request.envelope.session_commitment)
                result = await store.ingest(request.envelope)
                if result.disposition is ObservationIngestDisposition.REJECTED:
                    return result

                # ACCEPTED and DUPLICATE both reconcile the durable ledger before
                # reporting success. A DUPLICATE is never an early return: the
                # observation row may already exist while a prior ledger append
                # failed, so re-run the idempotent materialize/append to repair it.
                # If any step raises, the broad guard below turns it into a
                # retryable rejection so the outbox keeps the entry pending.
                batch = materialize_observation_envelope(request.envelope, task_id=runtime.task_id)
                if batch.skip_reason is None and batch.drafts:
                    await self._append_materialized(runtime, request.envelope, batch)

                await self._run_advice(workspace, runtime.task_id, store)
                return result
            except PublicOperationError as exc:
                if exc.code is PublicErrorCode.VAULT_LOCKED:
                    return _reject(ObservationGapCode.VAULT_LOCKED.value)
                if exc.code in {
                    PublicErrorCode.SERVICE_UNAVAILABLE,
                    PublicErrorCode.BUNDLE_BUSY,
                    PublicErrorCode.SESSION_NOT_FOUND,
                }:
                    return _reject(ObservationGapCode.SERVICE_UNAVAILABLE.value)
                if exc.code is PublicErrorCode.SESSION_CONFLICT:
                    return _reject(ObservationGapCode.MAPPING_MISSING.value)
                return _reject(ObservationGapCode.SERVICE_UNAVAILABLE.value)
            except Exception:
                return _reject(ObservationGapCode.SERVICE_UNAVAILABLE.value)
            finally:
                if runtime is not None:
                    with_context = getattr(self.runtime, "release", None)
                    if with_context is not None:
                        await with_context(runtime)

    async def ingest(self, envelope: ObservationEnvelope) -> ObservationIngestResult:
        """ObservationPort-shaped ingest without Codex session id → reject closed."""

        del envelope
        return _reject(ObservationGapCode.MAPPING_MISSING.value)

    async def status(self, query: ObservationStatusQuery) -> ObservationStatus:
        return self.local.status(query)

    async def pause(self, command: ObservationControlCommand) -> ObservationStatus:
        return self.local.pause(command)

    async def resume(self, command: ObservationControlCommand) -> ObservationStatus:
        return self.local.resume(command)

    async def revoke(self, command: ObservationRevokeCommand) -> ObservationStatus:
        return self.local.revoke(command)

    def _observation_store(self, runtime: TaskRuntime) -> TaskObservationPort:
        store = runtime.observation
        if store is None:
            raise PublicOperationError(
                PublicErrorCode.STORAGE_UNSAFE,
                "Observation store connection is unavailable.",
                retryable=True,
            )
        return store

    async def _append_materialized(
        self,
        runtime: TaskRuntime,
        envelope: ObservationEnvelope,
        batch: MaterializedObservationBatch,
    ) -> None:
        writer_id = runtime.writer_id
        if writer_id is None:
            return
        author = observation_author()
        refs: list[ObjectRef] = []
        entries: list[AppendEntry] = []
        for item in batch.drafts:
            commitment = await runtime.objects.commitment_for(
                item.payload_bytes, ObjectKind.EVENT_PAYLOAD
            )
            metadata = ObjectMetadata(
                ObjectKind.EVENT_PAYLOAD,
                media_type_for_schema(item.draft.schema.name),
                runtime.task_id,
                self.clock.now_utc(),
            )
            staged = await runtime.objects.stage(
                ObjectSource(data=item.payload_bytes, declared_size=len(item.payload_bytes)),
                metadata,
            )
            if staged.commitment != commitment:
                raise PublicOperationError(
                    PublicErrorCode.STORAGE_CORRUPT,
                    "Observation payload commitment mismatch.",
                    retryable=False,
                )
            ref = await runtime.objects.finalize(staged)
            refs.append(ref)
            entries.append(
                AppendEntry(
                    item.draft,
                    author,
                    ref,
                    commitment,
                    metadata.media_type,
                    ref.plaintext_size,
                    batch.channel,
                    batch.coverage,
                    item.projection_status,
                )
            )
        digest = observation_operation_digest(
            task_id=runtime.task_id,
            session_id=runtime.session_id,
            writer_id=writer_id,
            logical_identity=canonical_logical_identity(envelope),
            draft_roles=tuple(item.role for item in batch.drafts),
        )
        # Stable operation id from the same material so retries collide.
        operation_id = self._stable_operation_id(digest)
        existing = await runtime.ledger.lookup_operation(writer_id, operation_id)
        if existing is not None:
            return
        command = AppendCommand(
            runtime.task_id,
            runtime.session_id,
            writer_id,
            operation_id,
            OperationKind.PUBLISH_WORK,
            digest,
            None,
            tuple(entries),
        )
        mutation = PreparedMutation(
            command.writer_id,
            command.operation_id,
            command.request_digest,
            command.expected_frontier,
            tuple(refs),
            command,
        )
        await run_prepared_append(runtime.ledger, mutation)

    def _stable_operation_id(self, digest: str) -> str:
        # Derive a request-shaped id from the digest for idempotent appends.
        material = digest.removeprefix("sha256:")
        # Use IdPort when available for uniqueness shape; fall back to deterministic hex.
        try:
            # Prefer deterministic UUID from digest bytes.
            import uuid as _uuid

            raw = bytes.fromhex(material[:32])
            arr = bytearray(raw)
            arr[6] = (arr[6] & 0x0F) | 0x40
            arr[8] = (arr[8] & 0x3F) | 0x80
            from yoetz.protocol.ids import PREFIX_BY_KIND

            return PREFIX_BY_KIND[IdKind.REQUEST] + str(_uuid.UUID(bytes=bytes(arr)))
        except ValueError, TypeError:
            return self.ids.new(IdKind.REQUEST)

    async def _run_advice(self, workspace: str, task_id: str, store: TaskObservationPort) -> None:
        envelopes = store.list_envelopes(workspace)
        status = await store.status(ObservationStatusQuery(workspace))
        snapshot = build_observation_advice_snapshot(
            ObservationAdviceBuildInput(
                envelopes=envelopes,
                lifecycle=status.lifecycle,
                gaps=status.gaps,
                has_real_observation=bool(envelopes),
            )
        )
        if snapshot is not None:
            store.set_advice_snapshot(
                workspace, snapshot, timestamp_from_datetime(self.clock.now_utc())
            )
            # Mirror into local store for hook advice delivery.
            self.local.set_advice_snapshot(workspace, snapshot)
        if self.advice_hook is not None:
            result = self.advice_hook(
                workspace_commitment=workspace,
                task_id=task_id,
                store=store,
                envelopes=envelopes,
                frontier=None if snapshot is None else snapshot.freshness_frontier,
            )
            if asyncio.iscoroutine(result) or isinstance(result, Awaitable):
                await cast(Awaitable[None], result)
