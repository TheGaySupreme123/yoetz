"""Exercise the ordinary native-host capture path through a real task bundle.

These tests deliberately start at the host normalizers.  A fake control client provides the
in-process service transport, while the coordinator, SQLite ledger, encrypted object store, and
semantic case builder remain production implementations.  The marker is supplied by the hook
payload and is asserted in the prepared packet, so a manually fabricated evidence row cannot make
the test pass.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import apsw
import pytest
from tests.integration.objects.test_envelope_and_encrypted_files import (
    MacKeyForObjectTest,
    SecretMemoryForObjectTest,
    WrapKeyForObjectTest,
)

import integration.service.test_semantic_non_dispatch as semantic_non_dispatch
from yoetz.adapters.integrations.codex_lifecycle import (
    LifecycleMapping,
    load_mapping,
    store_mapping,
)
from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.adapters.objects.encrypted_files import EncryptedFilesObjectStore
from yoetz.adapters.sqlite import connection as sqlite_connection
from yoetz.adapters.sqlite.migrations import initialize_bundle
from yoetz.adapters.sqlite.observation import SqliteObservationStore
from yoetz.adapters.sqlite.repository import SqliteLedger
from yoetz.application.check import FinalSemanticEvaluation
from yoetz.application.egress import PrivacyCoordinator
from yoetz.application.observation_coordinator import ObservationCoordinator
from yoetz.application.observation_materialize import (
    materialize_observation_envelope,
    observation_content_identity,
)
from yoetz.application.semantic_case import (
    build_semantic_case,
    semantic_case_to_prepared_payload,
)
from yoetz.application.semantic_content import resolve_captured_semantic_content
from yoetz.cli.observe_hooks import handle_claude_observe, handle_cursor_observe, handle_observe
from yoetz.domain.observation import (
    OBSERVATION_CONTENT_CAPTURE_PENDING_REASON,
    ObservationCaptureTicket,
    ObservationContentKind,
    ObservationContentManifest,
    ObservationGapCode,
    ObservationIngestDisposition,
    ObservationIngestRequest,
    ObservationIngestResult,
    ObservationSource,
    observation_capture_part_descriptors,
    observation_capture_ticket_id,
    observation_ingest_request_from_json,
    observation_ingest_result_to_json,
)
from yoetz.domain.observation_profiles import (
    CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID,
    CURSOR_ORDINARY_OBSERVATION_PROFILE_ID,
)
from yoetz.domain.privacy import ProviderBinding, ReviewContextProfile, ReviewSelectionPolicy
from yoetz.domain.values import JsonValue as DomainJsonValue
from yoetz.domain.values import Timestamp, evidence_id
from yoetz.ports.clock import ClockPort
from yoetz.ports.ids import IdPort
from yoetz.ports.importer import ImporterPort
from yoetz.ports.keys import BundleKeys
from yoetz.ports.ledger import CheckPhase, FrozenCase
from yoetz.ports.objects import (
    ObjectKind,
    ObjectMetadata,
    ObjectRootSnapshot,
    ObjectSource,
    ObjectStorePort,
)
from yoetz.ports.observation import TaskObservationPort
from yoetz.ports.runtime import (
    BundleRuntimePort,
    OwnershipFence,
    RouteCommand,
    RuntimeCapability,
    StartCompletionEvidence,
    TaskRuntime,
)
from yoetz.ports.start_catalog import StartCatalogPort
from yoetz.protocol.canonical import JsonValue as CanonicalJsonValue
from yoetz.protocol.canonical import canonical_encode
from yoetz.protocol.coverage import (
    ArtifactObservation,
    AuthorshipAssurance,
    EvidenceImmutability,
    PublicationChannel,
)
from yoetz.protocol.ids import PREFIX_BY_KIND, IdKind
from yoetz.protocol.models import SemanticStatus

_NOW = datetime(2026, 9, 5, 17, 0, tzinfo=UTC)
_ZERO_DIGEST = "sha256:" + "0" * 64
_OWNER_NONCE = "native-capture-test-nonce"
_CAPTURE_MEDIA_TYPE = "application/vnd.yoetz.observation-content+json"


class _Clock(ClockPort):
    def now_utc(self) -> datetime:
        return _NOW

    def monotonic_seconds(self) -> float:
        return 1.0


class _Ids(IdPort):
    def __init__(self, *, object_counter: int = 16) -> None:
        self._object_counter = object_counter

    def new(self, kind: IdKind) -> str:
        if kind is IdKind.OBJECT:
            value = f"obj_{self._object_counter:08x}-0000-4000-8000-000000000001"
            self._object_counter += 1
            return value
        return PREFIX_BY_KIND[kind] + str(uuid.uuid4())


class _Roots:
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id

    async def current(self) -> ObjectRootSnapshot:
        return ObjectRootSnapshot(
            self.task_id,
            _ZERO_DIGEST,
            1,
            1,
            0,
            _ZERO_DIGEST,
            _ZERO_DIGEST,
            _ZERO_DIGEST,
            _ZERO_DIGEST,
            _NOW,
            (),
        )


class _RuntimeRouter(BundleRuntimePort):
    def __init__(self, runtime: TaskRuntime) -> None:
        self.runtime = runtime
        self.route_calls: list[RouteCommand] = []

    async def route(self, command: RouteCommand) -> TaskRuntime:
        self.route_calls.append(command)
        return self.runtime

    async def provision_start(self, command: object) -> TaskRuntime:
        del command
        raise AssertionError("capture test must use its preexisting mapped task")

    async def verify_start(
        self, runtime: TaskRuntime, expectation: object
    ) -> StartCompletionEvidence:
        del runtime, expectation
        raise AssertionError("capture test does not run start")

    async def release(self, runtime: TaskRuntime) -> None:
        assert runtime is self.runtime

    async def close(self) -> None:
        return None


class _ServiceClient:
    def __init__(self, coordinator: ObservationCoordinator) -> None:
        self.coordinator = coordinator
        self.requests: list[ObservationIngestRequest] = []
        self.connect_calls = 0

    async def observation_ingest(
        self, body: DomainJsonValue, *, deadline_ms: int | None = None
    ) -> DomainJsonValue:
        del deadline_ms
        request = observation_ingest_request_from_json(body)
        self.requests.append(request)
        result = await self.coordinator.ingest_request(request)
        return observation_ingest_result_to_json(result)

    async def close(self) -> None:
        return None


type _Connector = Callable[[object], Awaitable[_ServiceClient]]
type _CompositionEvaluator = Callable[
    [FrozenCase, tuple[object, ...], TaskRuntime], Awaitable[FinalSemanticEvaluation]
]


def _ids(kind: IdKind, seed: int) -> str:
    return PREFIX_BY_KIND[kind] + f"{seed:08x}-0000-4000-8000-000000000001"


async def _pipeline(
    tmp_path: Path,
    *,
    codex_session_id: str,
    profile: str | None,
    install_mapping: bool = True,
    identity_seed: int = 0,
) -> tuple[
    Path,
    str,
    str,
    LocalObservationStore,
    SqliteObservationStore,
    SqliteLedger,
    TaskRuntime,
    ObservationCoordinator,
    _ServiceClient,
    _Connector,
]:
    project = tmp_path / "project"
    project.mkdir(mode=0o700)
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    local = LocalObservationStore(_state=state)
    workspace = local.workspace_commitment(str(project.resolve()))
    local.grant_consent(workspace)
    if profile is not None:
        local.enable_content_capture(workspace, profile)
    session_commitment = local.bind_codex_session(workspace, codex_session_id)

    task_id = _ids(IdKind.TASK, identity_seed + 1)
    yoetz_session_id = _ids(IdKind.SESSION, identity_seed + 2)
    writer_id = _ids(IdKind.WRITER, identity_seed + 3)
    if install_mapping:
        store_mapping(
            LifecycleMapping(
                mapping_version=1,
                codex_session_id=codex_session_id,
                yoetz_task_id=task_id,
                yoetz_session_id=yoetz_session_id,
                yoetz_writer_id=writer_id,
                last_frontier=None,
            ),
            _state=state,
        )

    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir(mode=0o700)
    database = apsw.Connection(str(bundle_root / "bundle.sqlite3"))
    initialize_bundle(
        database,
        {
            "task_id": task_id,
            "owner_generation": "1",
            "owner_nonce": _OWNER_NONCE,
        },
    )
    # Keep this end-to-end native-content path on the same guarded writer
    # capability as the production task-bundle composition.  A raw APSW
    # connection would let the coordinator test pass while bypassing the
    # authorizer that exposed #616.
    database.set_authorizer(sqlite_connection._writer_authorizer)  # pyright: ignore[reportPrivateUsage]
    ids = _Ids()
    roots = _Roots(task_id)
    objects = EncryptedFilesObjectStore(
        bundle_root=bundle_root,
        bundle_keys=BundleKeys(
            "native-capture-slot",
            WrapKeyForObjectTest(b"w" * 32),
            MacKeyForObjectTest(b"m" * 32),
        ),
        secret_memory=SecretMemoryForObjectTest(),
        id_port=ids,
        current_root_snapshot=roots.current,
    )
    fence = OwnershipFence("svc_10000000-0000-4000-8000-000000000001", 1, 1, _OWNER_NONCE)
    ledger = SqliteLedger(
        db=database,
        task_id=task_id,
        ownership_fence=fence,
        clock=_Clock(),
        ids=ids,
        objects=objects,
    )
    observation = ledger.open_observation_store()
    runtime = TaskRuntime(
        task_id=task_id,
        session_id=yoetz_session_id,
        writer_id=writer_id,
        capabilities=frozenset(
            {
                RuntimeCapability.WRITE,
                RuntimeCapability.STRUCTURAL_READ,
                RuntimeCapability.PAYLOAD_READ,
                RuntimeCapability.SEMANTIC,
            }
        ),
        ledger=ledger,
        objects=cast(ObjectStorePort, objects),
        importer=cast(ImporterPort, object()),
        projection_version="0.1.0",
        engine_version="0.1.0",
        protocol_version="0.1",
        bundle_schema_version="1.0.0",
        fence=fence,
        observation=cast(TaskObservationPort, observation),
    )
    router = _RuntimeRouter(runtime)

    async def bootstrap(workspace: str, runtime: TaskRuntime, store: TaskObservationPort) -> bool:
        # This isolated pipeline owns exactly this one bundle; supply its
        # complete inventory independently of the content-profile grant.
        assert runtime.task_id == task_id
        assert store is runtime.observation
        current_store = cast(SqliteObservationStore, store)
        tickets = current_store.list_pending_capture_tickets(task_id)
        return local.bootstrap_capture_reservations(
            workspace,
            {task_id: current_store.capture_backlog(workspace)},
            ticket_ids_by_task={
                task_id: tuple(
                    observation_capture_ticket_id(ticket)
                    for ticket in tickets
                    if ticket.workspace_commitment == workspace
                )
            },
        )

    coordinator = ObservationCoordinator(
        runtime=router,
        local=local,
        clock=_Clock(),
        ids=ids,
        state_root=state,
        capture_budget_bootstrap=bootstrap,
    )
    client = _ServiceClient(coordinator)

    async def connect(_kind: object) -> _ServiceClient:
        client.connect_calls += 1
        return client

    return (
        project,
        workspace,
        session_commitment,
        local,
        observation,
        ledger,
        runtime,
        coordinator,
        client,
        connect,
    )


def _claude_hook_runner(
    *,
    project: Path,
    state: Path,
    connect: _Connector,
    profile: str,
) -> Callable[[str, Mapping[str, object]], int]:
    def run_async(factory: Callable[[], Awaitable[object]]) -> object:
        return asyncio.run(factory())

    def run_hook(event_name: str, payload: Mapping[str, object]) -> int:
        return handle_claude_observe(
            event_name=event_name,
            stdin_bytes=canonical_encode(cast(CanonicalJsonValue, payload)),
            workspace=str(project),
            _state=state,
            stdout=io.BytesIO(),
            connect=cast(object, connect),  # type: ignore[arg-type]
            run_async=run_async,
            observation_profile=profile,
        )

    return run_hook


async def _capture_claude_post_requests(
    *,
    client: _ServiceClient,
    monkeypatch: pytest.MonkeyPatch,
    run_hook: Callable[[str, Mapping[str, object]], int],
    session_id: str,
    tool_use_id: str,
    marker: bytes,
) -> tuple[ObservationIngestRequest, ...]:
    """Capture the current PostToolUse handoff after a queued structural pre-event.

    The hook stages the PostToolUse bytes first, then drains the queued
    PreToolUse row and the PostToolUse structural row in FIFO order.  Callers
    that exercise the content protocol need the capture and current structural
    requests; the queued pre-event is validated here so those callers cannot
    accidentally operate on the wrong envelope.
    """

    captured: list[ObservationIngestRequest] = []
    original_observation_ingest = client.observation_ingest

    async def capture_service_request(
        body: DomainJsonValue,
        *,
        deadline_ms: int | None = None,
    ) -> DomainJsonValue:
        request = observation_ingest_request_from_json(body)
        captured.append(request)
        if not request.capture_only and request.envelope.event_kind == "PreToolUse":
            # The deferred pre-event is a real FIFO row.  Let the fixture's
            # production coordinator acknowledge it so the current Post row
            # can be observed at the next structural slot.
            return await original_observation_ingest(body, deadline_ms=deadline_ms)
        reason = (
            OBSERVATION_CONTENT_CAPTURE_PENDING_REASON
            if request.capture_only
            else ObservationGapCode.SERVICE_UNAVAILABLE.value
        )
        return observation_ingest_result_to_json(
            ObservationIngestResult(
                ObservationIngestDisposition.REJECTED,
                reason,
                None,
            )
        )

    monkeypatch.setattr(client, "observation_ingest", capture_service_request)
    assert (
        await asyncio.to_thread(
            run_hook,
            "PostToolUse",
            {
                "hook_event_name": "PostToolUse",
                "session_id": session_id,
                "tool_name": "Bash",
                "tool_use_id": tool_use_id,
                "tool_response": marker.decode("utf-8"),
                "exit_status": 0,
            },
        )
        == 0
    )
    assert len(captured) in {2, 3}
    capture_request = captured[0]
    assert capture_request.capture_only
    assert capture_request.envelope.event_kind == "PostToolUse"
    queued_pre_requests = tuple(
        request for request in captured[1:] if request.envelope.event_kind == "PreToolUse"
    )
    if queued_pre_requests:
        assert len(queued_pre_requests) == 1
        queued_pre_request = queued_pre_requests[0]
        assert not queued_pre_request.capture_only
        assert captured.index(queued_pre_request) > captured.index(capture_request)
    post_structural_requests = tuple(
        request
        for request in captured[1:]
        if request.envelope.event_kind == "PostToolUse" and not request.capture_only
    )
    assert post_structural_requests, [
        (request.envelope.event_kind, request.capture_only) for request in captured
    ]
    structural_request = post_structural_requests[0]
    assert structural_request.envelope.event_kind == "PostToolUse"
    assert not structural_request.capture_only
    if queued_pre_requests:
        assert captured.index(queued_pre_requests[0]) < captured.index(structural_request)
    return capture_request, structural_request


def _pending_structural_request(  # pyright: ignore[reportUnusedFunction]
    local: LocalObservationStore,
    workspace: str,
    *,
    codex_session_id: str,
    event_kind: str,
) -> ObservationIngestRequest:
    """Build a service-shaped request from the locally durable deferred row."""

    rows = local.list_pending_outbox_rows(workspace, codex_session_id=codex_session_id)
    row = next(row for row in rows if row.envelope.event_kind == event_kind)
    return ObservationIngestRequest(codex_session_id=row.codex_session_id, envelope=row.envelope)


def _assert_native_handoff_requests(
    requests: tuple[ObservationIngestRequest, ...],
    *,
    codex_session_id: str,
    profile: str,
    captured_bytes: bytes,
    content_kind: ObservationContentKind,
) -> tuple[
    ObservationIngestRequest,
    ObservationIngestRequest,
    ObservationIngestRequest,
]:
    """Assert capture-first staging followed by the FIFO structural prefix.

    Contentless ordinary-native rows remain in the local outbox until a later
    content-bearing hook.  The later hook stages its transient bytes before
    draining that queued prefix, so the wire order is capture-only PostToolUse,
    structural PreToolUse, structural PostToolUse.  Return the requests by
    their protocol role so callers do not accidentally couple themselves to
    the transport order.
    """

    assert len(requests) == 3
    capture_request = next(request for request in requests if request.capture_only)
    structural_requests = tuple(request for request in requests if not request.capture_only)
    assert len(structural_requests) == 2
    pre_request = next(
        request for request in structural_requests if request.envelope.event_kind == "PreToolUse"
    )
    structural_request = next(
        request for request in structural_requests if request.envelope.event_kind == "PostToolUse"
    )
    assert requests.index(capture_request) < requests.index(pre_request)
    assert requests.index(pre_request) < requests.index(structural_request)
    assert pre_request.codex_session_id == codex_session_id
    assert not pre_request.capture_only
    assert pre_request.content_capture_profile == profile
    assert pre_request.content_chunks == ()
    assert pre_request.envelope.event_kind == "PreToolUse"

    assert capture_request.codex_session_id == codex_session_id
    assert capture_request.capture_only
    assert capture_request.content_capture_profile == profile
    assert len(capture_request.content_chunks) == 1
    capture_chunk = capture_request.content_chunks[0]
    assert capture_chunk.content_kind is content_kind
    assert capture_chunk.content == captured_bytes
    assert capture_request.envelope.event_kind == "PostToolUse"

    assert structural_request.codex_session_id == codex_session_id
    assert not structural_request.capture_only
    assert structural_request.content_capture_profile == profile
    assert structural_request.content_chunks == ()
    assert structural_request.envelope.event_kind == "PostToolUse"
    assert structural_request.envelope.source is capture_request.envelope.source
    assert structural_request.envelope.source_identity == capture_request.envelope.source_identity
    assert structural_request.envelope.cursor == capture_request.envelope.cursor
    assert (
        structural_request.envelope.session_commitment
        == capture_request.envelope.session_commitment
    )
    return pre_request, capture_request, structural_request


def _reopen_runtime(
    tmp_path: Path,
    runtime: TaskRuntime,
) -> tuple[apsw.Connection, SqliteLedger, TaskRuntime]:
    """Reopen the guarded bundle and rebuild the routed runtime for replay checks."""

    database = apsw.Connection(str(tmp_path / "bundle" / "bundle.sqlite3"))
    database.set_authorizer(sqlite_connection._writer_authorizer)  # pyright: ignore[reportPrivateUsage]
    # The original process has already allocated payload/content objects. Keep
    # replay-created check-resume objects outside that deterministic range.
    ids = _Ids(object_counter=64)
    objects = EncryptedFilesObjectStore(
        bundle_root=tmp_path / "bundle",
        bundle_keys=BundleKeys(
            "native-capture-slot",
            WrapKeyForObjectTest(b"w" * 32),
            MacKeyForObjectTest(b"m" * 32),
        ),
        secret_memory=SecretMemoryForObjectTest(),
        id_port=ids,
        current_root_snapshot=_Roots(runtime.task_id).current,
    )
    ledger = SqliteLedger(
        db=database,
        task_id=runtime.task_id,
        ownership_fence=runtime.fence,
        clock=_Clock(),
        ids=ids,
        objects=objects,
    )
    reopened = TaskRuntime(
        task_id=runtime.task_id,
        session_id=runtime.session_id,
        writer_id=runtime.writer_id,
        capabilities=runtime.capabilities,
        ledger=ledger,
        objects=cast(ObjectStorePort, objects),
        importer=cast(ImporterPort, object()),
        projection_version=runtime.projection_version,
        engine_version=runtime.engine_version,
        protocol_version=runtime.protocol_version,
        bundle_schema_version=runtime.bundle_schema_version,
        fence=runtime.fence,
        observation=cast(TaskObservationPort, ledger.open_observation_store()),
    )
    return database, ledger, reopened


async def _native_claude_case(
    tmp_path: Path,
    *,
    marker: bytes,
) -> tuple[
    Path,
    str,
    LocalObservationStore,
    SqliteObservationStore,
    SqliteLedger,
    TaskRuntime,
    FrozenCase,
]:
    """Build one real captured Claude case for composition-level semantic tests."""

    profile = CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID
    (
        project,
        workspace,
        session_commitment,
        local,
        observation,
        ledger,
        runtime,
        _coordinator,
        client,
        connect,
    ) = await _pipeline(
        tmp_path,
        codex_session_id="claude:composition-capture-session",
        profile=profile,
    )

    run_hook = _claude_hook_runner(
        project=project,
        state=tmp_path / "state",
        connect=connect,
        profile=profile,
    )

    assert (
        await asyncio.to_thread(
            run_hook,
            "PreToolUse",
            {
                "hook_event_name": "PreToolUse",
                "session_id": "composition-capture-session",
                "tool_name": "Bash",
                "tool_use_id": "composition-tool-1",
            },
        )
        == 0
    )
    assert (
        await asyncio.to_thread(
            run_hook,
            "PostToolUse",
            {
                "hook_event_name": "PostToolUse",
                "session_id": "composition-capture-session",
                "tool_name": "Bash",
                "tool_use_id": "composition-tool-1",
                "tool_response": marker.decode("utf-8"),
                "exit_status": 0,
            },
        )
        == 0
    )
    _pre_request, _capture_request, _structural_request = _assert_native_handoff_requests(
        tuple(client.requests),
        codex_session_id="claude:composition-capture-session",
        profile=profile,
        captured_bytes=marker,
        content_kind=ObservationContentKind.TOOL_OUTPUT,
    )

    observation.record_workspace_session_route(
        workspace=workspace,
        yoetz_session_id=runtime.session_id,
        yoetz_task_id=runtime.task_id,
        yoetz_writer_id=cast(str, runtime.writer_id),
        codex_session_commitment=session_commitment,
        bound_at=Timestamp("2026-09-05T17:00:00.000Z"),
    )
    frontier = await ledger.load_frontier()
    frozen = await ledger.freeze_case(
        runtime.session_id,
        cast(str, runtime.writer_id),
        frontier.sequence,
        _ids(IdKind.REQUEST, 24),
        _ZERO_DIGEST,
    )
    assert isinstance(frozen, FrozenCase)
    operation = await ledger.lookup_operation(
        cast(str, runtime.writer_id), frozen.lease.operation_id
    )
    assert operation is not None and operation.resume_object_ref is not None
    prior = operation.resume_object_ref
    deterministic_payload = canonical_encode(
        {
            "schema_version": "1.0.0",
            "request_id": frozen.lease.operation_id,
            "request_digest": _ZERO_DIGEST,
            "task_id": runtime.task_id,
            "session_id": runtime.session_id,
            "writer_id": cast(str, runtime.writer_id),
            "subject_frontier": frozen.case.frontier.as_wire(),
            "dependency_digest": frozen.lease.dependency_digest,
            "prior_resume": {
                "object_id": prior.object_id,
                "envelope_digest": prior.envelope_digest,
                "commitment": prior.commitment,
            },
            "policy_executions": (),
            "assessments": (),
        }
    )
    staged = await runtime.objects.stage(
        ObjectSource(data=deterministic_payload, declared_size=len(deterministic_payload)),
        ObjectMetadata(
            ObjectKind.DETERMINISTIC_RESULT,
            "application/vnd.yoetz.deterministic-result+json",
            runtime.task_id,
            _NOW,
        ),
    )
    deterministic_result = await runtime.objects.finalize(staged)
    lease = await ledger.advance_check_phase(
        frozen.lease,
        CheckPhase.RESERVED,
        CheckPhase.LOCAL_READY,
        deterministic_result,
    )
    lease = await ledger.advance_check_phase(
        lease,
        CheckPhase.LOCAL_READY,
        CheckPhase.SEMANTIC_WAIT,
    )
    return project, workspace, local, observation, ledger, runtime, FrozenCase(frozen.case, lease)


def _assisted_composition_evaluator(
    privacy: object,
    *,
    runtime: TaskRuntime,
    local_observation: object,
    profile: ReviewContextProfile = ReviewContextProfile.ASSISTED,
) -> _CompositionEvaluator:
    baseline = semantic_non_dispatch._test_effective_policy()  # pyright: ignore[reportPrivateUsage]
    assisted = replace(
        baseline.policy,
        review_context_profile=profile,
        review_selection=ReviewSelectionPolicy.for_profile(profile),
    )
    policy_application = semantic_non_dispatch._PolicyApplication(  # pyright: ignore[reportPrivateUsage]
        replace(baseline, policy=assisted), repository_granted=True
    )
    setattr(privacy, "policy_application", policy_application)
    setattr(privacy, "terminal_provider_result", True)

    async def resolve_provider() -> ProviderBinding:
        return semantic_non_dispatch._PROVIDER  # pyright: ignore[reportPrivateUsage]

    factory = cast(
        Callable[..., _CompositionEvaluator],
        getattr(
            semantic_non_dispatch.ready_composition_module, "_privacy_gated_semantic_evaluator"
        ),
    )
    return factory(
        cast(PrivacyCoordinator, privacy),
        # Keep the composition clock in the same UTC domain as the task ledger. The production
        # service supplies one clock to both; using the generic July fixture clock here would
        # make a freshly-created September semantic case appear expired before its first claim.
        _Clock(),
        semantic_non_dispatch._INSTALLATION,  # pyright: ignore[reportPrivateUsage]
        resolve_provider,
        cast(
            StartCatalogPort,
            semantic_non_dispatch._Catalog(  # pyright: ignore[reportPrivateUsage]
                semantic_non_dispatch._route_for(runtime.task_id, runtime.session_id)  # pyright: ignore[reportPrivateUsage]
            ),
        ),
        semantic_non_dispatch.ready_composition_module.IdPort(),
        local_observation=local_observation,
    )


@pytest.mark.anyio
async def test_profileless_codex_hook_content_reaches_guarded_task_bundle(
    tmp_path: Path,
) -> None:
    """A Codex PostToolUse body reaches capture on the production SQLite authorizer path."""

    codex_session_id = "codex:profileless-authorizer"
    (
        project,
        workspace,
        session_commitment,
        _local,
        task_observation,
        _ledger,
        _runtime,
        _coordinator,
        client,
        connect,
    ) = await _pipeline(
        tmp_path,
        codex_session_id=codex_session_id,
        profile=None,
    )
    marker = b"codex-profileless-capture-authorizer-marker"
    payload: Mapping[str, object] = {
        "cwd": str(project),
        "hook_event_name": "PostToolUse",
        "model": "gpt-5.6-luna",
        "permission_mode": "on-request",
        "session_id": codex_session_id,
        "tool_input": {"command": "printf fixture"},
        "tool_name": "Bash",
        "tool_response": {
            "aggregated_output": marker.decode("utf-8"),
            "exit_code": 0,
            "stdout": marker.decode("utf-8"),
        },
        "tool_use_id": "codex-profileless-tool-1",
        "transcript_path": str(project / "rollout.jsonl"),
        "turn_id": "codex-profileless-turn-1",
    }

    def run_async(factory: Callable[[], Awaitable[object]]) -> object:
        return asyncio.run(factory())

    assert (
        await asyncio.to_thread(
            handle_observe,
            event_name="PostToolUse",
            stdin_bytes=canonical_encode(cast(CanonicalJsonValue, payload)),
            workspace=str(project),
            _state=tmp_path / "state",
            stdout=io.BytesIO(),
            connect=cast(object, connect),  # type: ignore[arg-type]
            run_async=run_async,
            source=ObservationSource.CODEX_HOOK,
        )
        == 0
    )

    assert len(client.requests) == 2
    capture_request, request = client.requests
    assert capture_request.capture_only is True
    assert capture_request.content_capture_profile is None
    assert len(capture_request.content_chunks) == 1
    captured_chunk = capture_request.content_chunks[0]
    assert marker in captured_chunk.content
    assert request.capture_only is False
    assert request.envelope.source is ObservationSource.CODEX_HOOK
    assert request.content_capture_profile is None
    assert request.content_chunks == ()

    envelopes = task_observation.list_envelopes_for_session(workspace, session_commitment)
    assert len(envelopes) == 1
    envelope = envelopes[0]
    assert envelope.content_object_refs
    manifest = task_observation.load_content_manifest(envelope.content_object_refs[0])
    assert manifest is not None
    assert manifest.content_kind is ObservationContentKind.TOOL_OUTPUT
    assert manifest.content_digest == "sha256:" + hashlib.sha256(captured_chunk.content).hexdigest()
    assert manifest.content_bytes == len(captured_chunk.content)


@pytest.mark.anyio
@pytest.mark.parametrize(
    (
        "host",
        "profile",
        "pre_event_name",
        "pre_payload",
        "post_event_name",
        "post_payload",
        "captured_bytes",
        "marker",
        "content_kind",
    ),
    (
        (
            "claude",
            CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID,
            "PreToolUse",
            {
                "hook_event_name": "PreToolUse",
                "session_id": "claude-capture-session",
                "tool_name": "Bash",
                "tool_use_id": "claude-tool-1",
            },
            "PostToolUse",
            {
                "hook_event_name": "PostToolUse",
                "session_id": "claude-capture-session",
                "tool_name": "Bash",
                "tool_use_id": "claude-tool-1",
                "tool_response": "planted-claude-work-marker: missing validation",
                "exit_status": 0,
            },
            b"planted-claude-work-marker: missing validation",
            b"planted-claude-work-marker: missing validation",
            ObservationContentKind.TOOL_OUTPUT,
        ),
        (
            "cursor",
            CURSOR_ORDINARY_OBSERVATION_PROFILE_ID,
            "preToolUse",
            {
                "hook_event_name": "preToolUse",
                "conversation_id": "cursor-capture-session",
                "tool_use_id": "cursor-tool-1",
                "tool_name": "shell",
                "workspace_roots": (),
            },
            "postToolUse",
            {
                "hook_event_name": "postToolUse",
                "conversation_id": "cursor-capture-session",
                "tool_use_id": "cursor-tool-1",
                "tool_name": "shell",
                "tool_output": '{"exitCode":0,"stdout":"planted-cursor-tool-output-marker: missing validation"}',
                "exit_code": 0,
                "workspace_roots": (),
            },
            b'{"exitCode":0,"stdout":"planted-cursor-tool-output-marker: missing validation"}',
            b"planted-cursor-tool-output-marker: missing validation",
            ObservationContentKind.TOOL_OUTPUT,
        ),
    ),
    ids=("claude-tool-output", "cursor-tool-output"),
)
async def test_ordinary_native_hook_content_reaches_prepared_semantic_packet(
    tmp_path: Path,
    host: str,
    profile: str,
    pre_event_name: str,
    pre_payload: Mapping[str, object],
    post_event_name: str,
    post_payload: Mapping[str, object],
    captured_bytes: bytes,
    marker: bytes,
    content_kind: ObservationContentKind,
) -> None:
    codex_session_id = f"{host}:{pre_payload.get('session_id') or pre_payload['conversation_id']}"
    (
        project,
        workspace,
        session_commitment,
        local,
        task_observation,
        ledger,
        runtime,
        _coordinator,
        client,
        connect,
    ) = await _pipeline(
        tmp_path,
        codex_session_id=codex_session_id,
        profile=profile,
        install_mapping=host != "cursor",
    )

    def run_async(factory: Callable[[], Awaitable[object]]) -> object:
        # pytest-anyio propagates its backend context into ``to_thread``.  Run the synchronous
        # hook's coroutine runner explicitly so the hook exercises its normal service drain.
        return asyncio.run(factory())

    def run_hook(event_name: str, payload: Mapping[str, object]) -> int:
        if host == "claude":
            return handle_claude_observe(
                event_name=event_name,
                stdin_bytes=canonical_encode(cast(CanonicalJsonValue, payload)),
                workspace=str(project),
                _state=tmp_path / "state",
                stdout=io.BytesIO(),
                connect=cast(object, connect),  # type: ignore[arg-type]
                run_async=run_async,
                observation_profile=profile,
            )
        return handle_cursor_observe(
            event_name=event_name,
            stdin_bytes=canonical_encode(cast(CanonicalJsonValue, payload)),
            workspace=str(project),
            _state=tmp_path / "state",
            stdout=io.BytesIO(),
            connect=cast(object, connect),  # type: ignore[arg-type]
            run_async=run_async,
            observation_profile=profile,
        )

    if host == "cursor":
        # #661: the cooperative task exists but this Cursor conversation has no
        # native mapping. Only its owned MCP start result may supply the route.
        assert load_mapping(codex_session_id, _state=tmp_path / "state") is None
        assert await asyncio.to_thread(run_hook, pre_event_name, pre_payload) == 0
        # Contentless native hooks defer service drain until the content-bearing post event.
        assert client.requests == []
        assert local.pending_outbox_count(workspace) == 1
        assert task_observation.list_envelopes_for_session(workspace, session_commitment) == ()
        start_payload = {
            "conversation_id": pre_payload["conversation_id"],
            "tool_name": "start",
            "mcp_server_name": "yoetz",
            "result_json": canonical_encode(
                {
                    "isError": False,
                    "content": [
                        {
                            "type": "text",
                            "text": canonical_encode(
                                {
                                    "ok": True,
                                    "task_id": runtime.task_id,
                                    "session_id": runtime.session_id,
                                    "writer_id": runtime.writer_id,
                                }
                            ).decode(),
                        }
                    ],
                }
            ).decode(),
        }
        assert await asyncio.to_thread(run_hook, "afterMCPExecution", start_payload) == 0
        mapping = load_mapping(codex_session_id, _state=tmp_path / "state")
        assert mapping is not None and mapping.yoetz_task_id == runtime.task_id
        assert mapping.yoetz_session_id == runtime.session_id
        assert mapping.yoetz_writer_id == runtime.writer_id
        assert client.requests == []  # Binding neither ingests nor captures another event.
    else:
        assert await asyncio.to_thread(run_hook, pre_event_name, pre_payload) == 0

    assert await asyncio.to_thread(run_hook, post_event_name, post_payload) == 0
    pre_request, _capture_request, structural_request = _assert_native_handoff_requests(
        tuple(client.requests),
        codex_session_id=codex_session_id,
        profile=profile,
        captured_bytes=captured_bytes,
        content_kind=content_kind,
    )
    assert marker in captured_bytes

    envelopes = task_observation.list_envelopes_for_session(workspace, session_commitment)
    assert len(envelopes) == 2, (
        f"session={session_commitment!r} all="
        f"{[(item.session_commitment, item.source_identity) for item in task_observation.list_envelopes(workspace)]!r}"
    )
    pre_envelope = next(
        item for item in envelopes if item.source_identity == pre_request.envelope.source_identity
    )
    envelope = next(
        item
        for item in envelopes
        if item.source_identity == structural_request.envelope.source_identity
    )
    assert pre_envelope.content_object_refs == ()
    assert pre_envelope.gap_codes == ()
    assert envelope.source_identity == structural_request.envelope.source_identity
    assert envelope.content_object_refs
    assert envelope.gap_codes == ()
    manifest = task_observation.load_content_manifest(envelope.content_object_refs[0])
    assert manifest is not None
    assert manifest.content_kind is content_kind
    content_digest = manifest.content_digest
    assert content_digest == "sha256:" + hashlib.sha256(captured_bytes).hexdigest()
    assert content_digest is not None
    assert manifest.content_bytes == len(captured_bytes)

    # The service's routed-session table is the resolver's exact host/session fence.  This
    # fixture has no approved-check policy, so verification setup returns early; the route must
    # still have been recorded by the coordinator before that optional policy path.
    assert task_observation.observation_route_for_session(
        workspace=workspace,
        yoetz_session_id=runtime.session_id,
    ) == (session_commitment, runtime.task_id, True)
    frontier = await ledger.load_frontier()
    frozen = await ledger.freeze_case(
        runtime.session_id,
        cast(str, runtime.writer_id),
        frontier.sequence,
        _ids(IdKind.REQUEST, 4),
        _ZERO_DIGEST,
    )
    assert isinstance(frozen, FrozenCase)
    resolved = await resolve_captured_semantic_content(
        runtime=runtime,
        frozen=frozen,
        workspace_commitment=workspace,
        local_observation=local,
    )
    assert resolved.gaps == ()
    assert len(resolved.content) == 1
    captured = resolved.content[0]
    assert captured.content == captured_bytes
    assert captured.manifest.object_id == envelope.content_object_refs[0]
    assert captured.object_ref.metadata.kind is ObjectKind.CAPTURED_CONTENT
    assert captured.object_ref.metadata.media_type == _CAPTURE_MEDIA_TYPE
    object_path = (
        tmp_path
        / "bundle"
        / "objects"
        / captured.object_ref.object_id[4:6]
        / captured.object_ref.object_id
    )
    assert object_path.is_file()
    assert marker not in object_path.read_bytes()

    semantic = build_semantic_case(
        case_id="cas_10000000-0000-4000-8000-000000000005",
        frozen_case=frozen.case,
        dependency_digest=frozen.lease.dependency_digest,
        findings=(),
        review_context_profile=ReviewContextProfile.EXPANDED,
        review_selection=ReviewSelectionPolicy.for_profile(ReviewContextProfile.EXPANDED),
        policy_id="research-evidence",
        policy_version="0.1.0",
        captured_content=resolved.content,
        captured_content_scope=resolved.scope,
        captured_content_gaps=resolved.gaps,
    )
    assert resolved.scope is not None
    captured_evidence_refs = tuple(resolved.scope.phase_bindings)
    assert len(captured_evidence_refs) == 1
    captured_coverage = frozen.case.coverage_by_ref[evidence_id(captured_evidence_refs[0][0])]
    assert captured_coverage.artifact_observation is ArtifactObservation.CONTENT_CAPTURED
    assert captured_coverage.authorship_assurance is AuthorshipAssurance.SERVICE_AUTHENTICATED
    assert captured_coverage.evidence_immutability is EvidenceImmutability.IMMUTABLE_SNAPSHOT
    assert PublicationChannel.HOOK_OBSERVED in captured_coverage.publication_channels
    assert semantic.packet.coverage.known_gaps == ()
    assert semantic.packet.coverage.check_types
    excerpt = next(
        item
        for item in semantic.packet.targeted_excerpts
        if item.content_digest == "sha256:" + hashlib.sha256(captured_bytes).hexdigest()
    )
    assert excerpt.content_visibility == "available"
    assert "unpaired_event" not in semantic.packet.coverage.known_gaps
    prepared = semantic_case_to_prepared_payload(
        semantic,
        {item.item_id for item in semantic.items},
    )
    assert marker.decode("utf-8") in prepared.decode("utf-8")
    assert content_digest.encode("ascii") in prepared

    reopened_db, reopened_ledger, reopened_runtime = _reopen_runtime(tmp_path, runtime)
    try:
        reopened_frontier = await reopened_ledger.load_frontier()
        reopened_frozen = await reopened_ledger.freeze_case(
            reopened_runtime.session_id,
            cast(str, reopened_runtime.writer_id),
            reopened_frontier.sequence,
            _ids(IdKind.REQUEST, 5),
            _ZERO_DIGEST,
        )
        assert isinstance(reopened_frozen, FrozenCase)
        reopened_resolved = await resolve_captured_semantic_content(
            runtime=reopened_runtime,
            frozen=reopened_frozen,
            workspace_commitment=workspace,
            local_observation=local,
        )
        assert reopened_resolved.gaps == ()
        assert len(reopened_resolved.content) == 1
        assert reopened_resolved.content[0].content == captured_bytes
        assert reopened_resolved.scope is not None
        reopened_ref = evidence_id(reopened_resolved.scope.phase_bindings[0][0])
        assert (
            reopened_frozen.case.coverage_by_ref[reopened_ref].artifact_observation
            is ArtifactObservation.CONTENT_CAPTURED
        )
    finally:
        reopened_db.close(force=True)


@pytest.mark.anyio
async def test_native_manifest_binding_rejects_foreign_source_or_correlation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A native manifest cannot lend content across an envelope source binding."""

    profile = CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID
    (
        project,
        workspace,
        _session_commitment,
        _local,
        observation,
        _ledger,
        runtime,
        coordinator,
        _client,
        connect,
    ) = await _pipeline(
        tmp_path,
        codex_session_id="claude:binding-rejection-session",
        profile=profile,
    )
    run_hook = _claude_hook_runner(
        project=project,
        state=tmp_path / "state",
        connect=connect,
        profile=profile,
    )
    captured_requests = await _capture_claude_post_requests(
        client=_client,
        monkeypatch=monkeypatch,
        run_hook=run_hook,
        session_id="binding-rejection-session",
        tool_use_id="binding-rejection-tool",
        marker=b"binding-rejection-marker",
    )
    capture_request = next(item for item in captured_requests if item.capture_only)
    chunk = capture_request.content_chunks[0]
    foreign_commitment = "hmac-sha256:" + "f" * 64
    malformed_chunks = (
        replace(chunk, source_commitment=foreign_commitment),
        replace(chunk, correlation_identity="hook:foreign-source:tool-output"),
    )
    logical_identity = observation_content_identity(capture_request.envelope)
    before = observation.content_manifests_for_logical_identity(
        workspace=workspace,
        logical_identity=logical_identity,
    )
    for malformed in malformed_chunks:
        captured, replay, _redacted, unavailable = await coordinator._capture_content(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
            runtime,
            observation,
            workspace=workspace,
            envelope=capture_request.envelope,
            chunks=(malformed,),
        )
        assert captured == ()
        assert replay == ()
        assert unavailable is True
    after = observation.content_manifests_for_logical_identity(
        workspace=workspace,
        logical_identity=logical_identity,
    )
    assert after == before == ()

    for index, malformed in enumerate(malformed_chunks, start=1):
        object_value = f"obj_{index:08x}-0000-4000-8000-000000000001"
        manifest = ObservationContentManifest(
            object_id=object_value,
            envelope_digest="sha256:" + "a" * 64,
            content_kind=malformed.content_kind,
            part_index=malformed.part_index,
            part_count=malformed.part_count,
            redacted=False,
            content_digest="sha256:" + hashlib.sha256(malformed.content).hexdigest(),
            content_bytes=len(malformed.content),
            correlation_identity=malformed.correlation_identity,
            source_commitment=malformed.source_commitment,
        )
        batch = materialize_observation_envelope(
            replace(capture_request.envelope, content_object_refs=(object_value,)),
            task_id=runtime.task_id,
            captured_content=(manifest,),
        )
        assert ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value in batch.gaps
        assert all(not item.role.startswith("captured_") for item in batch.drafts)


