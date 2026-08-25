"""Service-level observation coordinator: local routing → task SQLite → ledger."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import timedelta
from functools import partial
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, Literal, Protocol, cast

from yoetz.adapters.approved_checks import ApprovedCheckRunner, ApprovedCheckStatus
from yoetz.adapters.git_subject_state import (
    GitSubjectStateAdapter,
    list_changed_relative_paths,
    open_local_workspace,
)
from yoetz.adapters.integrations.codex_lifecycle import (
    LifecycleMapping,
    load_mapping,
    validate_codex_session_id,
)
from yoetz.adapters.integrations.observation_local import (
    LocalObservationStore,
    session_commitment_from_codex_id,
)
from yoetz.adapters.workspace_inspect import LocalWorkspaceInspectAdapter
from yoetz.application.observation_advice import (
    ObservationAdviceContextBuilder,
    scoped_session_envelopes,
)
from yoetz.application.observation_check_policy import load_observation_check_policy
from yoetz.application.observation_materialize import (
    MATERIALIZATION_MAPPING_VERSION,
    MaterializedObservationBatch,
    approved_check_author,
    canonical_logical_identity,
    materialize_observation_envelope,
    media_type_for_schema,
    observation_author,
    observation_claim_identity,
    observation_operation_digest,
    observation_writer_id,
    stable_observation_id,
    stream_event_is_completed_tool,
)
from yoetz.application.observation_verification import (
    CompletedApprovedCheck,
    ObservationVerificationJob,
    ObservationVerificationRepository,
    ObservationVerificationSupervisor,
    ObservationVerificationWorker,
    VerificationDrainHandle,
    orchestrate_changed_path_inspection,
)
from yoetz.application.unit_of_work import PreparedMutation, run_prepared_append
from yoetz.domain.events import (
    EVIDENCE_SCHEMA_VERSION,
    ActionKind,
    ActionRecordedPayload,
    EventDraft,
    EventSchema,
    EvidenceContentAvailability,
    EvidenceDigestBinding,
    EvidenceDigestProvenance,
    EvidenceDigestSubject,
    EvidenceKind,
    EvidenceRecordedPayload,
    ResultOutcome,
    ResultRecordedPayload,
    encode_payload,
    media_type_for,
)
from yoetz.domain.findings import (
    FINDING_KIND_TRAITS,
    Finding,
    FindingId,
    FindingKind,
    FindingOrigin,
)
from yoetz.domain.observation import (
    OBSERVATION_BACKPRESSURE_REASON,
    AdviceItem,
    ObservationContentChunk,
    ObservationContentKind,
    ObservationControlCommand,
    ObservationEnvelope,
    ObservationGapCode,
    ObservationIngestDisposition,
    ObservationIngestRequest,
    ObservationIngestResult,
    ObservationRevokeCommand,
    ObservationStatus,
    ObservationStatusQuery,
    observation_envelope_to_json,
)
from yoetz.domain.values import (
    Frontier,
    JsonObject,
    SubjectStateRef,
    action_id,
    event_id,
    evidence_id,
    frontier_from_json,
    object_id,
    result_id,
    timestamp_from_datetime,
    timestamp_from_string,
)
from yoetz.kernel.projections import ProjectionRecord, ProjectionState
from yoetz.observability.logging import record_unexpected_exception_without_raising
from yoetz.observability.privacy import redact_sensitive_content
from yoetz.ports.clock import ClockPort
from yoetz.ports.ids import IdPort
from yoetz.ports.ledger import (
    AcceptedEventSummary,
    AppendCommand,
    AppendEntry,
    AppendResult,
    AppendWarning,
    OperationKind,
    OperationRecord,
    OperationState,
    ProjectionView,
)
from yoetz.ports.objects import ObjectKind, ObjectMetadata, ObjectRef, ObjectSource
from yoetz.ports.observation import TaskObservationPort
from yoetz.ports.runtime import (
    BundleRuntimePort,
    RouteAccess,
    RouteCommand,
    RuntimeCapability,
    TaskRuntime,
)
from yoetz.ports.subject_state import SubjectStateCaptureCommand, SubjectStateFormat
from yoetz.protocol.canonical import canonical_digest, canonical_encode, strict_json_parse
from yoetz.protocol.coverage import EvidenceImmutability, PublicationChannel, coverage_for_channel
from yoetz.protocol.errors import ProtocolValueError, PublicErrorCode, PublicOperationError
from yoetz.protocol.ids import IdKind

_ADVICE_FINDING_KIND_BY_RULE: Final = MappingProxyType(
    {
        "failed_command_unresolved": FindingKind.FAILED_WORK_OMITTED,
        "edit_after_successful_check": FindingKind.STALE_EVIDENCE_FOR_CHANGED_STATE,
        "completion_without_verification": FindingKind.CLAIM_WITHOUT_ADMISSIBLE_EVIDENCE,
        "static_test_for_live_claim": FindingKind.EVIDENCE_DOES_NOT_SUPPORT_CLAIM,
        "subagent_finding_unaddressed": FindingKind.MATERIAL_LIMITATION_OMITTED,
        "change_outside_plan": FindingKind.DIFF_DOES_NOT_MATCH_ACCOUNT,
        "observation_gap_or_stale": FindingKind.LEDGER_STALE_OR_INCOMPLETE,
        "semantic_claim_without_attempt": FindingKind.CLAIM_WITHOUT_ADMISSIBLE_EVIDENCE,
    }
)


def _materialized_advice_items(items: Sequence[AdviceItem]) -> tuple[AdviceItem, ...]:
    """Keep advice that describes repairable work; machine conditions remain advice only."""

    return tuple(
        item
        for item in items
        if item.origin == "deterministic" and item.rule_code in _ADVICE_FINDING_KIND_BY_RULE
    )


def _append_result_from_committed(record: object) -> AppendResult | None:
    """Rebuild frontier metadata from a completed append so retry can note motion."""

    if type(record) is not OperationRecord:
        return None
    if record.state is not OperationState.COMPLETE or record.result_canonical is None:
        return None
    try:
        parsed = strict_json_parse(record.result_canonical)
    except ProtocolValueError, TypeError, ValueError:
        return None
    if not isinstance(parsed, Mapping):
        return None
    result_map = cast(Mapping[str, object], parsed)
    accepted_raw = result_map.get("accepted")
    if not isinstance(accepted_raw, list | tuple) or not accepted_raw:
        return None
    try:
        accepted = tuple(
            AcceptedEventSummary(
                cast(str, item["event_id"]),
                int(cast(str, item["ingestion_sequence"])),
                int(cast(str, item["writer_sequence"])),
                cast(str, item["entry_digest"]),
                cast(
                    Literal["projected", "unknown_unprojected"],
                    item["projection_status"],
                ),
            )
            for item in cast(tuple[Mapping[str, object], ...], accepted_raw)
        )
        warnings_raw = result_map.get("warnings") or ()
        warnings = tuple(
            AppendWarning(cast(str, value)) for value in cast(tuple[object, ...], warnings_raw)
        )
        return AppendResult(
            "replayed",
            accepted,
            frontier_from_json(result_map["subject_frontier"]),
            frontier_from_json(result_map["result_frontier"]),
            warnings,
        )
    except KeyError, ProtocolValueError, TypeError, ValueError:
        return None


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


def _empty_advice_event_ref_cache() -> dict[tuple[str, str], tuple[str, ...]]:
    return {}


def _empty_storage_corrupt_sessions() -> set[str]:
    return set()


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
    advice_context_builder: ObservationAdviceContextBuilder = field(
        default_factory=ObservationAdviceContextBuilder
    )
    verification_supervisor: ObservationVerificationSupervisor | None = None
    observation_enabled: bool = True
    _advice_event_ref_cache: dict[tuple[str, str], tuple[str, ...]] = field(
        default_factory=_empty_advice_event_ref_cache, init=False, repr=False
    )
    _storage_corrupt_sessions: set[str] = field(
        default_factory=_empty_storage_corrupt_sessions, init=False, repr=False
    )
    _local_executor: ThreadPoolExecutor | None = field(default=None, init=False, repr=False)

    async def _local[ResultT](self, call: Callable[[], ResultT]) -> ResultT:
        """Run one blocking local-store call off the service event loop.

        Every ``LocalObservationStore`` entry point takes the same potentially blocking
        cross-process flock, so a read is no safer on the loop than a write. Reads route through
        this dedicated bounded pool too; the store also bounds acquisition so cancellation cannot
        leave a worker parked indefinitely (#238). The reentrant thread lock is acquired and
        released inside one worker on each hop, so no lock is held across an await.
        """

        executor = self._local_executor
        if executor is None:
            executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="yoetz-obs-local")
            self._local_executor = executor
        return await asyncio.get_running_loop().run_in_executor(executor, call)

    def close(self) -> None:
        """Stop accepting local-store work; bounded lock waits let running workers retire."""

        executor, self._local_executor = self._local_executor, None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    def __post_init__(self) -> None:
        self._lock = asyncio.Lock()
        if type(self.observation_enabled) is not bool:
            raise TypeError("observation_enabled_invalid")
        if self.verification_supervisor is None:
            # Tests and non-ready compositions still drain inline when no supervisor
            # is attached; production ready composition always injects one.
            self.verification_supervisor = None

    async def rediscover_pending_verification(self) -> None:
        """Rebuild drain handles for consented workspaces with durable pending jobs.

        Called once after ready-lifecycle supervisor start so restart reclaim can
        complete without waiting for a fresh hook ingest.
        """

        supervisor = self.verification_supervisor
        if supervisor is None:
            return
        for workspace in await self._local(self.local.list_consented_workspaces):
            await self._rediscover_workspace_pending_verification(supervisor, workspace)
        supervisor.notify()

    async def _rediscover_workspace_pending_verification(
        self,
        supervisor: ObservationVerificationSupervisor,
        workspace: str,
        *,
        sessions: tuple[str, ...] | None = None,
        start_index: int = 0,
    ) -> None:
        """Register the next pending session repository for one workspace."""

        if supervisor.closed or supervisor.has_handle(workspace):
            return
        consent = await self._local(partial(self.local.consent_for, workspace))
        if consent is None or not consent.active:
            return
        if sessions is None:
            sessions = await self._local(
                partial(self.local.codex_sessions_for_workspace, workspace)
            )
        for session_index, codex_session_id in enumerate(sessions[start_index:], start=start_index):
            if supervisor.closed or supervisor.has_handle(workspace):
                return
            mapping = self.mapping_loader(codex_session_id, _state=self.state_root)
            if mapping is None:
                continue
            runtime: TaskRuntime | None = None
            try:
                runtime = await self.runtime.route(
                    RouteCommand(
                        session_id=mapping.yoetz_session_id,
                        writer_id=observation_writer_id(
                            mapping.yoetz_task_id, mapping.yoetz_session_id
                        ),
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
                repository = getattr(store, "verification_repository", None)
                if not callable(repository):
                    continue
                repo = cast(ObservationVerificationRepository, repository())
                pending = repo.list_pending_workspaces()
                if workspace not in pending:
                    continue
                worker = await self._rebuild_verification_worker(
                    runtime,
                    workspace,
                    store,
                    legacy_writer_id=mapping.yoetz_writer_id,
                )
                if worker is None:
                    continue

                async def _after(
                    bound_workspace: str = workspace,
                    bound_runtime: TaskRuntime = runtime,
                    bound_store: TaskObservationPort = store,
                    bound_legacy_writer_id: str = mapping.yoetz_writer_id,
                ) -> None:
                    await self._run_advice(
                        bound_workspace,
                        bound_runtime,
                        bound_store,
                        legacy_writer_id=bound_legacy_writer_id,
                    )

                async def _release_and_continue(
                    bound_runtime: TaskRuntime = runtime,
                    bound_workspace: str = workspace,
                    bound_sessions: tuple[str, ...] = sessions,
                    next_session_index: int = session_index + 1,
                ) -> None:
                    await self.runtime.release(bound_runtime)
                    await self._rediscover_workspace_pending_verification(
                        supervisor,
                        bound_workspace,
                        sessions=bound_sessions,
                        start_index=next_session_index,
                    )

                registered = supervisor.register(
                    VerificationDrainHandle(
                        workspace_commitment=workspace,
                        worker=worker,
                        after_complete=_after,
                        on_idle=_release_and_continue,
                    )
                )
                if registered:
                    runtime = None
                return
            except Exception:
                await self._local(
                    partial(
                        self.local.note_coverage_gap,
                        workspace,
                        ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value,
                    )
                )
            finally:
                if runtime is not None:
                    release = getattr(self.runtime, "release", None)
                    if release is not None:
                        await release(runtime)

    async def ingest_request(self, request: ObservationIngestRequest) -> ObservationIngestResult:
        """Coordinator ingest path used by ordinary-control ``observation_ingest``."""

        if not self.observation_enabled:
            return _reject("observation_disabled")
        if type(request) is not ObservationIngestRequest:
            return _reject(ObservationGapCode.CONSENT_MISSING.value)
        try:
            codex_session_id = validate_codex_session_id(request.codex_session_id)
        except ProtocolValueError:
            return _reject(ObservationGapCode.CONSENT_MISSING.value)

        key_material = await self._local(self.local.key_material)
        expected = session_commitment_from_codex_id(key_material, codex_session_id)
        if request.envelope.session_commitment != expected:
            return _reject(ObservationGapCode.CONSENT_MISSING.value)
        if codex_session_id in self._storage_corrupt_sessions:
            return _reject(ObservationGapCode.OBSERVATION_STORAGE_CORRUPT.value)

        mapping = self.mapping_loader(codex_session_id, _state=self.state_root)
        if mapping is None:
            return _reject(ObservationGapCode.MAPPING_MISSING.value)

        workspace = await self._local(
            partial(self.local.find_workspace_for_codex_session, codex_session_id)
        )
        if workspace is None:
            return _reject(ObservationGapCode.CONSENT_MISSING.value)
        consent = await self._local(partial(self.local.consent_for, workspace))
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
            if codex_session_id in self._storage_corrupt_sessions:
                return _reject(ObservationGapCode.OBSERVATION_STORAGE_CORRUPT.value)
            runtime: TaskRuntime | None = None
            stage = "runtime_route"
            try:
                runtime = await self.runtime.route(
                    RouteCommand(
                        session_id=mapping.yoetz_session_id,
                        writer_id=observation_writer_id(
                            mapping.yoetz_task_id, mapping.yoetz_session_id
                        ),
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
                stage = "store_prepare"
                store = self._observation_store(runtime)
                store.grant_consent(workspace, consent.granted_at)
                store.bind_session(workspace, request.envelope.session_commitment)
                captured_refs, content_redacted = await self._capture_content(
                    runtime,
                    store,
                    workspace=workspace,
                    envelope=request.envelope,
                    chunks=request.content_chunks,
                )
                gaps = set(request.envelope.gap_codes)
                if content_redacted:
                    gaps.add(ObservationGapCode.CONTENT_REDACTED.value)
                envelope = replace(
                    request.envelope,
                    content_object_refs=tuple(
                        sorted(
                            {*request.envelope.content_object_refs, *captured_refs},
                            key=str.encode,
                        )
                    ),
                    gap_codes=tuple(sorted(gaps, key=str.encode)),
                )
                stage = "store_ingest"
                result = await store.ingest(envelope)
                if result.disposition is ObservationIngestDisposition.REJECTED:
                    return result

                # ACCEPTED and DUPLICATE both reconcile the durable ledger before
                # reporting success. A DUPLICATE is never an early return: the
                # observation row may already exist while a prior ledger append
                # failed, so re-run the idempotent materialize/append to repair it.
                # If any step raises, the broad guard below turns it into a
                # retryable rejection so the outbox keeps the entry pending.
                batch = materialize_observation_envelope(envelope, task_id=runtime.task_id)
                if batch.skip_reason is None and batch.drafts:
                    stage = "ledger_append"
                    claim = await self._append_materialized(
                        runtime,
                        envelope,
                        batch,
                        legacy_writer_id=mapping.yoetz_writer_id,
                    )
                    if claim is not None:
                        operation_id, materialization_digest, append_result = claim
                        # The claim key is role-scoped so the phases of one host
                        # call (pre action, paired result, permission, subagent)
                        # never contend, and the recorded mapping version is the
                        # source-independent materialization one so hook/stream
                        # copies of a phase can union their source masks.
                        stage = "identity_claim"
                        store.record_logical_identity_claim(
                            workspace=workspace,
                            logical_identity=observation_claim_identity(
                                envelope, tuple(item.role for item in batch.drafts)
                            ),
                            materialization_digest=materialization_digest,
                            operation_id=operation_id,
                            source_mask=1 if envelope.source.value == "codex_hook" else 2,
                            mapping_version=MATERIALIZATION_MAPPING_VERSION,
                            materialized_at=timestamp_from_datetime(self.clock.now_utc()),
                        )
                        stage = "ledger_append"
                        if append_result is not None:
                            await self._local(
                                partial(
                                    self.local.note_frontier_motion,
                                    workspace,
                                    codex_session_id,
                                    from_sequence=append_result.subject_frontier.sequence,
                                    to_sequence=append_result.result_frontier.sequence,
                                    head_digest=append_result.result_frontier.head_digest,
                                    observation_record_count=len(append_result.accepted),
                                    task_id=runtime.task_id,
                                )
                            )

                stage = "verification"
                await self._enqueue_verification(
                    runtime,
                    workspace,
                    store,
                    envelope,
                    legacy_writer_id=mapping.yoetz_writer_id,
                )
                stage = "advice"
                await self._run_advice(
                    workspace,
                    runtime,
                    store,
                    legacy_writer_id=mapping.yoetz_writer_id,
                    session_commitment=envelope.session_commitment,
                )
                return result
            except PublicOperationError as exc:
                if exc.code in {
                    PublicErrorCode.OPERATION_PENDING,
                    PublicErrorCode.BUNDLE_BUSY,
                    PublicErrorCode.FRONTIER_CONFLICT,
                }:
                    # Designed back-pressure, not a failure (#351). ADR-022
                    # decision 4 makes observation appends receive retryable
                    # OPERATION_PENDING while check acquisition or a frozen-case
                    # barrier is active; BUNDLE_BUSY and FRONTIER_CONFLICT are
                    # the same transient coordination one layer down. The
                    # durable outbox keeps the row pending and retries after the
                    # barrier clears, so no unexpected-exception diagnostic is
                    # recorded and no service_unavailable gap is projected.
                    return _reject(OBSERVATION_BACKPRESSURE_REASON)
                record_unexpected_exception_without_raising(
                    exc,
                    component="application.observation_coordinator",
                    operation=(f"observation_ingest_{stage}_{exc.code.value.lower()}"),
                )
                if exc.code is PublicErrorCode.VAULT_LOCKED:
                    return _reject(ObservationGapCode.VAULT_LOCKED.value)
                if exc.code is PublicErrorCode.STORAGE_CORRUPT:
                    if stage == "identity_claim":
                        # A conflicting logical-identity claim poisons one
                        # envelope, not the bundle. ADR-010 scopes the
                        # generation latch to bundle corruption, so reject just
                        # this envelope and keep the session observable.
                        return _reject(ObservationGapCode.DEDUP_CONFLICT.value)
                    self._storage_corrupt_sessions.add(codex_session_id)
                    return _reject(ObservationGapCode.OBSERVATION_STORAGE_CORRUPT.value)
                if exc.code in {
                    PublicErrorCode.SERVICE_UNAVAILABLE,
                    PublicErrorCode.SESSION_NOT_FOUND,
                }:
                    return _reject(ObservationGapCode.SERVICE_UNAVAILABLE.value)
                if exc.code is PublicErrorCode.SESSION_CONFLICT:
                    return _reject(ObservationGapCode.MAPPING_MISSING.value)
                return _reject(ObservationGapCode.SERVICE_UNAVAILABLE.value)
            except Exception as exc:
                record_unexpected_exception_without_raising(
                    exc,
                    component="application.observation_coordinator",
                    operation=f"observation_ingest_{stage}_failed",
                )
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
        return await self._local(partial(self.local.status, query))

    async def pause(self, command: ObservationControlCommand) -> ObservationStatus:
        return await self._local(partial(self.local.pause, command))

    async def resume(self, command: ObservationControlCommand) -> ObservationStatus:
        return await self._local(partial(self.local.resume, command))

    async def revoke(self, command: ObservationRevokeCommand) -> ObservationStatus:
        status = await self._local(partial(self.local.revoke, command))
        # The local fence is authoritative for immediately stopping new capture.
        # Best-effort bundle propagation additionally deactivates the encrypted
        # locator and exact-digest trust rows while retaining encrypted evidence.
        seen_tasks: set[str] = set()
        revoked_sessions = await self._local(
            partial(self.local.codex_sessions_for_workspace, command.workspace_commitment)
        )
        for codex_session_id in revoked_sessions:
            mapping = self.mapping_loader(codex_session_id, _state=self.state_root)
            if mapping is None or mapping.yoetz_task_id in seen_tasks:
                continue
            runtime: TaskRuntime | None = None
            try:
                runtime = await self.runtime.route(
                    RouteCommand(
                        session_id=mapping.yoetz_session_id,
                        writer_id=observation_writer_id(
                            mapping.yoetz_task_id, mapping.yoetz_session_id
                        ),
                        access=RouteAccess.WRITE,
                        required_capabilities=frozenset({RuntimeCapability.WRITE}),
                    )
                )
                await self._observation_store(runtime).revoke(command)
                seen_tasks.add(mapping.yoetz_task_id)
            except Exception:
                await self._local(
                    partial(
                        self.local.note_coverage_gap,
                        command.workspace_commitment,
                        ObservationGapCode.SERVICE_UNAVAILABLE.value,
                    )
                )
            finally:
                if runtime is not None:
                    await self.runtime.release(runtime)
        return status

    def _observation_store(self, runtime: TaskRuntime) -> TaskObservationPort:
        store = runtime.observation
        if store is None:
            raise PublicOperationError(
                PublicErrorCode.STORAGE_UNSAFE,
                "Observation store connection is unavailable.",
                retryable=True,
            )
        return store

    async def _route_observation_runtime(self, task_id: str, session_id: str) -> TaskRuntime:
        return await self.runtime.route(
            RouteCommand(
                session_id=session_id,
                writer_id=observation_writer_id(task_id, session_id),
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

    async def _append_materialized(
        self,
        runtime: TaskRuntime,
        envelope: ObservationEnvelope,
        batch: MaterializedObservationBatch,
        *,
        legacy_writer_id: str | None = None,
    ) -> tuple[str, str, AppendResult | None] | None:
        writer_id = runtime.writer_id
        if writer_id is None:
            return None
        digest = observation_operation_digest(
            task_id=runtime.task_id,
            session_id=runtime.session_id,
            writer_id=writer_id,
            logical_identity=canonical_logical_identity(envelope),
            draft_roles=tuple(item.role for item in batch.drafts),
        )
        # Check the stable operation identity before staging payloads. A replay
        # after "ledger committed, local outbox not acknowledged" must not
        # create orphan encrypted objects on every retry.
        operation_id = self._stable_operation_id(digest)
        existing = await runtime.ledger.lookup_operation(writer_id, operation_id)
        if existing is not None:
            return operation_id, digest, _append_result_from_committed(existing)
        if legacy_writer_id is not None and legacy_writer_id != writer_id:
            legacy_digest = observation_operation_digest(
                task_id=runtime.task_id,
                session_id=runtime.session_id,
                writer_id=legacy_writer_id,
                logical_identity=canonical_logical_identity(envelope),
                draft_roles=tuple(item.role for item in batch.drafts),
            )
            legacy_operation_id = self._stable_operation_id(legacy_digest)
            legacy_existing = await runtime.ledger.lookup_operation(
                legacy_writer_id, legacy_operation_id
            )
            if legacy_existing is not None:
                return (
                    legacy_operation_id,
                    legacy_digest,
                    _append_result_from_committed(legacy_existing),
                )

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
        append_result = await run_prepared_append(runtime.ledger, mutation)
        return operation_id, digest, append_result

    async def _materialize_approved_check(
        self,
        runtime: TaskRuntime,
        completed: CompletedApprovedCheck,
        *,
        legacy_writer_id: str | None = None,
    ) -> None:
        """Append one idempotent service-owned action/evidence/result graph."""

        writer_id = runtime.writer_id
        if writer_id is None:
            return
        result = completed.result
        operation_digest = canonical_digest(
            JsonObject(
                {
                    "format": "yoetz.approved-check-ledger-materialization/1",
                    "job_id": completed.job.job_id,
                    "approval_commitment": completed.job.approval_commitment,
                    "result_digest": result.result_digest,
                    "task_id": runtime.task_id,
                    "session_id": runtime.session_id,
                    "writer_id": writer_id,
                }
            )
        )
        operation_id = self._stable_operation_id(operation_digest)
        if await runtime.ledger.lookup_operation(writer_id, operation_id) is not None:
            return
        if legacy_writer_id is not None and legacy_writer_id != writer_id:
            legacy_digest = canonical_digest(
                JsonObject(
                    {
                        "format": "yoetz.approved-check-ledger-materialization/1",
                        "job_id": completed.job.job_id,
                        "approval_commitment": completed.job.approval_commitment,
                        "result_digest": result.result_digest,
                        "task_id": runtime.task_id,
                        "session_id": runtime.session_id,
                        "writer_id": legacy_writer_id,
                    }
                )
            )
            legacy_operation_id = self._stable_operation_id(legacy_digest)
            if (
                await runtime.ledger.lookup_operation(legacy_writer_id, legacy_operation_id)
                is not None
            ):
                return

        receipt_document = JsonObject(
            {
                "format": "yoetz.approved-check-evidence/1",
                "job_id": completed.job.job_id,
                "approval_id": completed.approval_id,
                "approval_commitment": completed.job.approval_commitment,
                "result_digest": result.result_digest,
                "status": result.status.value,
                "outcome": result.outcome.value,
                "exit_status": result.exit_status,
                "output_digest": result.output_digest,
                "output_bytes": result.output_bytes,
                "output_object_id": completed.output_object_id,
                "subject_state_before": completed.job.subject_state_digest,
                "subject_state_after": completed.subject_state_after,
                "is_current": completed.is_current,
                "recorded_at": completed.recorded_at,
            }
        )
        receipt_bytes = canonical_encode(receipt_document)
        receipt_metadata = ObjectMetadata(
            ObjectKind.CAPTURED_CONTENT,
            "application/vnd.yoetz.approved-check-evidence+json",
            runtime.task_id,
            self.clock.now_utc(),
        )
        staged_receipt = await runtime.objects.stage(
            ObjectSource(data=receipt_bytes, declared_size=len(receipt_bytes)), receipt_metadata
        )
        receipt_ref = await runtime.objects.finalize(staged_receipt)
        receipt_digest = canonical_digest(receipt_document)

        source_identity = f"approved-check:{completed.job.job_id}:{result.result_digest}"
        mapping = "approved-check/1.0.0"
        action_value = action_id(
            stable_observation_id(
                kind=IdKind.ACTION,
                task_id=runtime.task_id,
                source_identity=source_identity,
                mapping_version=mapping,
                role="action",
            )
        )
        evidence_value = evidence_id(
            stable_observation_id(
                kind=IdKind.EVIDENCE,
                task_id=runtime.task_id,
                source_identity=source_identity,
                mapping_version=mapping,
                role="evidence",
            )
        )
        result_value = result_id(
            stable_observation_id(
                kind=IdKind.RESULT,
                task_id=runtime.task_id,
                source_identity=source_identity,
                mapping_version=mapping,
                role="result",
            )
        )
        action_event = event_id(
            stable_observation_id(
                kind=IdKind.EVENT,
                task_id=runtime.task_id,
                source_identity=source_identity,
                mapping_version=mapping,
                role="action_event",
            )
        )
        evidence_event = event_id(
            stable_observation_id(
                kind=IdKind.EVENT,
                task_id=runtime.task_id,
                source_identity=source_identity,
                mapping_version=mapping,
                role="evidence_event",
            )
        )
        result_event = event_id(
            stable_observation_id(
                kind=IdKind.EVENT,
                task_id=runtime.task_id,
                source_identity=source_identity,
                mapping_version=mapping,
                role="result_event",
            )
        )
        occurred_at = timestamp_from_string(completed.recorded_at)
        subject_state = SubjectStateRef(
            described_state=f"approved-check:{completed.job.subject_state_digest}"
        )
        evidence_payload = EvidenceRecordedPayload(
            evidence_id=evidence_value,
            evidence_kind=EvidenceKind.TEST_RESULT,
            strength=EvidenceImmutability.IMMUTABLE_SNAPSHOT,
            observed_at=occurred_at,
            captured_object_id=object_id(receipt_ref.object_id),
            content_digest=receipt_digest,
            description=f"Approved check result status={result.status.value}",
            subject_state=subject_state,
            digest_binding=EvidenceDigestBinding(
                subject=EvidenceDigestSubject.APPROVED_CHECK_RECEIPT,
                content_availability=EvidenceContentAvailability.CAPTURED,
                byte_count=len(receipt_bytes),
                provenance=EvidenceDigestProvenance.APPROVED_CHECK,
                approval_commitment=completed.job.approval_commitment,
                approved_check_result_digest=result.result_digest,
            ),
        )
        drafts = (
            EventDraft(
                action_event,
                EventSchema("action_recorded", "1.0.0"),
                occurred_at,
                (),
                ActionRecordedPayload(
                    action_id=action_value,
                    action_kind=ActionKind.COMMAND,
                    description=f"Ran approved check {completed.approval_id}",
                    command="approved-check-service",
                    subject_state=subject_state,
                ),
                (),
                (),
            ),
            EventDraft(
                evidence_event,
                EventSchema("evidence_recorded", EVIDENCE_SCHEMA_VERSION),
                occurred_at,
                (action_event,),
                evidence_payload,
                (object_id(receipt_ref.object_id),),
                (),
            ),
            EventDraft(
                result_event,
                EventSchema("result_recorded", "1.0.0"),
                occurred_at,
                tuple(sorted((action_event, evidence_event), key=str.encode)),
                ResultRecordedPayload(
                    result_id=result_value,
                    action_id=action_value,
                    outcome=(
                        ResultOutcome.SUCCESS
                        if result.status is ApprovedCheckStatus.PASSED
                        else ResultOutcome.FAILURE
                    ),
                    exit_status=result.exit_status,
                    summary=f"Approved check status={result.status.value}",
                    subject_state=subject_state,
                    evidence_refs=(evidence_value,),
                ),
                (),
                (evidence_value,),
            ),
        )
        coverage = replace(
            coverage_for_channel(PublicationChannel.ENGINE_DERIVED),
            evidence_immutability=EvidenceImmutability.IMMUTABLE_SNAPSHOT,
        )
        refs: list[ObjectRef] = [receipt_ref]
        entries: list[AppendEntry] = []
        for draft in drafts:
            payload_bytes = canonical_encode(encode_payload(cast(Any, draft.payload)))
            metadata = ObjectMetadata(
                ObjectKind.EVENT_PAYLOAD,
                media_type_for(draft.schema.name),
                runtime.task_id,
                self.clock.now_utc(),
            )
            staged = await runtime.objects.stage(
                ObjectSource(data=payload_bytes, declared_size=len(payload_bytes)), metadata
            )
            payload_ref = await runtime.objects.finalize(staged)
            refs.append(payload_ref)
            entries.append(
                AppendEntry(
                    draft,
                    approved_check_author(),
                    payload_ref,
                    payload_ref.commitment,
                    metadata.media_type,
                    payload_ref.plaintext_size,
                    PublicationChannel.ENGINE_DERIVED,
                    coverage,
                    "projected",
                )
            )
        command = AppendCommand(
            runtime.task_id,
            runtime.session_id,
            writer_id,
            operation_id,
            OperationKind.PUBLISH_WORK,
            operation_digest,
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

    async def _capture_content(
        self,
        runtime: TaskRuntime,
        store: TaskObservationPort,
        *,
        workspace: str,
        envelope: ObservationEnvelope,
        chunks: tuple[ObservationContentChunk, ...],
    ) -> tuple[tuple[str, ...], bool]:
        """Assemble encrypted captured-content chunk objects before SQLite references them."""

        if not chunks:
            return (), False
        logical_identity = canonical_logical_identity(envelope)
        object_ids: set[str] = set()
        any_redacted = False
        for chunk in chunks:
            existing = store.content_manifest_object_id(
                workspace=workspace,
                logical_identity=logical_identity,
                chunk=chunk,
            )
            if existing is not None:
                object_ids.add(existing)
                any_redacted = any_redacted or chunk.redacted
                continue
            safe_content, detected = redact_sensitive_content(chunk.content)
            any_redacted = any_redacted or chunk.redacted or detected
            stored_chunk = replace(
                chunk,
                content=safe_content,
                redacted=chunk.redacted or detected,
            )
            manifest = canonical_encode(
                JsonObject(
                    {
                        "format": "yoetz.observation-content/1",
                        "content_kind": stored_chunk.content_kind.value,
                        "correlation_identity": stored_chunk.correlation_identity,
                        "source_commitment": stored_chunk.source_commitment,
                        "media_type": stored_chunk.media_type,
                        "part_index": stored_chunk.part_index,
                        "part_count": stored_chunk.part_count,
                        "redacted": stored_chunk.redacted,
                        "content_b64": base64.b64encode(safe_content).decode("ascii"),
                    }
                )
            )
            metadata = ObjectMetadata(
                ObjectKind.CAPTURED_CONTENT,
                "application/vnd.yoetz.observation-content+json",
                runtime.task_id,
                self.clock.now_utc(),
            )
            staged = await runtime.objects.stage(
                ObjectSource(data=manifest, declared_size=len(manifest)),
                metadata,
            )
            ref = await runtime.objects.finalize(staged)
            store.record_content_manifest(
                workspace=workspace,
                logical_identity=logical_identity,
                chunk=stored_chunk,
                ref=ref,
                recorded_at=timestamp_from_datetime(self.clock.now_utc()),
            )
            if stored_chunk.content_kind is ObservationContentKind.WORKSPACE_LOCATOR:
                store.bind_workspace_locator(
                    workspace=workspace,
                    locator_ref=ref,
                    bound_at=timestamp_from_datetime(self.clock.now_utc()),
                )
            object_ids.add(ref.object_id)
        return tuple(sorted(object_ids, key=str.encode)), any_redacted

    async def _enqueue_verification(
        self,
        runtime: TaskRuntime,
        workspace: str,
        store: TaskObservationPort,
        envelope: ObservationEnvelope,
        *,
        legacy_writer_id: str | None = None,
    ) -> None:
        """Capture subject state, enqueue durable work, wake the supervisor.

        When no supervisor is attached (unit tests), drain inline so existing
        scenarios keep completing within the same await.
        """

        worker = await self._prepare_verification_worker(
            runtime,
            workspace,
            store,
            envelope,
            legacy_writer_id=legacy_writer_id,
        )
        if worker is None:
            return
        if self.verification_supervisor is not None:
            supervisor = self.verification_supervisor
            if supervisor.has_handle(workspace):
                supervisor.notify(workspace)
                return

            deferred_runtime = await self._route_observation_runtime(
                runtime.task_id, runtime.session_id
            )
            try:
                deferred_store = self._observation_store(deferred_runtime)
                deferred_worker = await self._rebuild_verification_worker(
                    deferred_runtime,
                    workspace,
                    deferred_store,
                    legacy_writer_id=legacy_writer_id,
                )
                if deferred_worker is None:
                    await self.runtime.release(deferred_runtime)
                    return

                async def _after() -> None:
                    await self._run_advice(
                        workspace,
                        deferred_runtime,
                        deferred_store,
                        legacy_writer_id=legacy_writer_id,
                        session_commitment=envelope.session_commitment,
                    )

                async def _release() -> None:
                    await self.runtime.release(deferred_runtime)

                registered = supervisor.register(
                    VerificationDrainHandle(
                        workspace_commitment=workspace,
                        worker=deferred_worker,
                        after_complete=_after,
                        on_idle=_release,
                    )
                )
                if not registered:
                    await self.runtime.release(deferred_runtime)
            except BaseException:
                await self.runtime.release(deferred_runtime)
                raise
            supervisor.notify(workspace)
            return
        while await worker.run_once() is not None:
            pass

    async def _prepare_verification_worker(
        self,
        runtime: TaskRuntime,
        workspace: str,
        store: TaskObservationPort,
        envelope: ObservationEnvelope,
        *,
        legacy_writer_id: str | None = None,
    ) -> ObservationVerificationWorker | None:
        """Build a worker and enqueue if subject state changed; never run checks here."""

        if envelope.event_kind != "PostToolUse" and not stream_event_is_completed_tool(
            envelope.event_kind, envelope.structural_payload
        ):
            return None
        required = (
            "workspace_locator_descriptor",
            "verification_repository",
            "latest_verification_subject_digest",
            "policy_digest_is_trusted",
            "record_trusted_check_policy",
        )
        if any(not callable(getattr(store, name, None)) for name in required):
            return None
        descriptor = store.workspace_locator_descriptor(workspace)
        if descriptor is None:
            await self._local(
                partial(
                    self.local.note_coverage_gap,
                    workspace,
                    ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value,
                )
            )
            return None
        locator_ref = await runtime.objects.resolve_verified(*descriptor)
        encrypted = b"".join([chunk async for chunk in runtime.objects.open_verified(locator_ref)])
        parsed = strict_json_parse(encrypted)
        if not isinstance(parsed, dict) and type(parsed) is not JsonObject:
            return None
        content_b64 = parsed.get("content_b64")
        content_kind = parsed.get("content_kind")
        if (
            content_kind != ObservationContentKind.WORKSPACE_LOCATOR.value
            or type(content_b64) is not str
        ):
            return None
        try:
            locator = base64.b64decode(content_b64.encode("ascii"), validate=True).decode("utf-8")
            handle = open_local_workspace(Path(locator))
            policy, _raw_policy = load_observation_check_policy(Path(locator))
        except Exception:
            await self._local(
                partial(
                    self.local.note_coverage_gap,
                    workspace,
                    ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value,
                )
            )
            return None
        if not await self._local(
            partial(self.local.policy_digest_is_trusted, workspace, policy.raw_digest)
        ):
            await self._local(
                partial(
                    self.local.note_coverage_gap,
                    workspace,
                    ObservationGapCode.POLICY_UNTRUSTED.value,
                )
            )
            return None
        now = timestamp_from_datetime(self.clock.now_utc())
        if not store.policy_digest_is_trusted(workspace, policy.raw_digest):
            trust_payload = canonical_encode(
                JsonObject(
                    {
                        "format": "yoetz.approved-check-policy-trust/1",
                        "workspace_commitment": workspace,
                        "policy_digest": policy.raw_digest,
                        "trusted_at": now.wire,
                    }
                )
            )
            trust_ref = await self._encrypt_captured_content(runtime, trust_payload)
            store.record_trusted_check_policy(
                workspace=workspace,
                policy_digest=policy.raw_digest,
                trust_ref=trust_ref,
                trusted_at=now,
            )
        capture = GitSubjectStateAdapter()

        def subject_digest(_handle: object) -> str:
            result = capture.capture(
                SubjectStateCaptureCommand(handle, SubjectStateFormat.GIT_STRUCTURAL_V1)
            )
            if result.subject_state is None:
                raise RuntimeError("subject_state_unavailable")
            return canonical_digest(
                JsonObject(
                    {
                        "tree_digest": result.subject_state.tree_digest,
                        "diff_digest": result.subject_state.diff_digest,
                    }
                )
            )

        current_digest = await asyncio.to_thread(subject_digest, handle)
        repository = cast(ObservationVerificationRepository, store.verification_repository())

        async def persist_output(job: ObservationVerificationJob, content: bytes) -> str | None:
            chunk = ObservationContentChunk(
                content_kind=ObservationContentKind.APPROVED_CHECK_OUTPUT,
                correlation_identity=f"check:{job.job_id}",
                source_commitment=f"hmac-sha256:{'0' * 64}",
                media_type="text/plain",
                part_index=0,
                part_count=1,
                content=content,
                redacted=True,
            )
            manifest = canonical_encode(
                JsonObject(
                    {
                        "format": "yoetz.observation-content/1",
                        "content_kind": chunk.content_kind.value,
                        "correlation_identity": chunk.correlation_identity,
                        "source_commitment": chunk.source_commitment,
                        "media_type": chunk.media_type,
                        "part_index": 0,
                        "part_count": 1,
                        "redacted": True,
                        "content_b64": base64.b64encode(content).decode("ascii"),
                    }
                )
            )
            ref = await self._encrypt_captured_content(runtime, manifest)
            store.record_content_manifest(
                workspace=workspace,
                logical_identity=f"verification:{job.job_id}",
                chunk=chunk,
                ref=ref,
                recorded_at=timestamp_from_datetime(self.clock.now_utc()),
            )
            return ref.object_id

        def now_wire() -> str:
            return timestamp_from_datetime(self.clock.now_utc()).wire

        def lease_expiry() -> str:
            return timestamp_from_datetime(self.clock.now_utc() + timedelta(minutes=2)).wire

        worker = ObservationVerificationWorker(
            repository=repository,
            runner=ApprovedCheckRunner({item.approval_commitment: item for item in policy.checks}),
            workspace_provider=lambda _workspace: handle,
            policy_provider=lambda _workspace, _digest: policy.checks,
            capture_subject_state=subject_digest,
            persist_output=persist_output,
            service_generation=runtime.fence.service_generation,
            lease_owner=runtime.fence.service_instance_id,
            now=now_wire,
            lease_expires_at=lease_expiry,
            materialize_result=lambda completed: self._materialize_approved_check(
                runtime, completed, legacy_writer_id=legacy_writer_id
            ),
        )
        worker.enqueue_if_changed(
            workspace=workspace,
            policy_digest=policy.raw_digest,
            approvals=policy.checks,
            previous_subject_state_digest=store.latest_verification_subject_digest(workspace),
            subject_state_digest=current_digest,
        )
        # Persist inspection snapshot + session route when schema-4 helpers exist.
        route_recorder = getattr(store, "record_workspace_session_route", None)
        if callable(route_recorder):
            route_recorder(
                workspace=workspace,
                yoetz_session_id=runtime.session_id,
                yoetz_task_id=runtime.task_id,
                yoetz_writer_id=runtime.writer_id,
                codex_session_commitment=envelope.session_commitment,
                bound_at=now,
            )
        inspect_recorder = getattr(store, "record_inspection_snapshot", None)
        if callable(inspect_recorder):
            relative_paths: tuple[str, ...] = ()
            changed_digest = current_digest
            facts_object_id: str | None = None
            excerpt_object_id: str | None = None
            try:
                relative_paths = list_changed_relative_paths(handle)
                changed_digest = canonical_digest(
                    JsonObject({"relative_paths": list(relative_paths)})
                )
                orchestration = orchestrate_changed_path_inspection(
                    workspace=handle,
                    inspect_port=LocalWorkspaceInspectAdapter(),
                    relative_paths=relative_paths,
                    changed_paths_digest=changed_digest,
                )
                if orchestration.inspect_fact is not None:
                    fact_bytes = canonical_encode(
                        JsonObject(
                            {
                                "format": "yoetz.observation-inspect-fact/1",
                                "selection_digest": orchestration.inspect_fact.selection_digest,
                                "relative_paths": list(orchestration.inspect_fact.relative_paths),
                                "changed_paths_digest": changed_digest,
                            }
                        )
                    )
                    facts_ref = await self._encrypt_captured_content(runtime, fact_bytes)
                    facts_object_id = facts_ref.object_id
                if orchestration.inspect is not None and orchestration.inspect.artifacts:
                    excerpt_parts = tuple(
                        {
                            "path": item.relative_path,
                            "digest": item.content_digest,
                            "excerpt_b64": base64.b64encode(item.excerpt[:512]).decode("ascii"),
                        }
                        for item in orchestration.inspect.artifacts[:16]
                        if item.excerpt
                    )
                    if excerpt_parts:
                        excerpt_bytes = canonical_encode(
                            JsonObject(
                                {
                                    "format": "yoetz.observation-inspect-excerpt/1",
                                    "artifacts": list(excerpt_parts),
                                }
                            )
                        )
                        excerpt_ref = await self._encrypt_captured_content(runtime, excerpt_bytes)
                        excerpt_object_id = excerpt_ref.object_id
            except Exception:
                await self._local(
                    partial(
                        self.local.note_coverage_gap,
                        workspace,
                        ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value,
                    )
                )
                relative_paths = ()
            inspect_recorder(
                workspace=workspace,
                yoetz_session_id=runtime.session_id,
                subject_state_digest=current_digest,
                changed_paths_digest=changed_digest,
                relative_paths=relative_paths,
                facts_object_id=facts_object_id,
                excerpt_object_id=excerpt_object_id,
                recorded_at=now,
            )
        return worker

    async def _rebuild_verification_worker(
        self,
        runtime: TaskRuntime,
        workspace: str,
        store: TaskObservationPort,
        *,
        legacy_writer_id: str | None = None,
    ) -> ObservationVerificationWorker | None:
        """Rebuild a drain worker for already-pending durable jobs (no enqueue)."""

        required = (
            "workspace_locator_descriptor",
            "verification_repository",
            "policy_digest_is_trusted",
        )
        if any(not callable(getattr(store, name, None)) for name in required):
            return None
        descriptor = store.workspace_locator_descriptor(workspace)
        if descriptor is None:
            return None
        locator_ref = await runtime.objects.resolve_verified(*descriptor)
        encrypted = b"".join([chunk async for chunk in runtime.objects.open_verified(locator_ref)])
        parsed = strict_json_parse(encrypted)
        if not isinstance(parsed, dict) and type(parsed) is not JsonObject:
            return None
        content_b64 = parsed.get("content_b64")
        content_kind = parsed.get("content_kind")
        if (
            content_kind != ObservationContentKind.WORKSPACE_LOCATOR.value
            or type(content_b64) is not str
        ):
            return None
        try:
            locator = base64.b64decode(content_b64.encode("ascii"), validate=True).decode("utf-8")
            handle = open_local_workspace(Path(locator))
            policy, _raw_policy = load_observation_check_policy(Path(locator))
        except Exception:
            return None
        if not store.policy_digest_is_trusted(workspace, policy.raw_digest):
            return None
        capture = GitSubjectStateAdapter()

        def subject_digest(_handle: object) -> str:
            result = capture.capture(
                SubjectStateCaptureCommand(handle, SubjectStateFormat.GIT_STRUCTURAL_V1)
            )
            if result.subject_state is None:
                raise RuntimeError("subject_state_unavailable")
            return canonical_digest(
                JsonObject(
                    {
                        "tree_digest": result.subject_state.tree_digest,
                        "diff_digest": result.subject_state.diff_digest,
                    }
                )
            )

        repository = cast(ObservationVerificationRepository, store.verification_repository())

        async def persist_output(job: ObservationVerificationJob, content: bytes) -> str | None:
            chunk = ObservationContentChunk(
                content_kind=ObservationContentKind.APPROVED_CHECK_OUTPUT,
                correlation_identity=f"check:{job.job_id}",
                source_commitment=f"hmac-sha256:{'0' * 64}",
                media_type="text/plain",
                part_index=0,
                part_count=1,
                content=content,
                redacted=True,
            )
            manifest = canonical_encode(
                JsonObject(
                    {
                        "format": "yoetz.observation-content/1",
                        "content_kind": chunk.content_kind.value,
                        "correlation_identity": chunk.correlation_identity,
                        "source_commitment": chunk.source_commitment,
                        "media_type": chunk.media_type,
                        "part_index": 0,
                        "part_count": 1,
                        "redacted": True,
                        "content_b64": base64.b64encode(content).decode("ascii"),
                    }
                )
            )
            ref = await self._encrypt_captured_content(runtime, manifest)
            store.record_content_manifest(
                workspace=workspace,
                logical_identity=f"verification:{job.job_id}",
                chunk=chunk,
                ref=ref,
                recorded_at=timestamp_from_datetime(self.clock.now_utc()),
            )
            return ref.object_id

        def now_wire() -> str:
            return timestamp_from_datetime(self.clock.now_utc()).wire

        def lease_expiry() -> str:
            return timestamp_from_datetime(self.clock.now_utc() + timedelta(minutes=2)).wire

        return ObservationVerificationWorker(
            repository=repository,
            runner=ApprovedCheckRunner({item.approval_commitment: item for item in policy.checks}),
            workspace_provider=lambda _workspace: handle,
            policy_provider=lambda _workspace, _digest: policy.checks,
            capture_subject_state=subject_digest,
            persist_output=persist_output,
            service_generation=runtime.fence.service_generation,
            lease_owner=runtime.fence.service_instance_id,
            now=now_wire,
            lease_expires_at=lease_expiry,
            materialize_result=lambda completed: self._materialize_approved_check(
                runtime, completed, legacy_writer_id=legacy_writer_id
            ),
        )

    async def _run_verification(
        self,
        runtime: TaskRuntime,
        workspace: str,
        store: TaskObservationPort,
        envelope: ObservationEnvelope,
    ) -> None:
        """Deprecated alias: enqueue (+ inline drain without supervisor)."""

        await self._enqueue_verification(runtime, workspace, store, envelope)

    async def _encrypt_captured_content(self, runtime: TaskRuntime, content: bytes) -> ObjectRef:
        metadata = ObjectMetadata(
            ObjectKind.CAPTURED_CONTENT,
            "application/vnd.yoetz.observation-content+json",
            runtime.task_id,
            self.clock.now_utc(),
        )
        staged = await runtime.objects.stage(
            ObjectSource(data=content, declared_size=len(content)), metadata
        )
        return await runtime.objects.finalize(staged)

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

    async def _run_advice(
        self,
        workspace: str,
        runtime: TaskRuntime | str,
        store: TaskObservationPort,
        *,
        legacy_writer_id: str | None = None,
        session_commitment: str | None = None,
    ) -> None:
        # A plain string is a bare task id from callers with no runtime in hand;
        # anything else is runtime-shaped (the concrete TaskRuntime in
        # production, duck-typed stand-ins in tests).
        task_id = runtime if isinstance(runtime, str) else runtime.task_id
        session_id = None if isinstance(runtime, str) else runtime.session_id
        if session_commitment is None and type(session_id) is str:
            # Post-commit callers (verification drains, restart rediscovery)
            # carry no envelope; recover the mapped Codex session commitment
            # from the durable route so the build stays session-scoped (#352).
            route_lookup = getattr(store, "codex_session_commitment_for_session", None)
            if callable(route_lookup):
                routed = route_lookup(workspace=workspace, yoetz_session_id=session_id)
                if type(routed) is str:
                    session_commitment = routed
        envelopes = scoped_session_envelopes(store, workspace, session_commitment)
        snapshot = await self.advice_context_builder.build(
            workspace,
            store,
            yoetz_session_id=session_id if type(session_id) is str else None,
            session_commitment=session_commitment,
        )
        if snapshot is not None:
            # Materialize before publishing the snapshot to either durable cache.
            # Snapshot identity participates in deterministic suppression, so
            # advancing it first could make a failed ledger append disappear on
            # retry and let the outbox ACK without the finding ever landing.
            if not isinstance(runtime, str) and runtime.writer_id is not None:
                await self._materialize_advice_findings(
                    runtime,
                    envelopes,
                    snapshot,
                    legacy_writer_id=legacy_writer_id,
                )
            now = timestamp_from_datetime(self.clock.now_utc())
            store.set_advice_snapshot(workspace, snapshot, now)
            session_setter = getattr(store, "set_session_advice_snapshot", None)
            if callable(session_setter) and type(session_id) is str:
                session_setter(
                    workspace=workspace,
                    yoetz_session_id=session_id,
                    snapshot=snapshot,
                    updated_at=now,
                )
            recorder = getattr(store, "record_advice_history", None)
            if callable(recorder):
                recorder(
                    workspace=workspace,
                    snapshot=snapshot,
                    verification_state=(
                        "stale"
                        if ObservationGapCode.VERIFICATION_STALE.value
                        in snapshot.confidence_coverage.known_gaps
                        else "unavailable"
                    ),
                    semantic_state=(
                        "ready"
                        if any(
                            item.origin == "semantic_model_derived"
                            for item in snapshot.ranked_items
                        )
                        else "disabled"
                    ),
                    freshness=(
                        "current" if not snapshot.confidence_coverage.known_gaps else "partial"
                    ),
                    recorded_at=now,
                )
            # Mirror into local store for hook advice delivery.
            await self._local(partial(self.local.set_advice_snapshot, workspace, snapshot))
            if not isinstance(runtime, str) and type(session_id) is str:
                await self._local(
                    partial(
                        self.local.set_session_advice_snapshot,
                        workspace,
                        yoetz_session_id=session_id,
                        snapshot=snapshot,
                    )
                )
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

    def _materialized_event_refs(
        self, task_id: str, envelope: ObservationEnvelope
    ) -> tuple[str, ...]:
        key = (task_id, canonical_digest(observation_envelope_to_json(envelope)))
        cached = self._advice_event_ref_cache.pop(key, None)
        if cached is not None:
            self._advice_event_ref_cache[key] = cached
            return cached
        batch = materialize_observation_envelope(envelope, task_id=task_id)
        refs = tuple(str(item.draft.event_id) for item in batch.drafts)
        self._advice_event_ref_cache[key] = refs
        while len(self._advice_event_ref_cache) > 4096:
            self._advice_event_ref_cache.pop(next(iter(self._advice_event_ref_cache)))
        return refs

    async def _materialize_advice_findings(
        self,
        runtime: TaskRuntime,
        envelopes: tuple[ObservationEnvelope, ...],
        snapshot: object,
        *,
        legacy_writer_id: str | None = None,
    ) -> None:
        from yoetz.domain.observation import AdviceSnapshot

        if type(snapshot) is not AdviceSnapshot or runtime.writer_id is None:
            return
        candidate_items = _materialized_advice_items(snapshot.ranked_items)
        if not candidate_items:
            return
        finding_projection = await runtime.ledger.load_projection(
            runtime.session_id, ProjectionView.CANDIDATE_FINDINGS
        )
        projected_findings: Mapping[FindingId, ProjectionRecord[Finding]] = (
            finding_projection.state.findings
            if finding_projection is not None and type(finding_projection.state) is ProjectionState
            else {}
        )
        candidate_items = tuple(
            item
            for item in candidate_items
            if (
                (existing := projected_findings.get(item.finding_id)) is None
                or existing.payload is None
            )
        )
        if not candidate_items:
            # A readable condition finding already anchors the durable record.
            # Rolling evidence remains current in the observation snapshot and
            # coverage/gap state; appending a revision here would invalidate
            # checks and grow the ledger once per routine envelope.
            return
        refs_by_source: dict[str, set[str]] = {}
        for envelope in envelopes:
            refs_by_source.setdefault(envelope.source_identity, set()).update(
                self._materialized_event_refs(runtime.task_id, envelope)
            )
        known_event_ids: set[str] = set()
        lifecycle_ref: str | None = None
        async for record in runtime.ledger.load_events(runtime.session_id):
            known_event_ids.add(str(record.event_id))
            if lifecycle_ref is None and record.schema.name in {
                "session_opened",
                "session_resumed",
            }:
                lifecycle_ref = str(record.event_id)
        projection = await runtime.ledger.load_projection(
            runtime.session_id, ProjectionView.COMPACT
        )
        frontier = Frontier.genesis() if projection is None else projection.frontier
        items: list[tuple[AdviceItem, tuple[str, ...]]] = []
        for item in candidate_items:
            matched_refs = {
                ref
                for source_ref in item.evidence_refs
                for ref in refs_by_source.get(source_ref, ())
                if ref in known_event_ids
            }
            if not matched_refs:
                # A candidate that names no envelope is a standing condition about the session,
                # so anchor it on the session lifecycle event: one stable ref for the life of the
                # condition, which is what lets check and receipt collapse repeats. Where no
                # lifecycle event exists yet, fall back to every observed ref that was actually
                # appended rather than dropping the finding — a finding that silently fails to
                # land is the one outcome this must never produce. Computed draft IDs that
                # never entered the ledger stay out of subject_refs: receipt case construction
                # must not see citations that cannot be closed against the accepted prefix.
                if lifecycle_ref is not None:
                    matched_refs.add(lifecycle_ref)
                else:
                    for observed in refs_by_source.values():
                        matched_refs.update(ref for ref in observed if ref in known_event_ids)
            subject_refs = tuple(sorted(matched_refs, key=str.encode)[:64])
            if not subject_refs:
                continue
            items.append((item, subject_refs))
        if not items:
            return
        request_digest_value = canonical_digest(
            JsonObject(
                {
                    "format": "yoetz.observation-advice-findings/1",
                    "task_id": runtime.task_id,
                    "findings": tuple(
                        {
                            "finding_id": str(item.finding_id),
                            "subject_refs": subject_refs,
                        }
                        for item, subject_refs in items
                    ),
                }
            )
        )
        operation_id = self._stable_operation_id(request_digest_value)
        existing = await runtime.ledger.lookup_operation(runtime.writer_id, operation_id)
        if existing is not None:
            return
        if legacy_writer_id is not None and legacy_writer_id != runtime.writer_id:
            if await runtime.ledger.lookup_operation(legacy_writer_id, operation_id) is not None:
                return
        entries: list[AppendEntry] = []
        object_refs: list[ObjectRef] = []
        envelope_coverage = coverage_for_channel(PublicationChannel.ENGINE_DERIVED)
        for item, subject_ref_values in items:
            subject_refs = tuple(event_id(ref) for ref in subject_ref_values)
            kind = _ADVICE_FINDING_KIND_BY_RULE[item.rule_code]
            policy_id = (
                "work-integrity"
                if kind
                in {
                    FindingKind.FAILED_WORK_OMITTED,
                    FindingKind.STALE_EVIDENCE_FOR_CHANGED_STATE,
                    FindingKind.CLAIM_WITHOUT_ADMISSIBLE_EVIDENCE,
                    FindingKind.LEDGER_STALE_OR_INCOMPLETE,
                }
                else "research-evidence"
            )
            finding = Finding(
                item.finding_id,
                kind,
                FindingOrigin.DETERMINISTIC,
                FINDING_KIND_TRAITS[kind][0],
                item.summary,
                item.detail,
                subject_refs,
                policy_id,
                "0.1.0",
                frontier,
                item.coverage,
                None,
            )
            schema = EventSchema("finding_recorded", "1.0.0")
            draft = EventDraft(
                event_id(
                    stable_observation_id(
                        kind=IdKind.EVENT,
                        task_id=runtime.task_id,
                        source_identity=(
                            f"advice-candidate:{item.finding_id}:"
                            f"{canonical_digest(JsonObject({'subject_refs': subject_ref_values}))}"
                        ),
                        mapping_version="obs-advice/1.1.0",
                        role=str(item.finding_id),
                    )
                ),
                schema,
                timestamp_from_datetime(self.clock.now_utc()),
                (),
                finding,
                (),
                (),
            )
            payload = canonical_encode(encode_payload(finding))
            metadata = ObjectMetadata(
                ObjectKind.EVENT_PAYLOAD,
                media_type_for(schema.name),
                runtime.task_id,
                self.clock.now_utc(),
            )
            staged = await runtime.objects.stage(
                ObjectSource(data=payload, declared_size=len(payload)), metadata
            )
            ref = await runtime.objects.finalize(staged)
            object_refs.append(ref)
            entries.append(
                AppendEntry(
                    draft,
                    observation_author(),
                    ref,
                    ref.commitment,
                    metadata.media_type,
                    ref.plaintext_size,
                    PublicationChannel.ENGINE_DERIVED,
                    envelope_coverage,
                    "projected",
                )
            )
        command = AppendCommand(
            runtime.task_id,
            runtime.session_id,
            runtime.writer_id,
            operation_id,
            OperationKind.PUBLISH_WORK,
            request_digest_value,
            None,
            tuple(entries),
        )
        await run_prepared_append(
            runtime.ledger,
            PreparedMutation(
                command.writer_id,
                command.operation_id,
                command.request_digest,
                command.expected_frontier,
                tuple(object_refs),
                command,
            ),
        )
