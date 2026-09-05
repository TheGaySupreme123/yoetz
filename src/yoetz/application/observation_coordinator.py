"""Service-level observation coordinator: local routing → task SQLite → ledger."""

from __future__ import annotations

import asyncio
import base64
import hashlib
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
    acquire_session_lock,
    load_mapping,
    load_route_history,
    store_mapping,
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
    MATERIALIZATION_LEGACY_MAPPING_VERSIONS,
    MATERIALIZATION_MAPPING_VERSION,
    SESSION_BOUND_MAPPING_VERSIONS,
    MaterializedObservationBatch,
    approved_check_author,
    canonical_logical_identity,
    materialize_observation_envelope,
    materialize_observation_inspection_snapshot,
    materialize_observation_outcome_correction,
    media_type_for_schema,
    observation_author,
    observation_claim_identity,
    observation_content_identity,
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
from yoetz.application.unit_of_work import (
    PreparedMutation,
    PreSubmissionCancelled,
    abandon_preappend_objects,
    run_prepared_append,
)
from yoetz.domain.events import (
    EVIDENCE_TYPED_SCHEMA_VERSION,
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
    ObservationContentManifest,
    ObservationControlCommand,
    ObservationEnvelope,
    ObservationGapCode,
    ObservationIngestDisposition,
    ObservationIngestRequest,
    ObservationIngestResult,
    ObservationInspectionSnapshot,
    ObservationRevokeCommand,
    ObservationSource,
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
from yoetz.observability.privacy import prepare_persisted_plaintext
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
from yoetz.ports.objects import ObjectKind, ObjectMetadata, ObjectRef, ObjectSource, StagedObject
from yoetz.ports.observation import ObservationLogicalIdentityClaim, TaskObservationPort
from yoetz.ports.runtime import (
    BundleRuntimePort,
    RouteAccess,
    RouteCommand,
    RuntimeCapability,
    TaskRuntime,
)
from yoetz.ports.subject_state import SubjectStateCaptureCommand, SubjectStateFormat
from yoetz.ports.workspace_inspect import InspectedArtifact
from yoetz.protocol.canonical import canonical_digest, canonical_encode, strict_json_parse
from yoetz.protocol.coverage import EvidenceImmutability, PublicationChannel, coverage_for_channel
from yoetz.protocol.errors import ProtocolValueError, PublicErrorCode, PublicOperationError
from yoetz.protocol.ids import IdKind, is_valid_id

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

_LEGACY_UNPAIRED_REPLAY_PROFILE: Final = "legacy-paired-replay"


def _legacy_unpaired_replay_envelope(
    envelope: ObservationEnvelope,
) -> ObservationEnvelope | None:
    """Return a synthetic pre-#607 pairing view for historical role recovery.

    Before host/profile pairing was explicit, every post with an unpaired gap
    materialized as ``unpaired_evidence``. A current Claude/Cursor profile can
    materialize the same retained envelope as a post-only action/result, so
    retries need the old role set while probing pre-1.6 operation identities.
    The synthetic profile is never persisted or sent across the wire.
    """

    if ObservationGapCode.UNPAIRED_EVENT.value not in envelope.gap_codes:
        return None
    structural = dict(envelope.structural_payload)
    structural.update(
        {
            "capability_profile_id": _LEGACY_UNPAIRED_REPLAY_PROFILE,
            "pairing_mode": "paired",
            "correlation_kind": "tool_call_id",
        }
    )
    return replace(envelope, structural_payload=JsonObject(structural))


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
    "ObservationMappingStorer",
    "build_inspection_excerpt_manifest",
]

# One catalog lookup already returns the current task binding. Extra hops cover a
# rotation that lands between the superseded error and the retry (#577).
_MAX_SUPERSEDED_ROUTE_HOPS: Final = 4

_INSPECT_EXCERPT_FORMAT: Final = "yoetz.observation-inspect-excerpt/1"
_MAX_INSPECT_EXCERPT_ARTIFACTS: Final = 16
_MAX_INSPECT_EXCERPT_PREFIX_BYTES: Final = 512


def build_inspection_excerpt_manifest(
    artifacts: Sequence[InspectedArtifact],
    *,
    canaries: tuple[bytes, ...] = (),
) -> tuple[bytes | None, bool, bool, bool]:
    """Scan changed-file excerpts before base64 encoding.

    Returns ``(canonical_bytes, capture_unavailable, redacted, truncated)``. Secret matches
    persist only path/digest/finding-kind metadata. A scanner failure or canary match stores no
    excerpt object. Redaction and truncation remain explicit coverage limitations without
    requiring a later consumer to reopen captured bytes.
    """

    parts: list[JsonObject] = []
    capture_unavailable = False
    redacted = False
    truncated = False
    seen = 0
    for item in artifacts:
        if type(item) is not InspectedArtifact:
            continue
        if seen >= _MAX_INSPECT_EXCERPT_ARTIFACTS:
            break
        seen += 1
        if not item.excerpt:
            continue
        prefix = item.excerpt[:_MAX_INSPECT_EXCERPT_PREFIX_BYTES]
        item_truncated = item.excerpt_truncated or len(item.excerpt) > len(prefix)
        truncated = truncated or item_truncated
        scan = prepare_persisted_plaintext(prefix, canaries=canaries)
        if not scan.persist:
            capture_unavailable = True
            break
        if scan.redacted:
            redacted = True
            parts.append(
                JsonObject(
                    {
                        "path": item.relative_path,
                        "digest": item.content_digest,
                        "redacted": True,
                        "excerpt_truncated": item_truncated,
                        "byte_length": item.byte_length,
                        "finding_kinds": list(scan.finding_kinds),
                    }
                )
            )
            continue
        parts.append(
            JsonObject(
                {
                    "path": item.relative_path,
                    "digest": item.content_digest,
                    "redacted": False,
                    "excerpt_truncated": item_truncated,
                    "byte_length": item.byte_length,
                    "excerpt_b64": base64.b64encode(scan.content).decode("ascii"),
                }
            )
        )
    if capture_unavailable:
        return None, True, redacted, truncated
    if not parts:
        return None, False, redacted, truncated
    return (
        canonical_encode(JsonObject({"format": _INSPECT_EXCERPT_FORMAT, "artifacts": parts})),
        False,
        redacted,
        truncated,
    )


class ObservationMappingLoader(Protocol):
    def __call__(
        self, codex_session_id: str, *, _state: Path | None = None
    ) -> LifecycleMapping | None: ...


class ObservationMappingStorer(Protocol):
    def __call__(self, mapping: LifecycleMapping, *, _state: Path | None = None) -> None: ...


def _session_superseded_binding(
    error: PublicOperationError, *, expected_task_id: str
) -> tuple[str, str] | None:
    """Return successor session/writer ids when the public error carries the current binding."""

    if error.code is not PublicErrorCode.SESSION_NOT_FOUND:
        return None
    details = error.safe_details
    if details.get("reason_code") != "session_superseded":
        return None
    task_id = details.get("task_id")
    session_id = details.get("session_id")
    writer_id = details.get("writer_id")
    if (
        type(task_id) is not str
        or type(session_id) is not str
        or type(writer_id) is not str
        or task_id != expected_task_id
        or not is_valid_id(IdKind.TASK, task_id)
        or not is_valid_id(IdKind.SESSION, session_id)
        or not is_valid_id(IdKind.WRITER, writer_id)
    ):
        return None
    return session_id, writer_id