@pytest.mark.anyio
async def test_native_manifest_survives_preappend_cancellation_and_structural_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retry after manifest persistence recovers bytes and appends only once."""

    profile = CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID
    marker = b"preappend-cancellation-native-marker: recover this"
    (
        project,
        workspace,
        session_commitment,
        local,
        observation,
        ledger,
        runtime,
        coordinator,
        client,
        connect,
    ) = await _pipeline(
        tmp_path,
        codex_session_id="claude:preappend-cancellation",
        profile=profile,
    )

    run_hook = _claude_hook_runner(
        project=project,
        state=tmp_path / "state",
        connect=connect,
        profile=profile,
    )

    assert (
        await asyncio.to_thread(
            run_hook,
            "PreToolUse",
            {
                "hook_event_name": "PreToolUse",
                "session_id": "preappend-cancellation",
                "tool_name": "Bash",
                "tool_use_id": "preappend-tool-1",
            },
        )
        == 0
    )
    captured_requests = await _capture_claude_post_requests(
        client=client,
        monkeypatch=monkeypatch,
        run_hook=run_hook,
        session_id="preappend-cancellation",
        tool_use_id="preappend-tool-1",
        marker=marker,
    )
    capture_request = next(request for request in captured_requests if request.capture_only)
    structural_request = next(request for request in captured_requests if not request.capture_only)
    assert capture_request.content_chunks[0].content == marker
    before_failure = await ledger.load_frontier()

    staged = await coordinator.ingest_request(capture_request)
    assert staged.disposition is ObservationIngestDisposition.REJECTED
    assert staged.reason == OBSERVATION_CONTENT_CAPTURE_PENDING_REASON

    append_calls = 0

    async def cancel_append(*args: object, **kwargs: object) -> object:
        nonlocal append_calls
        del args, kwargs
        append_calls += 1
        raise asyncio.CancelledError()

    monkeypatch.setattr(coordinator, "_append_materialized", cancel_append)
    with pytest.raises(asyncio.CancelledError):
        await coordinator.ingest_request(structural_request)
    assert append_calls == 1

    stored = next(
        item
        for item in observation.list_envelopes_for_session(workspace, session_commitment)
        if item.source_identity == structural_request.envelope.source_identity
    )
    assert stored.content_object_refs
    manifest = observation.load_content_manifest(stored.content_object_refs[0])
    assert manifest is not None
    assert manifest.content_digest == "sha256:" + hashlib.sha256(marker).hexdigest()
    assert (await ledger.load_frontier()).sequence == before_failure.sequence
    ticket = observation.load_capture_ticket(
        workspace=workspace,
        logical_identity=observation_content_identity(structural_request.envelope),
    )
    assert ticket is not None and ticket.state == "pending"

    reopened_db, reopened_ledger, reopened_runtime = _reopen_runtime(tmp_path, runtime)
    reopened_coordinator = ObservationCoordinator(
        runtime=_RuntimeRouter(reopened_runtime),
        local=local,
        clock=_Clock(),
        ids=_Ids(object_counter=96),
        state_root=tmp_path / "state",
        capture_budget_bootstrap=coordinator.capture_budget_bootstrap,
    )
    try:
        structural_retry = ObservationIngestRequest(
            codex_session_id=structural_request.codex_session_id,
            envelope=structural_request.envelope,
        )
        first_retry = await reopened_coordinator.ingest_request(structural_retry)
        assert first_retry.disposition is ObservationIngestDisposition.DUPLICATE
        after_retry = await reopened_ledger.load_frontier()
        assert after_retry.sequence > before_failure.sequence

        second_retry = await reopened_coordinator.ingest_request(structural_retry)
        assert second_retry.disposition is ObservationIngestDisposition.DUPLICATE
        assert (await reopened_ledger.load_frontier()).sequence == after_retry.sequence

        reopened_observation = cast(SqliteObservationStore, reopened_runtime.observation)
        reopened_observation.record_workspace_session_route(
            workspace=workspace,
            yoetz_session_id=reopened_runtime.session_id,
            yoetz_task_id=reopened_runtime.task_id,
            yoetz_writer_id=cast(str, reopened_runtime.writer_id),
            codex_session_commitment=session_commitment,
            bound_at=Timestamp("2026-09-05T17:00:00.000Z"),
        )
        frozen = await reopened_ledger.freeze_case(
            reopened_runtime.session_id,
            cast(str, reopened_runtime.writer_id),
            after_retry.sequence,
            _ids(IdKind.REQUEST, 40),
            _ZERO_DIGEST,
        )
        assert isinstance(frozen, FrozenCase)
        resolved = await resolve_captured_semantic_content(
            runtime=reopened_runtime,
            frozen=frozen,
            workspace_commitment=workspace,
            local_observation=local,
        )
        assert resolved.gaps == ()
        assert len(resolved.content) == 1
        assert resolved.content[0].content == marker

        semantic = build_semantic_case(
            case_id=_ids(IdKind.OUTBOUND_CASE, 41),
            frozen_case=frozen.case,
            dependency_digest=frozen.lease.dependency_digest,
            findings=(),
            review_context_profile=ReviewContextProfile.EXPANDED,
            review_selection=ReviewSelectionPolicy.for_profile(ReviewContextProfile.EXPANDED),
            policy_id="native-retry",
            policy_version="0.1.0",
            captured_content=resolved.content,
            captured_content_scope=resolved.scope,
            captured_content_gaps=resolved.gaps,
        )
        prepared = semantic_case_to_prepared_payload(
            semantic,
            {item.item_id for item in semantic.items},
        )
        assert marker.decode("utf-8") in prepared.decode("utf-8")
    finally:
        reopened_coordinator.close()
        reopened_db.close(force=True)


@pytest.mark.anyio
async def test_native_pending_ticket_replays_through_same_task_successor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A validated same-task successor drains a predecessor's immutable handoff."""

    profile = CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID
    marker = b"same-task-successor-native-marker: retain this"
    (
        project,
        workspace,
        session_commitment,
        local,
        observation,
        ledger,
        runtime,
        coordinator,
        client,
        connect,
    ) = await _pipeline(
        tmp_path,
        codex_session_id="claude:same-task-successor",
        profile=profile,
    )
    run_hook = _claude_hook_runner(
        project=project,
        state=tmp_path / "state",
        connect=connect,
        profile=profile,
    )
    assert (
        await asyncio.to_thread(
            run_hook,
            "PreToolUse",
            {
                "hook_event_name": "PreToolUse",
                "session_id": "same-task-successor",
                "tool_name": "Bash",
                "tool_use_id": "same-task-tool-1",
            },
        )
        == 0
    )
    captured_requests = await _capture_claude_post_requests(
        client=client,
        monkeypatch=monkeypatch,
        run_hook=run_hook,
        session_id="same-task-successor",
        tool_use_id="same-task-tool-1",
        marker=marker,
    )
    capture_request = next(request for request in captured_requests if request.capture_only)
    structural_request = next(request for request in captured_requests if not request.capture_only)
    staged = await coordinator.ingest_request(capture_request)
    assert staged.reason == OBSERVATION_CONTENT_CAPTURE_PENDING_REASON

    predecessor_session = runtime.session_id
    pending_ticket = observation.load_capture_ticket(
        workspace=workspace,
        logical_identity=observation_content_identity(structural_request.envelope),
    )
    assert pending_ticket is not None
    assert pending_ticket.yoetz_session_id == predecessor_session
    successor_session = _ids(IdKind.SESSION, 990)
    successor_writer = _ids(IdKind.WRITER, 991)
    successor_runtime = replace(runtime, session_id=successor_session, writer_id=successor_writer)

    current_mapping = load_mapping("claude:same-task-successor", _state=tmp_path / "state")
    assert current_mapping is not None
    store_mapping(
        LifecycleMapping(
            mapping_version=current_mapping.mapping_version,
            codex_session_id=current_mapping.codex_session_id,
            yoetz_task_id=runtime.task_id,
            yoetz_session_id=successor_session,
            yoetz_writer_id=successor_writer,
            last_frontier=current_mapping.last_frontier,
        ),
        _state=tmp_path / "state",
    )

    class _SuccessorRuntime:
        async def route(self, command: RouteCommand) -> TaskRuntime:
            assert command.session_id == successor_session
            return successor_runtime

        async def provision_start(self, command: object) -> TaskRuntime:
            del command
            raise AssertionError("successor replay must use its existing runtime")

        async def verify_start(
            self, runtime: TaskRuntime, expectation: object
        ) -> StartCompletionEvidence:
            del runtime, expectation
            raise AssertionError("successor replay does not run start")

        async def release(self, runtime: TaskRuntime) -> None:
            assert runtime is successor_runtime

        async def close(self) -> None:
            return None

    successor_coordinator = ObservationCoordinator(
        runtime=_SuccessorRuntime(),
        local=local,
        clock=_Clock(),
        ids=_Ids(object_counter=112),
        state_root=tmp_path / "state",
        capture_budget_bootstrap=coordinator.capture_budget_bootstrap,
    )
    first_retry = await successor_coordinator.ingest_request(
        ObservationIngestRequest(
            codex_session_id=structural_request.codex_session_id,
            envelope=structural_request.envelope,
        )
    )
    assert first_retry.disposition is ObservationIngestDisposition.ACCEPTED, first_retry.reason
    assert (
        observation.load_capture_ticket(
            workspace=workspace,
            logical_identity=observation_content_identity(structural_request.envelope),
        )
        is None
    )

    observation.record_workspace_session_route(
        workspace=workspace,
        yoetz_session_id=successor_session,
        yoetz_task_id=runtime.task_id,
        yoetz_writer_id=successor_writer,
        codex_session_commitment=session_commitment,
        bound_at=Timestamp("2026-09-05T17:00:00.000Z"),
    )
    frontier = await ledger.load_frontier()
    frozen = await ledger.freeze_case(
        successor_session,
        successor_writer,
        frontier.sequence,
        _ids(IdKind.REQUEST, 992),
        _ZERO_DIGEST,
    )
    assert isinstance(frozen, FrozenCase)
    resolved = await resolve_captured_semantic_content(
        runtime=successor_runtime,
        frozen=frozen,
        workspace_commitment=workspace,
        local_observation=local,
    )
    assert resolved.gaps == ()
    assert len(resolved.content) == 1
    assert resolved.content[0].content == marker

    after_first_retry = await ledger.load_frontier()
    second_retry = await successor_coordinator.ingest_request(
        ObservationIngestRequest(
            codex_session_id=structural_request.codex_session_id,
            envelope=structural_request.envelope,
        )
    )
    assert second_retry.disposition is ObservationIngestDisposition.DUPLICATE
    assert await ledger.load_frontier() == after_first_retry


