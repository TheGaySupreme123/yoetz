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

from yoetz.adapters.integrations.codex_lifecycle import LifecycleMapping, store_mapping
from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.adapters.objects.encrypted_files import EncryptedFilesObjectStore
from yoetz.adapters.sqlite.migrations import initialize_bundle
from yoetz.adapters.sqlite.observation import SqliteObservationStore
from yoetz.adapters.sqlite.repository import SqliteLedger
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
from yoetz.domain.privacy import ReviewContextProfile, ReviewSelectionPolicy
from yoetz.domain.values import JsonValue as DomainJsonValue
from yoetz.domain.values import Timestamp
from yoetz.ports.clock import ClockPort
from yoetz.ports.ids import IdPort
from yoetz.ports.importer import ImporterPort
from yoetz.ports.keys import BundleKeys
from yoetz.ports.ledger import FrozenCase
from yoetz.ports.objects import ObjectKind, ObjectRootSnapshot, ObjectStorePort
from yoetz.ports.runtime import (
    BundleRuntimePort,
    OwnershipFence,
    RouteCommand,
    RuntimeCapability,
    StartCompletionEvidence,
    TaskRuntime,
)
from yoetz.protocol.canonical import JsonValue as CanonicalJsonValue
from yoetz.protocol.canonical import canonical_encode
from yoetz.protocol.ids import PREFIX_BY_KIND, IdKind

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
    def __init__(self) -> None:
        self._object_counter = 16

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


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("host", "profile", "event_name", "payload", "marker", "content_kind"),
    (
        (
            "claude",
            CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID,
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
            ObservationContentKind.TOOL_OUTPUT,
        ),
        (
            "cursor",
            CURSOR_ORDINARY_OBSERVATION_PROFILE_ID,
            "afterFileEdit",
            {
                "hook_event_name": "afterFileEdit",
                "conversation_id": "cursor-capture-session",
                "tool_use_id": "cursor-tool-1",
                "tool_name": "edit",
                "file_path": "src/planted.py",
                "file_content": "planted-cursor-code-marker: missing validation\n",
                "workspace_roots": (),
            },
            b"planted-cursor-code-marker: missing validation\n",
            ObservationContentKind.CHANGED_FILE,
        ),
    ),
    ids=("claude-tool-output", "cursor-changed-file"),
)
async def test_ordinary_native_hook_content_reaches_prepared_semantic_packet(
    tmp_path: Path,
    host: str,
    profile: str,
    event_name: str,
    payload: Mapping[str, object],
    marker: bytes,
    content_kind: ObservationContentKind,
) -> None:
    codex_session_id = f"{host}:{payload.get('session_id') or payload['conversation_id']}"
    (
        project,
        workspace,
        session_commitment,
        _local,
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

    def run_hook() -> int:
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

    assert await asyncio.to_thread(run_hook) == 0
    assert len(client.requests) == 1, f"connector calls={client.connect_calls}"
    request = client.requests[0]
    assert request.codex_session_id == codex_session_id
    assert request.content_capture_profile == profile
    assert len(request.content_chunks) == 1
    assert request.content_chunks[0].content_kind is content_kind
    assert request.content_chunks[0].content == marker

    envelopes = task_observation.list_envelopes_for_session(workspace, session_commitment)
    assert len(envelopes) == 1, (
        f"session={session_commitment!r} all="
        f"{[(item.session_commitment, item.source_identity) for item in task_observation.list_envelopes(workspace)]!r}"
    )
    envelope = envelopes[0]
    assert envelope.source_identity == request.envelope.source_identity
    assert envelope.content_object_refs
    assert envelope.gap_codes == ("unpaired_event",)
    manifest = task_observation.load_content_manifest(envelope.content_object_refs[0])
    assert manifest is not None
    assert manifest.content_kind is content_kind
    content_digest = manifest.content_digest
    assert content_digest == "sha256:" + hashlib.sha256(marker).hexdigest()
    assert content_digest is not None
    assert manifest.content_bytes == len(marker)

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
    )
    assert resolved.gaps == ()
    assert len(resolved.content) == 1
    captured = resolved.content[0]
    assert captured.content == marker
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
    excerpt = next(
        item
        for item in semantic.packet.targeted_excerpts
        if item.content_digest == "sha256:" + hashlib.sha256(marker).hexdigest()
    )
    assert excerpt.content_visibility == "available"
    prepared = semantic_case_to_prepared_payload(
        semantic,
        {item.item_id for item in semantic.items},
    )
    # JSON canonicalization escapes a source newline, so inspect the semantic bytes after
    # decoding that transport representation rather than matching the escaped wire spelling.
    assert marker.decode("utf-8") in prepared.decode("utf-8").replace("\\n", "\n")
    assert content_digest.encode("ascii") in prepared


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