def _observation_writer_routes(
    runtime: TaskRuntime,
    route_history: Sequence[LifecycleMapping],
    *,
    state_root: Path | None,
) -> tuple[tuple[tuple[str, str], ...], bool]:
    """Return the bounded writer routes admitted during one observation request.

    A start operation records its cooperative writer in the lifecycle mapping,
    while observation materialization historically used the deterministic
    observation writer for the same ``(task, session)`` pair.  Both identities
    remain valid for replay, and a session retirement can leave several such
    pairs behind before the request reaches its current route.  Keep the route
    order stable and remove duplicates so the legacy probe is deterministic.
    """

    candidates: list[tuple[str, str]] = []
    if runtime.writer_id is not None:
        candidates.append((runtime.session_id, runtime.writer_id))
    for mapping in route_history:
        candidates.extend(
            (
                (mapping.yoetz_session_id, mapping.yoetz_writer_id),
                (
                    mapping.yoetz_session_id,
                    observation_writer_id(mapping.yoetz_task_id, mapping.yoetz_session_id),
                ),
            )
        )
    # A prior route may have been cached by a previous process before this
    # request started.  Keep it in the same bounded candidate list as the
    # request-local hops; the sidecar is task-filtered by the lifecycle adapter.
    history_truncated = False
    if route_history:
        current = route_history[-1]
        durable_history = load_route_history(current, _state=state_root)
        if durable_history is None:
            raise PublicOperationError(
                PublicErrorCode.STORAGE_CORRUPT,
                "Observation route history is invalid.",
                retryable=False,
            )
        history_truncated = durable_history.truncated
        for session_id, writer_id in durable_history.routes:
            candidates.extend(
                (
                    (session_id, writer_id),
                    (session_id, observation_writer_id(current.yoetz_task_id, session_id)),
                )
            )
    return tuple(dict.fromkeys(candidates)), history_truncated


