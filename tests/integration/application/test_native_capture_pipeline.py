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
from yoetz.adapters.integrations.codex_lifecycle import LifecycleMapping, store_mapping
from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.adapters.objects.encrypted_files import EncryptedFilesObjectStore
from yoetz.adapters.sqlite import connection as sqlite_connection
from yoetz.adapters.sqlite.migrations import initialize_bundle
from yoetz.adapters.sqlite.observation import SqliteObservationStore
from yoetz.adapters.sqlite.repository import SqliteLedger
from yoetz.application.check import FinalSemanticEvaluation
from yoetz.application.egress import PrivacyCoordinator
from yoetz.application.observation_coordinator import ObservationCoordinator
from yoetz.application.semantic_case import (
    build_semantic_case,
    semantic_case_to_prepared_payload,
)
from yoetz.application.semantic_content import resolve_captured_semantic_content
from yoetz.cli.observe_hooks import handle_claude_observe, handle_cursor_observe
from yoetz.domain.observation import (
    ObservationContentKind,
    ObservationIngestRequest,
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
    profile: str,
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
    local.enable_content_capture(workspace, profile)
    session_commitment = local.bind_codex_session(workspace, codex_session_id)

    task_id = _ids(IdKind.TASK, 1)
    yoetz_session_id = _ids(IdKind.SESSION, 2)
    writer_id = _ids(IdKind.WRITER, 3)
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
        observation=observation,
    )
    router = _RuntimeRouter(runtime)
    coordinator = ObservationCoordinator(
        runtime=router,
        local=local,
        clock=_Clock(),
        ids=ids,
        state_root=state,
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
        observation=ledger.open_observation_store(),
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
            observation_profile=profile,
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
    assert len(client.requests) == 2
    assert client.requests[1].content_chunks[0].content == marker

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
        semantic_non_dispatch.FixedClock(),
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

    assert await asyncio.to_thread(run_hook, pre_event_name, pre_payload) == 0
    assert await asyncio.to_thread(run_hook, post_event_name, post_payload) == 0
    assert len(client.requests) == 2, f"connector calls={client.connect_calls}"
    pre_request, request = client.requests
    assert pre_request.codex_session_id == codex_session_id
    assert pre_request.content_capture_profile == profile
    assert pre_request.content_chunks == ()
    assert request.codex_session_id == codex_session_id
    assert request.content_capture_profile == profile
    assert len(request.content_chunks) == 1
    assert request.content_chunks[0].content_kind is content_kind
    assert request.content_chunks[0].content == captured_bytes
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
        item for item in envelopes if item.source_identity == request.envelope.source_identity
    )
    assert pre_envelope.content_object_refs == ()
    assert pre_envelope.gap_codes == ()
    assert envelope.source_identity == request.envelope.source_identity
    assert envelope.content_object_refs
    assert envelope.gap_codes == ()
    manifest = task_observation.load_content_manifest(envelope.content_object_refs[0])
    assert manifest is not None
    assert manifest.content_kind is content_kind
    content_digest = manifest.content_digest
    assert content_digest == "sha256:" + hashlib.sha256(captured_bytes).hexdigest()
    assert content_digest is not None
    assert manifest.content_bytes == len(captured_bytes)

    # The service's routed-session table is the resolver's exact host/session fence.  Production
    # verification workers record it when a workspace locator is available; this small in-process
    # harness records the same service-owned route explicitly because it does not run verification.
    task_observation.record_workspace_session_route(
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
    assert len(client.requests) == 2
    envelopes = observation.list_envelopes(workspace)
    post = next(item for item in envelopes if item.event_kind == "PostToolUse")
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
    assert len(client.requests) == 1, f"connector calls={client.connect_calls}"
    assert client.requests[0].content_capture_profile is None
    assert client.requests[0].content_chunks == ()