@pytest.mark.anyio
async def test_terminal_cursor_rejection_tombstones_ticket_and_unblocks_freeze(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A terminal structural refusal cannot leave its native handoff blocking checks."""

    profile = CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID
    marker = b"terminal-cursor-rejection-native-marker"
    (
        project,
        workspace,
        _session_commitment,
        _local,
        observation,
        ledger,
        runtime,
        coordinator,
        client,
        connect,
    ) = await _pipeline(
        tmp_path,
        codex_session_id="claude:terminal-cursor-rejection",
        profile=profile,
    )
    run_hook = _claude_hook_runner(
        project=project,
        state=tmp_path / "state",
        connect=connect,
        profile=profile,
    )
    assert (
        await asyncio.to_thread(
            run_hook,
            "PreToolUse",
            {
                "hook_event_name": "PreToolUse",
                "session_id": "terminal-cursor-rejection",
                "tool_name": "Bash",
                "tool_use_id": "terminal-cursor-rejection-tool-1",
            },
        )
        == 0
    )
    captured_requests = await _capture_claude_post_requests(
        client=client,
        monkeypatch=monkeypatch,
        run_hook=run_hook,
        session_id="terminal-cursor-rejection",
        tool_use_id="terminal-cursor-rejection-tool-1",
        marker=marker,
    )
    capture_request = next(request for request in captured_requests if request.capture_only)
    structural_request = next(request for request in captured_requests if not request.capture_only)
    staged = await coordinator.ingest_request(capture_request)
    assert staged.reason == OBSERVATION_CONTENT_CAPTURE_PENDING_REASON

    # Advance the task observation cursor through a different envelope. The original
    # structural row now receives the terminal CURSOR_STALE refusal after it has
    # successfully recovered the ticket's encrypted content.
    newer_envelope = replace(
        structural_request.envelope,
        source_identity="hook:terminal-cursor-rejection-newer",
        cursor=replace(
            structural_request.envelope.cursor,
            event_position=structural_request.envelope.cursor.event_position + 1,
        ),
    )
    advanced = await observation.ingest(newer_envelope)
    assert advanced.disposition is ObservationIngestDisposition.ACCEPTED

    refused = await coordinator.ingest_request(structural_request)
    assert refused.disposition is ObservationIngestDisposition.REJECTED
    assert refused.reason == ObservationGapCode.CURSOR_STALE.value
    ticket = observation.load_capture_ticket(
        workspace=workspace,
        logical_identity=observation_content_identity(structural_request.envelope),
    )
    assert ticket is not None and ticket.state == "revoked"

    frontier = await ledger.load_frontier()
    frozen = await ledger.freeze_case(
        runtime.session_id,
        cast(str, runtime.writer_id),
        frontier.sequence,
        _ids(IdKind.REQUEST, 993),
        _ZERO_DIGEST,
    )
    assert isinstance(frozen, FrozenCase)


@pytest.mark.anyio
async def test_native_ticket_from_unproven_session_cannot_lend_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A same-task ticket without an admitted route lineage remains fenced."""

    profile = CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID
    marker = b"unproven-native-session-marker: do not lend"
    (
        project,
        workspace,
        _session_commitment,
        local,
        observation,
        _ledger,
        runtime,
        coordinator,
        client,
        connect,
    ) = await _pipeline(
        tmp_path,
        codex_session_id="claude:unproven-ticket",
        profile=profile,
    )
    run_hook = _claude_hook_runner(
        project=project,
        state=tmp_path / "state",
        connect=connect,
        profile=profile,
    )
    assert (
        await asyncio.to_thread(
            run_hook,
            "PreToolUse",
            {
                "hook_event_name": "PreToolUse",
                "session_id": "unproven-ticket",
                "tool_name": "Bash",
                "tool_use_id": "unproven-tool-1",
            },
        )
        == 0
    )
    captured_requests = await _capture_claude_post_requests(
        client=client,
        monkeypatch=monkeypatch,
        run_hook=run_hook,
        session_id="unproven-ticket",
        tool_use_id="unproven-tool-1",
        marker=marker,
    )
    capture_request = next(request for request in captured_requests if request.capture_only)
    structural_request = next(request for request in captured_requests if not request.capture_only)
    authority = local.content_capture_authority(workspace)
    assert authority is not None
    unproven_ticket = ObservationCaptureTicket(
        workspace_commitment=workspace,
        task_id=runtime.task_id,
        yoetz_session_id=_ids(IdKind.SESSION, 993),
        session_commitment=structural_request.envelope.session_commitment,
        source=structural_request.envelope.source,
        source_identity=structural_request.envelope.source_identity,
        cursor=structural_request.envelope.cursor,
        logical_identity=observation_content_identity(structural_request.envelope),
        content_capture_profile=profile,
        authority_generation=authority.generation,
        object_ids=(),
        captured_at=Timestamp("2026-09-05T17:00:00.000Z"),
        state="staging",
        expected_parts=observation_capture_part_descriptors(capture_request.content_chunks),
    )
    observation.record_capture_ticket(unproven_ticket)

    refused = await coordinator.ingest_request(structural_request)
    assert refused.disposition is ObservationIngestDisposition.REJECTED
    assert refused.reason == ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value
    retained = observation.load_capture_ticket(
        workspace=workspace,
        logical_identity=unproven_ticket.logical_identity,
    )
    assert retained is not None and retained.state == "staging"
    assert not any(
        item.source_identity == structural_request.envelope.source_identity
        for item in observation.list_envelopes(workspace)
    )


@pytest.mark.anyio
async def test_native_finalize_without_manifest_stays_orphan_on_structural_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A finalized object without its manifest is never fabricated into captured evidence."""

    profile = CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID
    marker = b"finalize-before-manifest-native-marker: must stay unavailable"
    (
        project,
        workspace,
        session_commitment,
        local,
        observation,
        _ledger,
        runtime,
        coordinator,
        client,
        connect,
    ) = await _pipeline(
        tmp_path,
        codex_session_id="claude:finalize-before-manifest",
        profile=profile,
    )

    run_hook = _claude_hook_runner(
        project=project,
        state=tmp_path / "state",
        connect=connect,
        profile=profile,
    )

    assert (
        await asyncio.to_thread(
            run_hook,
            "PreToolUse",
            {
                "hook_event_name": "PreToolUse",
                "session_id": "finalize-before-manifest",
                "tool_name": "Bash",
                "tool_use_id": "finalize-tool-1",
            },
        )
        == 0
    )

    captured_requests = await _capture_claude_post_requests(
        client=client,
        monkeypatch=monkeypatch,
        run_hook=run_hook,
        session_id="finalize-before-manifest",
        tool_use_id="finalize-tool-1",
        marker=marker,
    )

    def object_files() -> set[Path]:
        objects_root = tmp_path / "bundle" / "objects"
        if not objects_root.is_dir():
            return set()
        return {
            path
            for shard in objects_root.iterdir()
            if shard.is_dir() and shard.name != ".staging"
            for path in shard.iterdir()
            if path.is_file()
        }

    def count_rows(database: apsw.Connection, query: str) -> int:
        row = database.execute(query).fetchone()
        assert row is not None
        return cast(int, row[0])

    before_files = object_files()
    before_inventory = count_rows(
        observation._db,  # pyright: ignore[reportPrivateUsage]
        "SELECT COUNT(*) FROM objects",
    )
    before_manifests = count_rows(
        observation._db,  # pyright: ignore[reportPrivateUsage]
        "SELECT COUNT(*) FROM observation_content_manifests",
    )

    capture_request = next(request for request in captured_requests if request.capture_only)
    structural_request = next(request for request in captured_requests if not request.capture_only)

    def fail_manifest(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("simulated_manifest_transaction_failure")

    monkeypatch.setattr(observation, "record_content_manifest", fail_manifest)
    failed_capture = await coordinator.ingest_request(capture_request)
    assert failed_capture.disposition is ObservationIngestDisposition.REJECTED
    assert failed_capture.reason == ObservationGapCode.SERVICE_UNAVAILABLE.value
    after_failure_files = object_files()
    orphan_files = after_failure_files - before_files
    assert len(orphan_files) == 1
    orphan = next(iter(orphan_files))
    assert orphan.is_file()
    assert (
        count_rows(
            observation._db,  # pyright: ignore[reportPrivateUsage]
            "SELECT COUNT(*) FROM objects",
        )
        == before_inventory
    )
    assert (
        count_rows(
            observation._db,  # pyright: ignore[reportPrivateUsage]
            "SELECT COUNT(*) FROM observation_content_manifests",
        )
        == before_manifests
        == 0
    )
    # A fresh service process sees only the structural outbox envelope. It may commit that
    # envelope, but it has no authenticated manifest pointer from which to invent the marker.
    reopened_db, _reopened_ledger, reopened_runtime = _reopen_runtime(tmp_path, runtime)
    reopened_coordinator = ObservationCoordinator(
        runtime=_RuntimeRouter(reopened_runtime),
        local=local,
        clock=_Clock(),
        ids=_Ids(object_counter=96),
        state_root=tmp_path / "state",
        capture_budget_bootstrap=coordinator.capture_budget_bootstrap,
    )
    try:
        structural_retry = ObservationIngestRequest(
            codex_session_id=structural_request.codex_session_id,
            envelope=structural_request.envelope,
        )
        retry = await reopened_coordinator.ingest_request(structural_retry)
        assert retry.disposition is ObservationIngestDisposition.ACCEPTED
        reopened_observation = cast(SqliteObservationStore, reopened_runtime.observation)
        stored = next(
            item
            for item in reopened_observation.list_envelopes_for_session(
                workspace, session_commitment
            )
            if item.source_identity == structural_request.envelope.source_identity
        )
        assert stored.content_object_refs == ()
        assert (
            count_rows(
                reopened_db,
                "SELECT COUNT(*) FROM observation_content_manifests",
            )
            == 0
        )
        assert orphan.exists()
        assert (
            reopened_db.execute(  # pyright: ignore[reportPrivateUsage]
                "SELECT 1 FROM objects WHERE object_id=?", (orphan.name,)
            ).fetchone()
            is None
        )

    finally:
        reopened_coordinator.close()
        reopened_db.close(force=True)


@pytest.mark.anyio
async def test_ready_composition_selects_real_native_marker_with_distinct_commitments(
    tmp_path: Path,
) -> None:
    marker = b"composition-real-native-marker: inspect this"
    project, workspace, local, _observation, _ledger, runtime, frozen = await _native_claude_case(
        tmp_path,
        marker=marker,
    )
    del project
    assert workspace != semantic_non_dispatch._REPOSITORY  # pyright: ignore[reportPrivateUsage]

    privacy = semantic_non_dispatch._Privacy(  # pyright: ignore[reportPrivateUsage]
        task_id=runtime.task_id
    )
    candidates: list[object] = []
    original_evaluate = privacy.evaluate_semantic

    async def record_candidate(candidate: object, deadline: object) -> object:
        candidates.append(candidate)
        return await original_evaluate(candidate, deadline)

    setattr(privacy, "evaluate_semantic", record_candidate)
    evaluator = _assisted_composition_evaluator(
        privacy,
        runtime=runtime,
        local_observation=local,
        profile=ReviewContextProfile.EXPANDED,
    )

    result = await evaluator(frozen, (), runtime)

    assert result.status is SemanticStatus.UNAVAILABLE
    assert candidates
    excerpt_items = tuple(
        item
        for item in getattr(candidates[0], "items")
        if cast(str, getattr(item, "origin_ref")).startswith("/case/excerpt/")
    )
    assert excerpt_items
    assert any(marker in cast(bytes, getattr(item, "plaintext")) for item in excerpt_items)


@pytest.mark.anyio
async def test_ready_composition_route_loss_after_resolution_blocks_native_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = b"composition-route-loss-marker: inspect this"
    (
        project,
        workspace,
        local,
        observation,
        _ledger,
        original_runtime,
        frozen,
    ) = await _native_claude_case(tmp_path, marker=marker)
    del project

    class _RouteLossObservation:
        def __init__(self, delegate: SqliteObservationStore) -> None:
            self.delegate = delegate
            self.lost = False

        def workspace_for_yoetz_session(self, session_id: str) -> str | None:
            if self.lost:
                return None
            return self.delegate.workspace_for_yoetz_session(session_id)

        def observation_route_for_session(
            self, *, workspace: str, yoetz_session_id: str
        ) -> tuple[str, str, bool] | None:
            if self.lost:
                return None
            return self.delegate.observation_route_for_session(
                workspace=workspace,
                yoetz_session_id=yoetz_session_id,
            )

        def __getattr__(self, name: str) -> object:
            return getattr(self.delegate, name)

    routed_observation = _RouteLossObservation(observation)
    runtime = replace(original_runtime, observation=routed_observation)
    real_resolver = semantic_non_dispatch.ready_composition_module.resolve_captured_semantic_content

    async def resolve_then_lose_route(**kwargs: object) -> object:
        resolved = await real_resolver(
            runtime=cast(TaskRuntime, kwargs["runtime"]),
            frozen=cast(FrozenCase, kwargs["frozen"]),
            workspace_commitment=cast(str, kwargs["workspace_commitment"]),
            local_observation=kwargs.get("local_observation"),
            max_parts=cast(int, kwargs["max_parts"]),
            max_total_bytes=cast(int, kwargs["max_total_bytes"]),
        )
        routed_observation.lost = True
        return resolved

    monkeypatch.setattr(
        semantic_non_dispatch.ready_composition_module,
        "resolve_captured_semantic_content",
        resolve_then_lose_route,
    )
    privacy = semantic_non_dispatch._Privacy(  # pyright: ignore[reportPrivateUsage]
        task_id=runtime.task_id
    )
    evaluator = _assisted_composition_evaluator(
        privacy,
        runtime=runtime,
        local_observation=local,
        profile=ReviewContextProfile.EXPANDED,
    )

    result = await evaluator(frozen, (), runtime)

    assert workspace != semantic_non_dispatch._REPOSITORY  # pyright: ignore[reportPrivateUsage]
    assert routed_observation.lost
    assert result.status is SemanticStatus.BLOCKED_BY_POLICY
    assert privacy.calls == 0


@pytest.mark.anyio
async def test_unreadable_native_capture_degrades_without_semantic_content(tmp_path: Path) -> None:
    """A deleted captured object must become an explicit content gap after replay."""

    (
        project,
        workspace,
        _session_commitment,
        local,
        observation,
        _ledger,
        runtime,
        _coordinator,
        client,
        connect,
    ) = await _pipeline(
        tmp_path,
        codex_session_id="claude:unreadable-content-session",
        profile=CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID,
    )

    def run_async(factory: Callable[[], Awaitable[object]]) -> object:
        return asyncio.run(factory())

    def run_hook(event_name: str, payload: Mapping[str, object]) -> int:
        return handle_claude_observe(
            event_name=event_name,
            stdin_bytes=canonical_encode(cast(CanonicalJsonValue, payload)),
            workspace=str(project),
            _state=tmp_path / "state",
            stdout=io.BytesIO(),
            connect=cast(object, connect),  # type: ignore[arg-type]
            run_async=run_async,
            observation_profile=CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID,
        )

    await asyncio.to_thread(
        run_hook,
        "PreToolUse",
        {
            "hook_event_name": "PreToolUse",
            "session_id": "unreadable-content-session",
            "tool_name": "Bash",
            "tool_use_id": "unreadable-tool-1",
        },
    )
    await asyncio.to_thread(
        run_hook,
        "PostToolUse",
        {
            "hook_event_name": "PostToolUse",
            "session_id": "unreadable-content-session",
            "tool_name": "Bash",
            "tool_use_id": "unreadable-tool-1",
            "tool_response": "unreadable-native-capture-marker",
            "exit_status": 0,
        },
    )
    _pre_request, _capture_request, structural_request = _assert_native_handoff_requests(
        tuple(client.requests),
        codex_session_id="claude:unreadable-content-session",
        profile=CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID,
        captured_bytes=b"unreadable-native-capture-marker",
        content_kind=ObservationContentKind.TOOL_OUTPUT,
    )
    envelopes = observation.list_envelopes(workspace)
    post = next(
        item
        for item in envelopes
        if item.source_identity == structural_request.envelope.source_identity
    )
    assert post.content_object_refs
    object_id_value = post.content_object_refs[0]
    object_path = tmp_path / "bundle" / "objects" / object_id_value[4:6] / object_id_value
    assert object_path.is_file()
    object_path.unlink()

    reopened_db, reopened_ledger, reopened_runtime = _reopen_runtime(tmp_path, runtime)
    try:
        frontier = await reopened_ledger.load_frontier()
        frozen = await reopened_ledger.freeze_case(
            reopened_runtime.session_id,
            cast(str, reopened_runtime.writer_id),
            frontier.sequence,
            _ids(IdKind.REQUEST, 7),
            _ZERO_DIGEST,
        )
        assert isinstance(frozen, FrozenCase)
        resolved = await resolve_captured_semantic_content(
            runtime=reopened_runtime,
            frozen=frozen,
            workspace_commitment=workspace,
            local_observation=local,
        )
        assert resolved.content == ()
        assert "content_capture_unavailable" in resolved.gaps
        assert any(gap.code == "captured_object_unavailable" for gap in frozen.case.gaps)
    finally:
        reopened_db.close(force=True)


@pytest.mark.anyio
async def test_disabled_native_content_never_enters_service_request(tmp_path: Path) -> None:
    profile = CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID
    (
        project,
        workspace,
        _session,
        local,
        _observation,
        _ledger,
        _runtime,
        _coordinator,
        client,
        connect,
    ) = await _pipeline(
        tmp_path,
        codex_session_id="claude:disabled-content-session",
        profile=profile,
    )
    local.disable_content_capture(workspace, profile)

    def run_async(factory: Callable[[], Awaitable[object]]) -> object:
        return asyncio.run(factory())

    payload = {
        "hook_event_name": "PostToolUse",
        "session_id": "disabled-content-session",
        "tool_name": "Bash",
        "tool_use_id": "disabled-tool-1",
        "tool_response": "must-not-be-captured",
        "exit_status": 0,
    }

    def run_hook() -> int:
        return handle_claude_observe(
            event_name="PostToolUse",
            stdin_bytes=canonical_encode(cast(CanonicalJsonValue, payload)),
            workspace=str(project),
            _state=tmp_path / "state",
            stdout=io.BytesIO(),
            connect=cast(object, connect),  # type: ignore[arg-type]
            run_async=run_async,
            observation_profile=profile,
        )

    assert await asyncio.to_thread(run_hook) == 0
    # With content capture disabled this ordinary-native row is structural-only;
    # the hook keeps it locally durable and does not open a foreground service
    # connection.
    assert client.requests == [], f"connector calls={client.connect_calls}"
    pending = local.list_pending_outbox_rows(
        workspace,
        codex_session_id="claude:disabled-content-session",
    )
    assert len(pending) == 1
    assert pending[0].envelope.event_kind == "PostToolUse"
    assert pending[0].envelope.content_object_refs == ()