def _load_logical_identity_claim(
    store: TaskObservationPort,
    *,
    workspace: str,
    logical_identity: str,
) -> ObservationLogicalIdentityClaim | None:
    """Read one optional durable claim without widening test-only store seams."""

    loader = getattr(store, "load_logical_identity_claim", None)
    if not callable(loader):
        return None
    raw_claim = loader(workspace=workspace, logical_identity=logical_identity)
    if raw_claim is None:
        return None
    if type(raw_claim) is not tuple:
        raise PublicOperationError(
            PublicErrorCode.STORAGE_CORRUPT,
            "Observation logical identity claim is invalid.",
            retryable=False,
        )
    claim_values = cast(tuple[object, ...], raw_claim)
    if len(claim_values) != 3 or not all(type(value) is str for value in claim_values):
        raise PublicOperationError(
            PublicErrorCode.STORAGE_CORRUPT,
            "Observation logical identity claim is invalid.",
            retryable=False,
        )
    materialization_digest = cast(str, claim_values[0])
    operation_id = cast(str, claim_values[1])
    mapping_version = cast(str, claim_values[2])
    if (
        len(materialization_digest) != 71
        or not materialization_digest.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in materialization_digest[7:])
        or not is_valid_id(IdKind.REQUEST, operation_id)
        or mapping_version
        not in {MATERIALIZATION_MAPPING_VERSION, *MATERIALIZATION_LEGACY_MAPPING_VERSIONS}
    ):
        raise PublicOperationError(
            PublicErrorCode.STORAGE_CORRUPT,
            "Observation logical identity claim is invalid.",
            retryable=False,
        )
    return cast(ObservationLogicalIdentityClaim, raw_claim)


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
    mapping_storer: ObservationMappingStorer = store_mapping
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
            predecessor_session_id = mapping.yoetz_session_id
            predecessor_writer_id = mapping.yoetz_writer_id
            runtime: TaskRuntime | None = None
            try:
                runtime, mapping = await self._route_observation_mapping(mapping)
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
                    legacy_session_id=predecessor_session_id,
                    legacy_writer_id=predecessor_writer_id,
                )
                if worker is None:
                    continue

                async def _after(
                    bound_workspace: str = workspace,
                    bound_runtime: TaskRuntime = runtime,
                    bound_store: TaskObservationPort = store,
                    bound_legacy_writer_id: str = predecessor_writer_id,
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
        predecessor_session_id = mapping.yoetz_session_id
        predecessor_writer_id = mapping.yoetz_writer_id

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
            route_history: list[LifecycleMapping] = []
            stage = "runtime_route"
            try:
                runtime, mapping = await self._route_observation_mapping(
                    mapping, route_history=route_history
                )
                legacy_writer_routes, route_history_truncated = _observation_writer_routes(
                    runtime, route_history, state_root=self.state_root
                )
                stage = "store_prepare"
                store = self._observation_store(runtime)
                store.grant_consent(workspace, consent.granted_at)
                store.bind_session(workspace, request.envelope.session_commitment)
                (
                    captured_content,
                    replay_content_candidates,
                    content_redacted,
                    content_unavailable,
                ) = await self._capture_content(
                    runtime,
                    store,
                    workspace=workspace,
                    envelope=request.envelope,
                    chunks=request.content_chunks,
                )
                gaps = set(request.envelope.gap_codes)
                if content_redacted:
                    gaps.add(ObservationGapCode.CONTENT_REDACTED.value)
                if content_unavailable:
                    gaps.add(ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value)
                envelope = replace(
                    request.envelope,
                    content_object_refs=tuple(
                        sorted(
                            {
                                *request.envelope.content_object_refs,
                                *(item.object_id for item in captured_content),
                            },
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
                batch = materialize_observation_envelope(
                    envelope,
                    task_id=runtime.task_id,
                    captured_content=captured_content,
                )
                if batch.skip_reason is None and batch.drafts:
                    replay_role_sets: list[tuple[str, ...]] = []
                    # The inverse lost-order case is contentless first, then an
                    # equivalent copy with content. Its immutable core operation
                    # must win over a second operation that would remint the same
                    # core event ids. The late content remains stored but cannot
                    # retroactively strengthen that committed operation.
                    core_batch = materialize_observation_envelope(
                        replace(envelope, content_object_refs=()),
                        task_id=runtime.task_id,
                        captured_content=(),
                    )
                    if core_batch.skip_reason is None and core_batch.drafts:
                        replay_role_sets.append(tuple(item.role for item in core_batch.drafts))
                    # A historical Claude/Cursor post-only false positive was
                    # committed as unpaired evidence before the explicit host
                    # contract existed. Rebuild that role set from the
                    # retained gap itself, even when no captured manifest is
                    # available to supply a replay candidate.
                    legacy_unpaired = _legacy_unpaired_replay_envelope(envelope)
                    if legacy_unpaired is not None:
                        legacy_core_batch = materialize_observation_envelope(
                            legacy_unpaired,
                            task_id=runtime.task_id,
                            captured_content=(),
                        )
                        legacy_roles = tuple(item.role for item in legacy_core_batch.drafts)
                        if (
                            legacy_core_batch.skip_reason is None
                            and legacy_roles
                            and legacy_roles not in replay_role_sets
                        ):
                            replay_role_sets.append(legacy_roles)
                    for candidate in replay_content_candidates:
                        replay_envelope = replace(
                            envelope,
                            content_object_refs=tuple(
                                sorted(
                                    (item.object_id for item in candidate),
                                    key=str.encode,
                                )
                            ),
                        )
                        replay_batch = materialize_observation_envelope(
                            replay_envelope,
                            task_id=runtime.task_id,
                            captured_content=candidate,
                        )
                        roles = tuple(item.role for item in replay_batch.drafts)
                        if (
                            replay_batch.skip_reason is None
                            and roles
                            and roles not in replay_role_sets
                        ):
                            replay_role_sets.append(roles)
                        if legacy_unpaired is not None:
                            legacy_replay_batch = materialize_observation_envelope(
                                replace(
                                    legacy_unpaired,
                                    content_object_refs=replay_envelope.content_object_refs,
                                ),
                                task_id=runtime.task_id,
                                captured_content=candidate,
                            )
                            legacy_replay_roles = tuple(
                                item.role for item in legacy_replay_batch.drafts
                            )
                            if (
                                legacy_replay_batch.skip_reason is None
                                and legacy_replay_roles
                                and legacy_replay_roles not in replay_role_sets
                            ):
                                replay_role_sets.append(legacy_replay_roles)
                    replay_claims: list[
                        tuple[ObservationLogicalIdentityClaim, tuple[str, ...]]
                    ] = []
                    if route_history_truncated:
                        candidate_role_sets = tuple(
                            dict.fromkeys(
                                (
                                    tuple(item.role for item in batch.drafts),
                                    *replay_role_sets,
                                )
                            )
                        )
                        for candidate_mapping_version in (
                            MATERIALIZATION_MAPPING_VERSION,
                            *MATERIALIZATION_LEGACY_MAPPING_VERSIONS,
                        ):
                            for candidate_roles in candidate_role_sets:
                                claim = _load_logical_identity_claim(
                                    store,
                                    workspace=workspace,
                                    logical_identity=observation_claim_identity(
                                        envelope,
                                        candidate_roles,
                                        mapping_version=candidate_mapping_version,
                                    ),
                                )
                                if claim is not None:
                                    replay_claims.append((claim, candidate_roles))
                    stage = "ledger_append"
                    claim = await self._append_materialized(
                        runtime,
                        envelope,
                        batch,
                        legacy_session_id=predecessor_session_id,
                        legacy_writer_id=predecessor_writer_id,
                        legacy_writer_routes=legacy_writer_routes,
                        replay_required=(
                            route_history_truncated
                            and (
                                result.disposition is ObservationIngestDisposition.DUPLICATE
                                or bool(replay_claims)
                            )
                        ),
                        replay_claims=tuple(replay_claims),
                        replay_draft_role_sets=tuple(replay_role_sets),
                    )
                    if claim is not None:
                        (
                            operation_id,
                            materialization_digest,
                            append_result,
                            resolved_mapping_version,
                            resolved_draft_roles,
                        ) = claim
                        current_draft_roles = tuple(item.role for item in batch.drafts)
                        if resolved_draft_roles != current_draft_roles:
                            await self._local(
                                partial(
                                    self.local.note_coverage_gap,
                                    workspace,
                                    ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value,
                                )
                            )
                        # The claim key is role-scoped so the phases of one host
                        # call (pre action, paired result, permission, subagent)
                        # never contend, and the recorded mapping version is the
                        # source-independent materialization one so hook/stream
                        # copies of a phase can union their source masks. Every
                        # hook source (Codex, Claude Code, Cursor) is the hook
                        # bit; only the Codex session stream is the stream bit.
                        stage = "identity_claim"
                        store.record_logical_identity_claim(
                            workspace=workspace,
                            logical_identity=observation_claim_identity(
                                envelope,
                                resolved_draft_roles,
                                mapping_version=resolved_mapping_version,
                            ),
                            materialization_digest=materialization_digest,
                            operation_id=operation_id,
                            source_mask=(
                                2
                                if envelope.source is ObservationSource.CODEX_SESSION_STREAM
                                else 1
                            ),
                            mapping_version=resolved_mapping_version,
                            materialized_at=timestamp_from_datetime(self.clock.now_utc()),
                        )
                        stage = "ledger_append"
                        if append_result is not None:
                            await self._note_frontier_motion(
                                runtime,
                                workspace,
                                codex_session_id,
                                append_result,
                            )
                        correction = materialize_observation_outcome_correction(
                            envelope,
                            task_id=runtime.task_id,
                        )
                        if (
                            append_result is not None
                            and append_result.outcome == "replayed"
                            and correction.skip_reason is None
                            and correction.drafts
                        ):
                            projection = await runtime.ledger.load_projection(
                                runtime.session_id,
                                ProjectionView.CANDIDATE_FINDINGS,
                            )
                            if (
                                projection is None
                                or type(projection.state) is not ProjectionState
                                or projection.lag != 0
                                or projection.rebuild_required
                            ):
                                raise PublicOperationError(
                                    PublicErrorCode.SERVICE_UNAVAILABLE,
                                    "Observation outcome projection is unavailable.",
                                    retryable=True,
                                )
                            if resolved_mapping_version == MATERIALIZATION_MAPPING_VERSION:
                                core_result = next(
                                    (
                                        item.draft.payload
                                        for item in batch.drafts
                                        if type(item.draft.payload) is ResultRecordedPayload
                                    ),
                                    None,
                                )
                                if type(core_result) is not ResultRecordedPayload:
                                    raise PublicOperationError(
                                        PublicErrorCode.SERVICE_UNAVAILABLE,
                                        "Observation outcome projection is unavailable.",
                                        retryable=True,
                                    )
                                core_result_id = core_result.result_id
                                existing_result = projection.state.results.get(core_result_id)
                            else:
                                # A replayed legacy-mapping operation committed
                                # its graph under pre-upgrade record identities
                                # that cannot be re-derived here, and it may be a
                                # legacy hook operation whose result is UNKNOWN.
                                # Its accepted event ids are the exact link to
                                # the committed result, so consult that result
                                # before deciding whether the explicit stream
                                # outcome still needs a correction (#418).
                                accepted_event_ids = {
                                    item.event_id for item in append_result.accepted
                                }
                                committed_results = [
                                    (candidate_id, record)
                                    for candidate_id, record in (projection.state.results.items())
                                    if record.source_event_id in accepted_event_ids
                                ]
                                if len(committed_results) != 1:
                                    raise PublicOperationError(
                                        PublicErrorCode.SERVICE_UNAVAILABLE,
                                        "Observation outcome projection is unavailable.",
                                        retryable=True,
                                    )
                                core_result_id, existing_result = committed_results[0]
                            if existing_result is None or existing_result.payload is None:
                                raise PublicOperationError(
                                    PublicErrorCode.SERVICE_UNAVAILABLE,
                                    "Observation outcome projection is unavailable.",
                                    retryable=True,
                                )
                            existing_payload = existing_result.payload
                            correction_payload = correction.drafts[0].draft.payload
                            if type(correction_payload) is not ResultRecordedPayload:
                                raise PublicOperationError(
                                    PublicErrorCode.SERVICE_UNAVAILABLE,
                                    "Observation outcome correction is unavailable.",
                                    retryable=True,
                                )
                            incoming_fact = (
                                correction_payload.outcome,
                                correction_payload.exit_status,
                            )
                            existing_fact = (
                                existing_payload.outcome,
                                existing_payload.exit_status,
                            )
                            prior_corrections = tuple(
                                record.payload
                                for result_id, record in projection.state.results.items()
                                if result_id != core_result_id
                                and record.payload is not None
                                and record.payload.action_id == existing_payload.action_id
                            )
                            conflict = (
                                existing_payload.outcome is not ResultOutcome.UNKNOWN
                                and existing_fact != incoming_fact
                            ) or any(
                                (item.outcome, item.exit_status) != incoming_fact
                                for item in prior_corrections
                            )
                            should_correct = (
                                existing_payload.outcome is ResultOutcome.UNKNOWN
                                or existing_fact != incoming_fact
                            )
                            if should_correct:
                                if resolved_mapping_version != MATERIALIZATION_MAPPING_VERSION:
                                    # Bind the correction to the exact committed
                                    # legacy action (and its committed event as
                                    # causal parent when the projection still
                                    # exposes it) instead of the current
                                    # canonical action identities.
                                    action_record = projection.state.actions.get(
                                        existing_payload.action_id
                                    )
                                    correction = materialize_observation_outcome_correction(
                                        envelope,
                                        task_id=runtime.task_id,
                                        conflict=conflict,
                                        target_action_id=existing_payload.action_id,
                                        target_action_event_id=(
                                            action_record.source_event_id
                                            if action_record is not None
                                            else None
                                        ),
                                    )
                                elif conflict:
                                    correction = materialize_observation_outcome_correction(
                                        envelope,
                                        task_id=runtime.task_id,
                                        conflict=True,
                                    )
                                corrected = await self._append_materialized(
                                    runtime,
                                    envelope,
                                    correction,
                                    legacy_session_id=predecessor_session_id,
                                    legacy_writer_id=predecessor_writer_id,
                                    legacy_writer_routes=legacy_writer_routes,
                                )
                                if corrected is not None:
                                    (
                                        correction_operation_id,
                                        correction_digest,
                                        correction_result,
                                        correction_mapping_version,
                                        correction_draft_roles,
                                    ) = corrected
                                    store.record_logical_identity_claim(
                                        workspace=workspace,
                                        logical_identity=observation_claim_identity(
                                            envelope,
                                            correction_draft_roles,
                                            mapping_version=correction_mapping_version,
                                        ),
                                        materialization_digest=correction_digest,
                                        operation_id=correction_operation_id,
                                        source_mask=2,
                                        mapping_version=correction_mapping_version,
                                        materialized_at=timestamp_from_datetime(
                                            self.clock.now_utc()
                                        ),
                                    )
                                    if correction_result is not None:
                                        await self._note_frontier_motion(
                                            runtime,
                                            workspace,
                                            codex_session_id,
                                            correction_result,
                                        )

                stage = "verification"
                await self._enqueue_verification(
                    runtime,
                    workspace,
                    store,
                    envelope,
                    codex_session_id=codex_session_id,
                    legacy_session_id=predecessor_session_id,
                    legacy_writer_id=predecessor_writer_id,
                )
                stage = "advice"
                await self._run_advice(
                    workspace,
                    runtime,
                    store,
                    legacy_writer_id=predecessor_writer_id,
                    session_commitment=envelope.session_commitment,
                )
                return result
            except PublicOperationError as exc:
                if exc.retryable and exc.code in {
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
                if exc.code is PublicErrorCode.STORAGE_CORRUPT:
                    if stage == "identity_claim":
                        # A conflicting logical-identity claim poisons one
                        # envelope, not the bundle. ADR-010 scopes the
                        # generation latch to bundle corruption, so reject just
                        # this envelope and keep the session observable.
                        return _reject(ObservationGapCode.DEDUP_CONFLICT.value)
                    self._storage_corrupt_sessions.add(codex_session_id)
                    return _reject(ObservationGapCode.OBSERVATION_STORAGE_CORRUPT.value)
                if (
                    exc.code is PublicErrorCode.SESSION_NOT_FOUND
                    and exc.safe_details.get("reason_code") == "session_superseded"
                ):
                    # Followable retirement is consumed in
                    # `_route_observation_mapping`. A payload that cannot be
                    # followed, a hop cycle, or a rotation after the route
                    # opened is route retirement, not a missing mapping file
                    # and not a ledger content refusal (#577).
                    return _reject(ObservationGapCode.SESSION_SUPERSEDED.value)
                if not exc.retryable:
                    # Validation and identity rejections are terminal by their
                    # public contract. Calling them service_unavailable made a
                    # healthy daemon look down and left the FIFO head immortal
                    # because every drain path retried it (#540). That includes
                    # SESSION_NOT_FOUND without a followable binding and
                    # SESSION_CONFLICT: every route, catalog, and ledger
                    # authority raises both non-retryable, so neither has a
                    # retryable rendering below (#554).
                    return _reject(ObservationGapCode.LEDGER_REJECTED.value)
                if exc.code is PublicErrorCode.VAULT_LOCKED:
                    return _reject(ObservationGapCode.VAULT_LOCKED.value)
                # Any other retryable public failure is transient coordination
                # with no narrower class; the row stays pending under the
                # consecutive-rejection ceiling (#539).
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
                runtime, mapping = await self._route_observation_mapping(
                    mapping,
                    required_capabilities=frozenset({RuntimeCapability.WRITE}),
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

    async def _route_observation_runtime(
        self,
        task_id: str,
        session_id: str,
        *,
        required_capabilities: frozenset[RuntimeCapability] | None = None,
    ) -> TaskRuntime:
        capabilities = (
            frozenset(
                {
                    RuntimeCapability.STRUCTURAL_READ,
                    RuntimeCapability.PAYLOAD_READ,
                    RuntimeCapability.WRITE,
                }
            )
            if required_capabilities is None
            else required_capabilities
        )
        return await self.runtime.route(
            RouteCommand(
                session_id=session_id,
                writer_id=observation_writer_id(task_id, session_id),
                access=RouteAccess.WRITE,
                required_capabilities=capabilities,
            )
        )

    def _persist_successor_mapping(
        self, predecessor: LifecycleMapping, successor: LifecycleMapping
    ) -> None:
        """Cache a route only while its original lifecycle mapping is still current."""

        try:
            with acquire_session_lock(
                predecessor.codex_session_id, _state=self.state_root
            ) as owned:
                if not owned:
                    return
                latest = self.mapping_loader(predecessor.codex_session_id, _state=self.state_root)
                if latest != predecessor:
                    return
                self.mapping_storer(successor, _state=self.state_root)
        except Exception:
            return

    async def _route_observation_mapping(
        self,
        mapping: LifecycleMapping,
        *,
        required_capabilities: frozenset[RuntimeCapability] | None = None,
        route_history: list[LifecycleMapping] | None = None,
    ) -> tuple[TaskRuntime, LifecycleMapping]:
        """Route through the current session, following ``session_superseded`` (#577).

        ``route_history`` is an optional request-local trace of every admitted
        session binding visited while following a retirement chain.  The
        lifecycle mapping file is intentionally rewritten to the latest route,
        so callers that need to replay a session-bound operation must retain
        this bounded in-memory history before the predecessor is forgotten.
        """

        current = mapping
        seen = {mapping.yoetz_session_id}
        last_error: PublicOperationError | None = None
        if route_history is not None:
            route_history.append(current)
        for _ in range(_MAX_SUPERSEDED_ROUTE_HOPS):
            try:
                runtime = await self._route_observation_runtime(
                    current.yoetz_task_id,
                    current.yoetz_session_id,
                    required_capabilities=required_capabilities,
                )
            except PublicOperationError as error:
                last_error = error
                successor = _session_superseded_binding(
                    error, expected_task_id=current.yoetz_task_id
                )
                if successor is None:
                    raise
                session_id, writer_id = successor
                if session_id in seen:
                    raise
                seen.add(session_id)
                predecessor = current
                current = replace(
                    current,
                    yoetz_session_id=session_id,
                    yoetz_writer_id=writer_id,
                )
                if route_history is not None:
                    route_history.append(current)
                self._persist_successor_mapping(predecessor, current)
                continue
            return runtime, current
        if last_error is not None:
            raise last_error
        raise PublicOperationError(
            PublicErrorCode.SESSION_NOT_FOUND,
            "The requested task attachment was not found.",
            retryable=False,
        )

    async def _note_frontier_motion(
        self,
        runtime: TaskRuntime,
        workspace: str,
        codex_session_id: str,
        append_result: AppendResult,
    ) -> None:
        """Bind reconstructed append motion to the routed ledger's current lineage."""

        # A newly accepted append result is the routed head at its commit. A
        # completed-operation replay carries its historical result frontier,
        # so only that recovery path needs a fresh read to distinguish replay
        # from a same-task restore.
        lineage_frontier = append_result.result_frontier
        if append_result.outcome == "replayed":
            lineage_frontier = await runtime.ledger.load_frontier()
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
                lineage_frontier=lineage_frontier,
            )
        )

    async def _append_materialized(
        self,
        runtime: TaskRuntime,
        envelope: ObservationEnvelope,
        batch: MaterializedObservationBatch,
        *,
        legacy_session_id: str | None = None,
        legacy_writer_id: str | None = None,
        legacy_writer_routes: tuple[tuple[str, str], ...] = (),
        replay_required: bool = False,
        replay_claims: tuple[tuple[ObservationLogicalIdentityClaim, tuple[str, ...]], ...] = (),
        replay_draft_role_sets: tuple[tuple[str, ...], ...] = (),
    ) -> tuple[str, str, AppendResult | None, str, tuple[str, ...]] | None:
        writer_id = runtime.writer_id
        if writer_id is None:
            return None
        logical_identity = canonical_logical_identity(envelope)
        draft_roles = tuple(item.role for item in batch.drafts)
        candidate_role_sets = tuple(dict.fromkeys((draft_roles, *replay_draft_role_sets)))
        writer_routes = [(runtime.session_id, writer_id)]
        for route in legacy_writer_routes:
            if route not in writer_routes:
                writer_routes.append(route)
        if legacy_writer_id is not None and legacy_writer_id != writer_id:
            route = (legacy_session_id or runtime.session_id, legacy_writer_id)
            if route not in writer_routes:
                writer_routes.append(route)
        for (claim_digest, claim_operation_id, claim_mapping_version), claim_roles in replay_claims:
            if self._stable_operation_id(claim_digest) != claim_operation_id:
                raise PublicOperationError(
                    PublicErrorCode.STORAGE_CORRUPT,
                    "Observation logical identity claim is invalid.",
                    retryable=False,
                )
            existing = await runtime.ledger.lookup_task_operation(writer_id, claim_operation_id)
            if existing is None:
                continue
            if (
                getattr(existing, "operation_id", claim_operation_id) != claim_operation_id
                or existing.request_digest != claim_digest
            ):
                raise PublicOperationError(
                    PublicErrorCode.STORAGE_CORRUPT,
                    "Observation logical identity claim does not match its operation.",
                    retryable=False,
                )
            return (
                claim_operation_id,
                claim_digest,
                _append_result_from_committed(existing),
                claim_mapping_version,
                claim_roles,
            )
        # Check the stable operation identity before staging payloads. A replay
        # after "ledger committed, local outbox not acknowledged" must not
        # create orphan encrypted objects on every retry.
        #
        # The current mapping's operation digest is task-scoped, matching the
        # stable event ids it commits, so the committed operation is resolved
        # task-wide: a workflow reattach in the same host session rotates the
        # routed Yoetz session and observation writer, and the repeat must
        # still find the operation the predecessor session committed instead
        # of reminting its event ids into a ledger that already holds them
        # (#560). A hit whose request digest differs is a genuine conflicting
        # reuse of the operation identity and fails closed.
        for candidate_roles in candidate_role_sets:
            candidate_digest = observation_operation_digest(
                task_id=runtime.task_id,
                logical_identity=logical_identity,
                draft_roles=candidate_roles,
                mapping_version=MATERIALIZATION_MAPPING_VERSION,
            )
            candidate_operation_id = self._stable_operation_id(candidate_digest)
            existing = await runtime.ledger.lookup_task_operation(writer_id, candidate_operation_id)
            if existing is None:
                continue
            if existing.request_digest != candidate_digest:
                raise PublicOperationError(
                    PublicErrorCode.IDEMPOTENCY_CONFLICT,
                    "Observation operation identity is already committed with other content.",
                    retryable=False,
                )
            return (
                candidate_operation_id,
                candidate_digest,
                _append_result_from_committed(existing),
                MATERIALIZATION_MAPPING_VERSION,
                candidate_roles,
            )
        # Legacy mapping versions bound the digest to the routed session and
        # writer (1.2-1.4).  Mapping 1.5 was task-scoped but used the older
        # identity shape; search it through the same task-wide lookup with the
        # legacy identity before staging so pre-1.6 rows remain replayable.
        for mapping_version in MATERIALIZATION_LEGACY_MAPPING_VERSIONS:
            legacy_identities = (
                logical_identity,
                canonical_logical_identity(envelope, mapping_version=mapping_version),
            )
            # Preserve the established writer/role lookup order for the
            # session-bound mappings. Try every role under the current
            # identity before falling back to the historical identity; this
            # keeps replay probes deterministic while still finding rows
            # written before the source/session identity was widened.
            for legacy_identity in dict.fromkeys(legacy_identities):
                candidate_writer_routes = (
                    writer_routes
                    if mapping_version in SESSION_BOUND_MAPPING_VERSIONS
                    else ((runtime.session_id, writer_id),)
                )
                for candidate_session_id, candidate_writer_id in candidate_writer_routes:
                    for candidate_roles in candidate_role_sets:
                        digest_kwargs: dict[str, str] = {}
                        if mapping_version in SESSION_BOUND_MAPPING_VERSIONS:
                            digest_kwargs = {
                                "session_id": candidate_session_id,
                                "writer_id": candidate_writer_id,
                            }
                        candidate_digest = observation_operation_digest(
                            task_id=runtime.task_id,
                            logical_identity=legacy_identity,
                            draft_roles=candidate_roles,
                            mapping_version=mapping_version,
                            **digest_kwargs,
                        )
                        candidate_operation_id = self._stable_operation_id(candidate_digest)
                        existing = (
                            await runtime.ledger.lookup_operation(
                                candidate_writer_id, candidate_operation_id
                            )
                            if mapping_version in SESSION_BOUND_MAPPING_VERSIONS
                            else await runtime.ledger.lookup_task_operation(
                                writer_id, candidate_operation_id
                            )
                        )
                        if existing is not None:
                            if existing.request_digest != candidate_digest:
                                raise PublicOperationError(
                                    PublicErrorCode.IDEMPOTENCY_CONFLICT,
                                    "Observation operation identity is already committed with other content.",
                                    retryable=False,
                                )
                            return (
                                candidate_operation_id,
                                candidate_digest,
                                _append_result_from_committed(existing),
                                mapping_version,
                                candidate_roles,
                            )

        if replay_required:
            raise PublicOperationError(
                PublicErrorCode.SESSION_NOT_FOUND,
                "The retained observation replay route is incomplete.",
                retryable=False,
                safe_details={
                    "reason_code": "session_superseded",
                    "task_id": runtime.task_id,
                    "session_id": runtime.session_id,
                    "writer_id": writer_id,
                },
            )

        digest = observation_operation_digest(
            task_id=runtime.task_id,
            logical_identity=logical_identity,
            draft_roles=draft_roles,
            mapping_version=MATERIALIZATION_MAPPING_VERSION,
        )
        operation_id = self._stable_operation_id(digest)

        author = observation_author()
        refs: list[ObjectRef] = []
        entries: list[AppendEntry] = []
        staged_objects: list[StagedObject] = []
        try:
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
                staged_objects.append(staged)
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
        except BaseException:
            await abandon_preappend_objects(
                runtime.objects,
                tuple(staged_objects),
                component="application.observation_coordinator",
                operation="observation_object_abandon_failed",
                request_id=operation_id,
            )
            raise
        try:
            append_result = await run_prepared_append(runtime.ledger, mutation)
        except PreSubmissionCancelled:
            await abandon_preappend_objects(
                runtime.objects,
                tuple(staged_objects),
                component="application.observation_coordinator",
                operation="observation_object_abandon_failed",
                request_id=operation_id,
            )
            raise
        return (
            operation_id,
            digest,
            append_result,
            MATERIALIZATION_MAPPING_VERSION,
            draft_roles,
        )

    async def _append_inspection_snapshot(
        self,
        runtime: TaskRuntime,
        snapshot: ObservationInspectionSnapshot,
    ) -> AppendResult | None:
        """Append one idempotent harness-owned inspection evidence batch."""

        writer_id = runtime.writer_id
        if writer_id is None:
            return None
        batch = materialize_observation_inspection_snapshot(snapshot, task_id=runtime.task_id)
        if batch.skip_reason is not None or not batch.drafts:
            return None
        operation_digest = canonical_digest(
            JsonObject(
                {
                    "format": "yoetz.observation-inspection-ledger-materialization/1",
                    "snapshot_id": snapshot.snapshot_id,
                    "subject_state_digest": snapshot.subject_state_digest,
                    "changed_paths_digest": snapshot.changed_paths_digest,
                    "facts_object_id": snapshot.facts_object_id,
                    "facts_content_digest": snapshot.facts_content_digest,
                    "excerpt_object_id": snapshot.excerpt_object_id,
                    "excerpt_content_digest": snapshot.excerpt_content_digest,
                    "task_id": runtime.task_id,
                    "session_id": runtime.session_id,
                    "writer_id": writer_id,
                }
            )
        )
        operation_id = self._stable_operation_id(operation_digest)
        existing = await runtime.ledger.lookup_operation(writer_id, operation_id)
        if existing is not None:
            return _append_result_from_committed(existing)

        author = observation_author()
        refs: list[ObjectRef] = []
        entries: list[AppendEntry] = []
        staged_objects: list[StagedObject] = []
        try:
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
                staged_objects.append(staged)
                if staged.commitment != commitment:
                    raise PublicOperationError(
                        PublicErrorCode.STORAGE_CORRUPT,
                        "Observation inspection payload commitment mismatch.",
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
        except BaseException:
            await abandon_preappend_objects(
                runtime.objects,
                tuple(staged_objects),
                component="application.observation_coordinator",
                operation="observation_object_abandon_failed",
                request_id=operation_id,
            )
            raise
        try:
            return await run_prepared_append(runtime.ledger, mutation)
        except PreSubmissionCancelled:
            await abandon_preappend_objects(
                runtime.objects,
                tuple(staged_objects),
                component="application.observation_coordinator",
                operation="observation_object_abandon_failed",
                request_id=operation_id,
            )
            raise

    async def _materialize_approved_check(
        self,
        runtime: TaskRuntime,
        completed: CompletedApprovedCheck,
        *,
        legacy_session_id: str | None = None,
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
                        "session_id": legacy_session_id or runtime.session_id,
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
        staged_objects: list[StagedObject] = []
        try:
            staged_receipt = await runtime.objects.stage(
                ObjectSource(data=receipt_bytes, declared_size=len(receipt_bytes)), receipt_metadata
            )
            staged_objects.append(staged_receipt)
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
                    EventSchema("evidence_recorded", EVIDENCE_TYPED_SCHEMA_VERSION),
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
                staged_objects.append(staged)
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
        except BaseException:
            await abandon_preappend_objects(
                runtime.objects,
                tuple(staged_objects),
                component="application.observation_coordinator",
                operation="observation_object_abandon_failed",
                request_id=operation_id,
            )
            raise
        try:
            await run_prepared_append(runtime.ledger, mutation)
        except PreSubmissionCancelled:
            await abandon_preappend_objects(
                runtime.objects,
                tuple(staged_objects),
                component="application.observation_coordinator",
                operation="observation_object_abandon_failed",
                request_id=operation_id,
            )
            raise

    async def _capture_content(
        self,
        runtime: TaskRuntime,
        store: TaskObservationPort,
        *,
        workspace: str,
        envelope: ObservationEnvelope,
        chunks: tuple[ObservationContentChunk, ...],
    ) -> tuple[
        tuple[ObservationContentManifest, ...],
        tuple[tuple[ObservationContentManifest, ...], ...],
        bool,
        bool,
    ]:
        """Assemble usable content and replay-only manifest candidates.

        Authenticated, readable objects may strengthen the new materialization.
        Stored manifest identities also reconstruct prior operation role sets,
        but an unreadable object is replay-only and never grants captured
        coverage to a new append.
        """

        logical_identity = canonical_logical_identity(envelope)
        content_identity = observation_content_identity(envelope)
        legacy_logical_identities = tuple(
            canonical_logical_identity(envelope, mapping_version=mapping_version)
            for mapping_version in MATERIALIZATION_LEGACY_MAPPING_VERSIONS
        )
        legacy_content_identities = tuple(
            observation_content_identity(envelope, mapping_version=mapping_version)
            for mapping_version in MATERIALIZATION_LEGACY_MAPPING_VERSIONS
        )
        manifests: dict[str, ObservationContentManifest] = {}
        replay_candidates: list[tuple[ObservationContentManifest, ...]] = []
        any_redacted = False
        any_unavailable = False

        async def verify_manifest(
            loaded: ObservationContentManifest,
        ) -> tuple[ObservationContentManifest, bool, bool]:
            """Rebind manifest metadata to the authenticated captured object."""

            if loaded.envelope_digest is None:
                return loaded, False, False
            try:
                ref = await runtime.objects.resolve_verified(
                    loaded.object_id, loaded.envelope_digest
                )
                if (
                    ref.metadata.kind is not ObjectKind.CAPTURED_CONTENT
                    or ref.metadata.media_type != "application/vnd.yoetz.observation-content+json"
                ):
                    return loaded, False, False
                raw = bytearray()
                async for chunk in runtime.objects.open_verified(ref):
                    raw.extend(chunk)
                    if len(raw) > ref.plaintext_size:
                        return loaded, False, False
                material = bytes(raw)
                if len(material) != ref.plaintext_size:
                    return loaded, False, False
                parsed = strict_json_parse(material)
                expected_keys = {
                    "format",
                    "content_kind",
                    "correlation_identity",
                    "source_commitment",
                    "media_type",
                    "part_index",
                    "part_count",
                    "redacted",
                    "content_b64",
                }
                if (
                    not isinstance(parsed, Mapping)
                    or set(parsed) != expected_keys
                    or canonical_encode(parsed) != material
                    or parsed.get("format") != "yoetz.observation-content/1"
                    or type(parsed.get("content_b64")) is not str
                    or type(parsed.get("redacted")) is not bool
                ):
                    return loaded, False, False
                content = base64.b64decode(cast(str, parsed["content_b64"]), validate=True)
                verified = ObservationContentManifest(
                    object_id=ref.object_id,
                    envelope_digest=ref.envelope_digest,
                    content_kind=ObservationContentKind(cast(str, parsed["content_kind"])),
                    part_index=cast(int, parsed["part_index"]),
                    part_count=cast(int, parsed["part_count"]),
                    redacted=cast(bool, parsed["redacted"]),
                    content_digest="sha256:" + hashlib.sha256(content).hexdigest(),
                    content_bytes=len(content),
                    correlation_identity=cast(str, parsed["correlation_identity"]),
                    source_commitment=cast(str, parsed["source_commitment"]),
                )
                return verified, True, verified == loaded
            except Exception:
                return loaded, False, False

        async def note_unavailable() -> None:
            await self._local(
                partial(
                    self.local.note_coverage_gap,
                    workspace,
                    ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value,
                )
            )

        def manifests_complete(candidate: tuple[ObservationContentManifest, ...]) -> bool:
            parts: dict[
                tuple[str, ObservationContentKind],
                list[ObservationContentManifest],
            ] = {}
            for item in candidate:
                if item.correlation_identity is None or item.source_commitment is None:
                    return False
                key = (item.correlation_identity, item.content_kind)
                parts.setdefault(key, []).append(item)
            return bool(parts) and all(
                len({item.part_count for item in group}) == 1
                and len({item.source_commitment for item in group}) == 1
                and len({item.part_index for item in group}) == len(group)
                and {item.part_index for item in group} == set(range(group[0].part_count))
                for group in parts.values()
            )

        def legacy_source_group(item: ObservationContentManifest) -> str:
            correlation = item.correlation_identity
            if correlation is None or ":" not in correlation:
                return item.object_id
            return correlation.rsplit(":", 1)[0]

        # Content chunks are intentionally ephemeral at the hook boundary. If
        # the ledger commit wins a client-side timeout, the durable outbox retry
        # carries only the structural envelope. Recover the manifests that the
        # first attempt already bound to this normalized phase so equivalent
        # hook/stream copies reconstruct the same original role set
        # and find the committed operation (#539).
        recovered_current = store.content_manifests_for_logical_identity(
            workspace=workspace,
            logical_identity=content_identity,
        )
        # Upgrade recovery for manifests written before issue #539 added the
        # phase-scoped content identity. The matching source group may supply
        # usable content; every legacy source group is also kept separately as
        # lookup-only input, because an equivalent post-upgrade stream copy has
        # a different source identity but the same committed operation roles.
        recovered_legacy_same_source: list[ObservationContentManifest] = []
        recovered_legacy_all: list[ObservationContentManifest] = []
        # The raw canonical identity is the pre-#539 manifest key. Keep both
        # that shape and the phase-scoped identities used by newer writers;
        # the mapping-version variants cover rows written before #607 widened
        # canonical identity with source lane and generation.
        recovery_identities = (
            content_identity,
            logical_identity,
            *legacy_content_identities,
            *legacy_logical_identities,
        )
        for candidate_identity in dict.fromkeys(recovery_identities):
            recovered_legacy_same_source.extend(
                store.content_manifests_for_logical_identity(
                    workspace=workspace,
                    logical_identity=candidate_identity,
                    correlation_identity_prefix=f"{envelope.source_identity}:",
                )
            )
            recovered_legacy_all.extend(
                store.content_manifests_for_logical_identity(
                    workspace=workspace,
                    logical_identity=candidate_identity,
                )
            )
        recovered_legacy_same_source_tuple = tuple(recovered_legacy_same_source)
        legacy_groups: dict[str, list[ObservationContentManifest]] = {}
        for item in recovered_legacy_all:
            legacy_groups.setdefault(legacy_source_group(item), []).append(item)
        recovered_sets: list[tuple[ObservationContentManifest, ...]] = []
        for candidate in (
            recovered_current,
            recovered_legacy_same_source_tuple,
            *(
                tuple(sorted(group, key=lambda item: item.object_id.encode()))
                for group in legacy_groups.values()
            ),
        ):
            if candidate and candidate not in recovered_sets:
                recovered_sets.append(candidate)
        # Keep object identity here: the loop below deliberately admits
        # captured roles only for the primary same-phase/source candidate.
        # Re-wrapping a list as a tuple would make ``candidate is
        # primary_recovered`` false and silently turn every historical retry
        # into replay-only evidence.
        primary_recovered = recovered_current or recovered_legacy_same_source_tuple
        for candidate in recovered_sets:
            complete = manifests_complete(candidate)
            if not complete:
                any_unavailable = True
                await note_unavailable()
            raw_candidate = tuple(sorted(candidate, key=lambda item: item.object_id.encode()))
            verified_candidate: list[ObservationContentManifest] = []
            verified_rows: list[
                tuple[ObservationContentManifest, ObservationContentManifest, bool, bool]
            ] = []
            for loaded in candidate:
                verified, readable, exact = await verify_manifest(loaded)
                verified_candidate.append(verified if readable else loaded)
                verified_rows.append((loaded, verified, readable, exact))
                if not exact:
                    any_unavailable = True
                    await note_unavailable()
            candidate_usable = complete and all(
                readable and exact and loaded.content_digest is not None
                for loaded, _verified, readable, exact in verified_rows
            )
            if candidate is primary_recovered and candidate_usable:
                for loaded, verified, readable, exact in verified_rows:
                    # A legacy-unbound row was intentionally excluded by the
                    # materializer that may already have committed it. Reading
                    # its object now must not silently add a captured role.
                    assert readable and exact and loaded.content_digest is not None
                    manifests[verified.object_id] = verified
                    any_redacted = any_redacted or verified.redacted
            normalized_verified = tuple(
                sorted(verified_candidate, key=lambda item: item.object_id.encode())
            )
            for replay_candidate in (raw_candidate, normalized_verified):
                if replay_candidate and replay_candidate not in replay_candidates:
                    replay_candidates.append(replay_candidate)

        # Once a phase has durable manifests its materialized role set is
        # frozen. Equivalent hook/stream copies may carry fresh ephemeral
        # chunks, but adding those roles after a commit would change the stable
        # operation identity. The existing set remains authoritative.
        freeze_roles = bool(primary_recovered)
        for chunk in chunks:
            existing = store.content_manifest_object_id(
                workspace=workspace,
                logical_identity=content_identity,
                chunk=chunk,
            )
            if existing is not None:
                if existing in manifests:
                    continue
                loaded = store.load_content_manifest(existing)
                if loaded is not None and loaded.content_digest is not None:
                    verified, readable, exact = await verify_manifest(loaded)
                    if not exact:
                        any_unavailable = True
                        await note_unavailable()
                    if readable and exact:
                        manifests[verified.object_id] = verified
                        any_redacted = any_redacted or verified.redacted
                    continue
            if freeze_roles:
                any_unavailable = True
                await note_unavailable()
                continue
            scan = prepare_persisted_plaintext(chunk.content)
            if not scan.persist:
                any_redacted = any_redacted or scan.redacted
                any_unavailable = True
                await self._local(
                    partial(
                        self.local.note_coverage_gap,
                        workspace,
                        ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value,
                    )
                )
                continue
            safe_content = scan.content
            detected = scan.redacted
            any_redacted = any_redacted or chunk.redacted or detected
            stored_chunk = replace(
                chunk,
                content=safe_content,
                redacted=chunk.redacted or detected,
            )
            manifest_bytes = canonical_encode(
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
            if existing is None:
                metadata = ObjectMetadata(
                    ObjectKind.CAPTURED_CONTENT,
                    "application/vnd.yoetz.observation-content+json",
                    runtime.task_id,
                    self.clock.now_utc(),
                )
                staged = await runtime.objects.stage(
                    ObjectSource(data=manifest_bytes, declared_size=len(manifest_bytes)),
                    metadata,
                )
                ref = await runtime.objects.finalize(staged)
            else:
                loaded = store.load_content_manifest(existing)
                if loaded is None or loaded.envelope_digest is None:
                    any_unavailable = True
                    await self._local(
                        partial(
                            self.local.note_coverage_gap,
                            workspace,
                            ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value,
                        )
                    )
                    continue
                try:
                    ref = await runtime.objects.resolve_verified(
                        loaded.object_id, loaded.envelope_digest
                    )
                except Exception:
                    any_unavailable = True
                    await self._local(
                        partial(
                            self.local.note_coverage_gap,
                            workspace,
                            ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value,
                        )
                    )
                    continue
            store.record_content_manifest(
                workspace=workspace,
                logical_identity=content_identity,
                chunk=stored_chunk,
                ref=ref,
                content_digest="sha256:" + hashlib.sha256(safe_content).hexdigest(),
                content_bytes=len(safe_content),
                recorded_at=timestamp_from_datetime(self.clock.now_utc()),
            )
            if stored_chunk.content_kind is ObservationContentKind.WORKSPACE_LOCATOR:
                store.bind_workspace_locator(
                    workspace=workspace,
                    locator_ref=ref,
                    bound_at=timestamp_from_datetime(self.clock.now_utc()),
                )
            loaded = store.load_content_manifest(ref.object_id)
            if loaded is None or loaded.content_digest is None:
                any_unavailable = True
                await self._local(
                    partial(
                        self.local.note_coverage_gap,
                        workspace,
                        ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value,
                    )
                )
                continue
            manifests[loaded.object_id] = loaded

        usable_manifests = tuple(
            sorted(manifests.values(), key=lambda item: item.object_id.encode())
        )
        if usable_manifests and not manifests_complete(usable_manifests):
            # The first ingest can itself carry only a prefix of a multipart
            # value. Persisting that prefix helps a later exact retry, but it
            # cannot become immutable captured evidence on its own.
            usable_manifests = ()
            any_unavailable = True
            await note_unavailable()

        # Durable refs are assertions from the incoming envelope, not authority
        # to borrow a manifest from another phase. Correct phase-bound refs were
        # already recovered above; every other ref weakens coverage.
        for ref in envelope.content_object_refs:
            if not ref.startswith("obj_") or any(
                item.object_id == ref for item in usable_manifests
            ):
                continue
            any_unavailable = True
            await note_unavailable()
        return (
            usable_manifests,
            tuple(replay_candidates),
            any_redacted,
            any_unavailable,
        )

    async def _enqueue_verification(
        self,
        runtime: TaskRuntime,
        workspace: str,
        store: TaskObservationPort,
        envelope: ObservationEnvelope,
        *,
        codex_session_id: str | None = None,
        legacy_session_id: str | None = None,
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
            codex_session_id=codex_session_id,
            legacy_session_id=legacy_session_id,
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
                    legacy_session_id=legacy_session_id,
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
        codex_session_id: str | None = None,
        legacy_session_id: str | None = None,
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
            return await self._persist_approved_check_output(
                runtime, store, workspace, job, content
            )

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
                runtime,
                completed,
                legacy_session_id=legacy_session_id,
                legacy_writer_id=legacy_writer_id,
            ),
        )
        worker.enqueue_if_changed(
            workspace=workspace,
            policy_digest=policy.raw_digest,
            approvals=policy.checks,
            previous_subject_state_digest=store.latest_verification_subject_digest(workspace),
            subject_state_digest=current_digest,
        )
        # Persist inspection snapshot + session route when their durable helpers exist.
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
        inspect_loader = getattr(store, "load_inspection_snapshot", None)
        inspection_snapshot: ObservationInspectionSnapshot | None = None
        if callable(inspect_loader):
            inspection_snapshot = cast(
                ObservationInspectionSnapshot | None,
                inspect_loader(
                    workspace=workspace,
                    yoetz_session_id=runtime.session_id,
                    subject_state_digest=current_digest,
                ),
            )
        if inspection_snapshot is None and callable(inspect_recorder):
            relative_paths: tuple[str, ...] = ()
            changed_digest = current_digest
            facts_ref: ObjectRef | None = None
            facts_content_digest: str | None = None
            facts_content_bytes: int | None = None
            excerpt_ref: ObjectRef | None = None
            excerpt_content_digest: str | None = None
            excerpt_content_bytes: int | None = None
            excerpt_redacted = False
            excerpt_truncated = False
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
                    facts_content_digest = "sha256:" + hashlib.sha256(fact_bytes).hexdigest()
                    facts_content_bytes = len(fact_bytes)
                if orchestration.inspect is not None and orchestration.inspect.artifacts:
                    (
                        excerpt_bytes,
                        excerpt_unavailable,
                        excerpt_redacted,
                        excerpt_truncated,
                    ) = build_inspection_excerpt_manifest(orchestration.inspect.artifacts)
                    if excerpt_unavailable:
                        await self._local(
                            partial(
                                self.local.note_coverage_gap,
                                workspace,
                                ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value,
                            )
                        )
                    elif excerpt_bytes is not None:
                        excerpt_ref = await self._encrypt_captured_content(runtime, excerpt_bytes)
                        excerpt_content_digest = (
                            "sha256:" + hashlib.sha256(excerpt_bytes).hexdigest()
                        )
                        excerpt_content_bytes = len(excerpt_bytes)
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
                facts_ref=facts_ref,
                facts_content_digest=facts_content_digest,
                facts_content_bytes=facts_content_bytes,
                excerpt_ref=excerpt_ref,
                excerpt_content_digest=excerpt_content_digest,
                excerpt_content_bytes=excerpt_content_bytes,
                excerpt_redacted=excerpt_redacted,
                excerpt_truncated=excerpt_truncated,
                recorded_at=now,
            )
            if callable(inspect_loader):
                inspection_snapshot = cast(
                    ObservationInspectionSnapshot | None,
                    inspect_loader(
                        workspace=workspace,
                        yoetz_session_id=runtime.session_id,
                        subject_state_digest=current_digest,
                    ),
                )
        if inspection_snapshot is None:
            await self._local(
                partial(
                    self.local.note_coverage_gap,
                    workspace,
                    ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value,
                )
            )
        if inspection_snapshot is not None:
            inspection_result = await self._append_inspection_snapshot(runtime, inspection_snapshot)
            if inspection_result is not None and codex_session_id is not None:
                await self._local(
                    partial(
                        self.local.note_frontier_motion,
                        workspace,
                        codex_session_id,
                        from_sequence=inspection_result.subject_frontier.sequence,
                        to_sequence=inspection_result.result_frontier.sequence,
                        head_digest=inspection_result.result_frontier.head_digest,
                        observation_record_count=len(inspection_result.accepted),
                        task_id=runtime.task_id,
                    )
                )
        return worker

    async def _rebuild_verification_worker(
        self,
        runtime: TaskRuntime,
        workspace: str,
        store: TaskObservationPort,
        *,
        legacy_session_id: str | None = None,
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
            return await self._persist_approved_check_output(
                runtime, store, workspace, job, content
            )

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
                runtime,
                completed,
                legacy_session_id=legacy_session_id,
                legacy_writer_id=legacy_writer_id,
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

    async def _persist_approved_check_output(
        self,
        runtime: TaskRuntime,
        store: TaskObservationPort,
        workspace: str,
        job: ObservationVerificationJob,
        content: bytes,
    ) -> str | None:
        """Encrypt approved-check output only after the shared fail-closed scan."""

        scan = prepare_persisted_plaintext(content)
        if not scan.persist:
            await self._local(
                partial(
                    self.local.note_coverage_gap,
                    workspace,
                    ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value,
                )
            )
            return None
        chunk = ObservationContentChunk(
            content_kind=ObservationContentKind.APPROVED_CHECK_OUTPUT,
            correlation_identity=f"check:{job.job_id}",
            source_commitment=f"hmac-sha256:{'0' * 64}",
            media_type="text/plain",
            part_index=0,
            part_count=1,
            content=scan.content,
            redacted=scan.redacted,
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
                    "redacted": scan.redacted,
                    "content_b64": base64.b64encode(scan.content).decode("ascii"),
                }
            )
        )
        ref = await self._encrypt_captured_content(runtime, manifest)
        store.record_content_manifest(
            workspace=workspace,
            logical_identity=f"verification:{job.job_id}",
            chunk=chunk,
            ref=ref,
            content_digest="sha256:" + hashlib.sha256(scan.content).hexdigest(),
            content_bytes=len(scan.content),
            recorded_at=timestamp_from_datetime(self.clock.now_utc()),
        )
        return ref.object_id

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
                    store=store,
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
        self,
        task_id: str,
        envelope: ObservationEnvelope,
        store: TaskObservationPort | None,
    ) -> tuple[str, ...]:
        key = (task_id, canonical_digest(observation_envelope_to_json(envelope)))
        cached = self._advice_event_ref_cache.pop(key, None)
        if cached is not None:
            self._advice_event_ref_cache[key] = cached
            return cached
        manifest_loader = getattr(store, "load_content_manifest", None)
        recovered: list[ObservationContentManifest] = []
        if callable(manifest_loader):
            for ref in envelope.content_object_refs:
                manifest = cast(ObservationContentManifest | None, manifest_loader(ref))
                if manifest is not None:
                    recovered.append(manifest)
        captured_content = tuple(recovered)
        batch = (
            materialize_observation_envelope(
                envelope,
                task_id=task_id,
                captured_content=captured_content,
            )
            if captured_content
            else materialize_observation_envelope(envelope, task_id=task_id)
        )
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
        store: TaskObservationPort | None = None,
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
                self._materialized_event_refs(runtime.task_id, envelope, store)
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
        # The finding event ids and this digest are task-scoped, so the
        # committed operation is resolved task-wide: after a workflow reattach
        # the routed writer is new, but the predecessor session's finding
        # events are already in the ledger (#560).
        existing = await runtime.ledger.lookup_task_operation(runtime.writer_id, operation_id)
        if existing is not None:
            if existing.request_digest != request_digest_value:
                raise PublicOperationError(
                    PublicErrorCode.IDEMPOTENCY_CONFLICT,
                    "Observation advice identity is already committed with other content.",
                    retryable=False,
                )
            return
        if legacy_writer_id is not None and legacy_writer_id != runtime.writer_id:
            legacy_existing = await runtime.ledger.lookup_operation(legacy_writer_id, operation_id)
            if legacy_existing is not None:
                if legacy_existing.request_digest != request_digest_value:
                    raise PublicOperationError(
                        PublicErrorCode.IDEMPOTENCY_CONFLICT,
                        "Observation advice identity is already committed with other content.",
                        retryable=False,
                    )
                return
        entries: list[AppendEntry] = []
        object_refs: list[ObjectRef] = []
        staged_objects: list[StagedObject] = []
        envelope_coverage = coverage_for_channel(PublicationChannel.ENGINE_DERIVED)
        try:
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
                schema = EventSchema("finding_recorded", "1.1.0")
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
                staged_objects.append(staged)
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
            mutation = PreparedMutation(
                command.writer_id,
                command.operation_id,
                command.request_digest,
                command.expected_frontier,
                tuple(object_refs),
                command,
            )
        except BaseException:
            await abandon_preappend_objects(
                runtime.objects,
                tuple(staged_objects),
                component="application.observation_coordinator",
                operation="observation_object_abandon_failed",
                request_id=operation_id,
            )
            raise
        try:
            await run_prepared_append(runtime.ledger, mutation)
        except PreSubmissionCancelled:
            await abandon_preappend_objects(
                runtime.objects,
                tuple(staged_objects),
                component="application.observation_coordinator",
                operation="observation_object_abandon_failed",
                request_id=operation_id,
            )
            raise
