"""Successor acceptance scenarios for observation routing, supervisor, and session advice."""

from __future__ import annotations

import asyncio
import io
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from yoetz.adapters.integrations.codex_plugin import render_plugin_tree
from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.application.observation_verification import (
    CompletedApprovedCheck,
    ObservationVerificationJob,
    ObservationVerificationSupervisor,
    ObservationVerificationWorker,
    VerificationDrainHandle,
)
from yoetz.cli.observe_hooks import handle_observe
from yoetz.domain.values import Timestamp


def test_rendered_hooks_declare_workspace_binding_and_nonblocking_budgets() -> None:
    """Observe hooks bind the workspace and never block a session (#209).

    The old contract here — every observe timeout <= 3s — was unmeetable and
    got every hook SIGKILLed mid-drain. The successor contract: pure-ingress
    handlers are async (they cannot block regardless of timeout), synchronous
    advice handlers declare a meetable 10s, and SessionEnd stays inside the
    Codex host's hard 3s clamp.
    """

    tree = render_plugin_tree()
    hooks = json.loads(tree["hooks/hooks.json"].decode("utf-8"))
    observe_commands: list[tuple[str, dict[str, Any]]] = []
    for event, groups in hooks["hooks"].items():
        for group in groups:
            entries = group.get("hooks", [group] if "command" in group else [])
            for hook in entries:
                command = hook.get("command")
                if isinstance(command, str) and "hooks observe" in command:
                    observe_commands.append((event, hook))
    assert observe_commands
    for event, hook in observe_commands:
        command = hook["command"]
        assert isinstance(command, str)
        assert "--workspace ." in command
        if event == "SessionEnd":
            assert hook.get("async") is not True
            assert int(hook["timeout"]) <= 3
        elif hook.get("async") is True:
            assert int(hook["timeout"]) <= 10
        else:
            assert int(hook["timeout"]) <= 10


def test_two_consented_workspaces_bind_independent_codex_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    state = tmp_path / "state"
    project_a = tmp_path / "proj-a"
    project_b = tmp_path / "proj-b"
    project_a.mkdir()
    project_b.mkdir()
    store = LocalObservationStore(_state=state)
    commit_a = store.workspace_commitment(str(project_a.resolve()))
    commit_b = store.workspace_commitment(str(project_b.resolve()))
    store.grant_consent(commit_a)
    store.grant_consent(commit_b)
    assert commit_a != commit_b

    def _observe(workspace: Path, session: str) -> None:
        payload = json.dumps({"session_id": session, "hook_event_name": "SessionStart"}).encode(
            "utf-8"
        )
        code = handle_observe(
            event_name="SessionStart",
            stdin_bytes=payload,
            workspace=str(workspace),
            _state=state,
            skip_service=True,
            stdout=io.BytesIO(),
        )
        assert code == 0

    _observe(project_a, "codex-session-a")
    _observe(project_b, "codex-session-b")
    assert store.find_workspace_for_codex_session("codex-session-a") == commit_a
    assert store.find_workspace_for_codex_session("codex-session-b") == commit_b
    assert store.find_workspace_for_codex_session("codex-session-a") != commit_b


@pytest.mark.anyio
async def test_supervisor_drains_after_notify_without_inline_await() -> None:
    ran = {"count": 0}

    class _Repo:
        def enqueue_latest(self, **kwargs: object) -> tuple[str, ...]:
            del kwargs
            return ("job-1",)

        def claim_next(self, **kwargs: object) -> object | None:
            del kwargs
            return None

        def complete(self, **kwargs: object) -> None:
            del kwargs

    class _Runner:
        def __init__(self) -> None:
            self._output_sink = None

    worker = ObservationVerificationWorker(
        repository=_Repo(),  # type: ignore[arg-type]
        runner=_Runner(),  # type: ignore[arg-type]
        workspace_provider=lambda _w: object(),  # type: ignore[arg-type,return-value]
        policy_provider=lambda _w, _d: (),
        capture_subject_state=lambda _h: "sha256:" + "d" * 64,
        persist_output=lambda _job, _content: asyncio.sleep(0, result=None),
        service_generation=1,
        lease_owner="svc",
        now=lambda: Timestamp("2026-07-24T00:00:00.000Z").wire,
        lease_expires_at=lambda: Timestamp("2026-07-24T00:02:00.000Z").wire,
    )

    async def _run_once() -> object | None:
        ran["count"] += 1
        return None

    worker.run_once = _run_once  # type: ignore[method-assign]
    supervisor = ObservationVerificationSupervisor(service_generation=1)
    idle = {"count": 0}

    async def _on_idle() -> None:
        idle["count"] += 1

    await supervisor.start()
    supervisor.register(
        VerificationDrainHandle(
            workspace_commitment="hmac-sha256:" + "a" * 64,
            worker=worker,
            on_idle=_on_idle,
        )
    )
    supervisor.notify()
    await asyncio.sleep(0.05)
    assert ran["count"] >= 1
    assert idle["count"] == 1
    assert supervisor.has_handle("hmac-sha256:" + "a" * 64) is False
    await supervisor.stop()


@pytest.mark.anyio
async def test_verification_supervisor_preserves_wake_from_idle_handoff() -> None:
    workspace = "hmac-sha256:" + "b" * 64
    supervisor = ObservationVerificationSupervisor(service_generation=1)
    successor_ran = asyncio.Event()

    class _Worker:
        service_generation = 1

        def __init__(self, ran: asyncio.Event | None = None) -> None:
            self._ran = ran

        async def run_once(self) -> None:
            if self._ran is not None:
                self._ran.set()
            return None

    async def _register_successor() -> None:
        supervisor.register(
            VerificationDrainHandle(
                workspace_commitment=workspace,
                worker=_Worker(successor_ran),  # type: ignore[arg-type]
            )
        )

    await supervisor.start()
    supervisor.register(
        VerificationDrainHandle(
            workspace_commitment=workspace,
            worker=_Worker(),  # type: ignore[arg-type]
            on_idle=_register_successor,
        )
    )
    await asyncio.wait_for(successor_ran.wait(), timeout=0.25)
    await supervisor.stop()


@pytest.mark.anyio
async def test_completed_approved_check_forwards_bounded_result_for_ledger_materialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yoetz.adapters.approved_checks import (
        ApprovedCheckApproval,
        ApprovedCheckOutcome,
        ApprovedCheckResult,
        ApprovedCheckStatus,
        approval_commitment,
    )
    from yoetz.application import observation_verification as verification_module

    state = "sha256:" + "d" * 64
    approval_digest = approval_commitment("focused-tests", ("/usr/bin/true",), allow_network=False)
    result = ApprovedCheckResult(
        status=ApprovedCheckStatus.PASSED,
        outcome=ApprovedCheckOutcome.SUCCESS,
        exit_status=0,
        output_digest="sha256:" + "e" * 64,
        output_bytes=14,
        subject_state_digest=state,
        approval_commitment=approval_digest,
        result_digest="sha256:" + "f" * 64,
        duration_ms=2,
    )
    approval = ApprovedCheckApproval(
        approval_id="focused-tests",
        argv=("/usr/bin/true",),
        allow_network=False,
        timeout_seconds=10.0,
        approval_commitment=approval_digest,
    )
    job = ObservationVerificationJob(
        job_id="job-1",
        workspace_commitment="hmac-sha256:" + "a" * 64,
        policy_digest="sha256:" + "b" * 64,
        approval_commitment=approval_digest,
        subject_state_digest=state,
        state_token=1,
    )
    order: list[str] = []

    class _Repo:
        def claim_next(self, **_kwargs: object) -> ObservationVerificationJob | None:
            return job

        def complete(self, **kwargs: object) -> None:
            order.append("complete")
            assert kwargs["result"] == result
            assert kwargs["output_object_id"] == "obj_00000000-0000-4000-8000-000000000001"

    class _Runner:
        _output_sink: object | None = None

    def _run(**kwargs: object):
        runner = kwargs["runner"]
        sink = getattr(runner, "_output_sink")
        assert callable(sink)
        sink(b"bounded output")
        return result, None

    monkeypatch.setattr(verification_module, "run_bound_approved_check", _run)
    persisted: list[bytes] = []
    materialized: list[CompletedApprovedCheck] = []

    async def _persist(_job: ObservationVerificationJob, content: bytes) -> str:
        persisted.append(content)
        return "obj_00000000-0000-4000-8000-000000000001"

    async def _materialize(completed: CompletedApprovedCheck) -> None:
        order.append("materialize")
        materialized.append(completed)

    worker = ObservationVerificationWorker(
        repository=_Repo(),  # type: ignore[arg-type]
        runner=_Runner(),  # type: ignore[arg-type]
        workspace_provider=lambda _value: object(),  # type: ignore[arg-type,return-value]
        policy_provider=lambda _workspace, _policy: (approval,),
        capture_subject_state=lambda _workspace: state,
        persist_output=_persist,
        service_generation=1,
        lease_owner="service",
        now=lambda: "2026-08-09T00:00:00.000Z",
        lease_expires_at=lambda: "2026-08-09T00:02:00.000Z",
        materialize_result=_materialize,
    )
    assert await worker.run_once() == job
    assert persisted == [b"bounded output"]
    assert len(materialized) == 1
    completed = materialized[0]
    assert completed.approval_id == "focused-tests"
    assert completed.result == result
    assert completed.output_object_id == "obj_00000000-0000-4000-8000-000000000001"
    assert completed.is_current is True
    assert order == ["materialize", "complete"]


@pytest.mark.anyio
async def test_approved_check_materialization_is_service_owned_and_idempotent(
    tmp_path: Path,
) -> None:
    from typing import cast

    from builders.ledger_adapters import (
        FixedClock,
        FixedIds,
        MemoryObjects,
        append_command,
        memory_adapter,
        ownership_fence,
    )
    from yoetz.adapters.approved_checks import (
        ApprovedCheckOutcome,
        ApprovedCheckResult,
        ApprovedCheckStatus,
        approval_commitment,
    )
    from yoetz.application.observation_coordinator import ObservationCoordinator
    from yoetz.application.observation_materialize import observation_writer_id
    from yoetz.domain.events import (
        AcceptedEvent,
        EvidenceDigestProvenance,
        EvidenceRecordedPayload,
    )
    from yoetz.ports.diagnostics import RuntimeCapability
    from yoetz.ports.importer import ImporterPort
    from yoetz.ports.objects import ObjectStorePort
    from yoetz.ports.runtime import BundleRuntimePort, TaskRuntime
    from yoetz.protocol.coverage import PublicationChannel

    seed = append_command()
    ledger = memory_adapter(seed)
    objects = cast(MemoryObjects, ledger._objects)  # pyright: ignore[reportPrivateUsage]
    runtime = TaskRuntime(
        seed.task_id,
        seed.session_id,
        seed.writer_id,
        frozenset({RuntimeCapability.WRITE}),
        ledger,
        cast(ObjectStorePort, objects),
        cast(ImporterPort, object()),
        "0.1.0",
        "0.1.0",
        "0.1",
        "1.0.0",
        ownership_fence(),
    )
    coordinator = ObservationCoordinator(
        runtime=cast(BundleRuntimePort, object()),
        local=LocalObservationStore(_state=tmp_path),
        clock=FixedClock(),
        ids=FixedIds(),
        state_root=tmp_path,
    )
    approval_digest = approval_commitment("focused-tests", ("/usr/bin/true",), allow_network=False)
    result = ApprovedCheckResult(
        status=ApprovedCheckStatus.PASSED,
        outcome=ApprovedCheckOutcome.SUCCESS,
        exit_status=0,
        output_digest="sha256:" + "e" * 64,
        output_bytes=14,
        subject_state_digest="sha256:" + "d" * 64,
        approval_commitment=approval_digest,
        result_digest="sha256:" + "f" * 64,
        duration_ms=2,
    )
    completed = CompletedApprovedCheck(
        job=ObservationVerificationJob(
            job_id="job-materialize-1",
            workspace_commitment="hmac-sha256:" + "a" * 64,
            policy_digest="sha256:" + "b" * 64,
            approval_commitment=approval_digest,
            subject_state_digest="sha256:" + "d" * 64,
            state_token=1,
        ),
        approval_id="focused-tests",
        result=result,
        subject_state_after="sha256:" + "d" * 64,
        output_object_id=None,
        is_current=True,
        recorded_at="2026-08-09T00:00:00.000Z",
    )

    await coordinator._materialize_approved_check(runtime, completed)  # pyright: ignore[reportPrivateUsage]
    object_count = len(objects._data)  # pyright: ignore[reportPrivateUsage]
    harness_runtime = replace(
        runtime,
        writer_id=observation_writer_id(runtime.task_id, runtime.session_id),
    )
    await coordinator._materialize_approved_check(  # pyright: ignore[reportPrivateUsage]
        harness_runtime,
        completed,
        legacy_writer_id=runtime.writer_id,
    )
    assert len(objects._data) == object_count  # pyright: ignore[reportPrivateUsage]

    records = [row async for row in ledger.load_events(seed.session_id)]
    assert [row.schema.name for row in records] == [
        "action_recorded",
        "evidence_recorded",
        "result_recorded",
    ]
    evidence = records[1]
    assert type(evidence) is AcceptedEvent
    assert evidence.publication_channel is PublicationChannel.ENGINE_DERIVED
    assert evidence.author.actor_type.value == "yoetz_engine"
    assert type(evidence.payload) is EvidenceRecordedPayload
    assert evidence.payload.digest_binding is not None
    assert evidence.payload.digest_binding.provenance is EvidenceDigestProvenance.APPROVED_CHECK
    assert evidence.payload.digest_binding.approval_commitment == approval_digest
    assert evidence.payload.digest_binding.approved_check_result_digest == result.result_digest


@pytest.mark.anyio
async def test_advice_finding_materialization_passes_real_ledger_validation(tmp_path: Path) -> None:
    """The advice append must survive the adapter's full event preimage validation."""

    from typing import cast

    from builders.ledger_adapters import (
        FixedClock,
        FixedIds,
        MemoryObjects,
        append_command,
        memory_adapter,
        ownership_fence,
    )
    from yoetz.application.observation_coordinator import ObservationCoordinator
    from yoetz.application.observation_materialize import materialize_observation_envelope
    from yoetz.domain.events import AcceptedEvent
    from yoetz.domain.findings import Finding
    from yoetz.domain.observation import (
        AdviceItem,
        AdviceSnapshot,
        ObservationCursor,
        ObservationEnvelope,
        ObservationSource,
    )
    from yoetz.domain.values import JsonObject, finding_id
    from yoetz.ports.diagnostics import RuntimeCapability
    from yoetz.ports.importer import ImporterPort
    from yoetz.ports.objects import ObjectStorePort
    from yoetz.ports.runtime import BundleRuntimePort, TaskRuntime
    from yoetz.protocol.coverage import PublicationChannel, coverage_for_channel, weakest
    from yoetz.protocol.ids import PREFIX_BY_KIND, IdKind

    seed = append_command()
    ledger = memory_adapter(seed)
    await ledger.append_batch(seed)
    objects = cast(MemoryObjects, ledger._objects)  # pyright: ignore[reportPrivateUsage]
    runtime = TaskRuntime(
        seed.task_id,
        seed.session_id,
        seed.writer_id,
        frozenset({RuntimeCapability.WRITE}),
        ledger,
        cast(ObjectStorePort, objects),
        cast(ImporterPort, object()),
        "0.1.0",
        "0.1.0",
        "0.1",
        "1.0.0",
        ownership_fence(),
    )
    coordinator = ObservationCoordinator(
        runtime=cast(BundleRuntimePort, object()),
        local=LocalObservationStore(_state=tmp_path),
        clock=FixedClock(),
        ids=FixedIds(),
        state_root=tmp_path,
    )
    mixed_coverage = weakest(
        coverage_for_channel(PublicationChannel.HOOK_OBSERVED),
        coverage_for_channel(PublicationChannel.ENGINE_DERIVED),
    )
    item = AdviceItem(
        finding_id(PREFIX_BY_KIND[IdKind.FINDING] + "00000000-0000-4000-8000-000000000010"),
        "failed_command_unresolved",
        1,
        "A failed command remains unresolved.",
        "Resolve the failure and rerun the check.",
        "rerun_check",
        (),
        mixed_coverage,
        "frontier-ledger-validation",
    )
    snapshot = AdviceSnapshot(
        (item.finding_id,),
        "sha256:" + "a" * 64,
        mixed_coverage,
        "rerun_check",
        "frontier-ledger-validation",
        "advice-ledger-validation-1",
        (item,),
    )
    envelope = ObservationEnvelope(
        session_commitment="hmac-sha256:" + "b" * 64,
        event_kind="PostToolUse",
        source_identity="hook:advice-ledger-validation",
        source=ObservationSource.CODEX_HOOK,
        cursor=ObservationCursor(
            1,
            0,
            1,
            "hmac-sha256:" + "c" * 64,
            "codex-obs-hook/1.0.0",
        ),
        receipt_time=Timestamp("2026-08-12T00:00:00.000Z"),
        structural_payload=JsonObject(
            {
                "tool_name": "shell",
                "tool_call_id": "advice-ledger-validation",
                "correlation_id": "advice-ledger-validation",
                "exit_status": 2,
            }
        ),
        content_object_refs=(),
        gap_codes=(),
    )

    await coordinator._materialize_advice_findings(  # pyright: ignore[reportPrivateUsage]
        runtime, (envelope,), snapshot
    )

    records = [row async for row in ledger.load_events(seed.session_id)]
    assert len(records) == 2
    accepted = records[1]
    assert type(accepted) is AcceptedEvent
    assert accepted.schema.name == "finding_recorded"
    assert type(accepted.payload) is Finding
    assert tuple(str(ref) for ref in accepted.payload.subject_refs) == (str(records[0].event_id),)
    assert accepted.publication_channel is PublicationChannel.ENGINE_DERIVED
    assert accepted.coverage == coverage_for_channel(PublicationChannel.ENGINE_DERIVED)
    assert accepted.payload.coverage == mixed_coverage

    revision_envelope = replace(
        envelope,
        source_identity="hook:advice-ledger-revision",
        cursor=replace(envelope.cursor, event_position=2),
        structural_payload=JsonObject(
            {
                "tool_name": "shell",
                "tool_call_id": "advice-ledger-revision",
                "correlation_id": "advice-ledger-revision",
                "exit_status": 2,
            }
        ),
    )
    await coordinator._append_materialized(  # pyright: ignore[reportPrivateUsage]
        runtime,
        revision_envelope,
        materialize_observation_envelope(revision_envelope, task_id=seed.task_id),
    )
    before_repeat = [row async for row in ledger.load_events(seed.session_id)]
    revised_item = replace(item, evidence_refs=(revision_envelope.source_identity,))
    await coordinator._materialize_advice_findings(  # pyright: ignore[reportPrivateUsage]
        runtime,
        (envelope, revision_envelope),
        replace(
            snapshot,
            ranked_items=(revised_item,),
            evidence_basis_digest="sha256:" + "d" * 64,
            suppression_identity="advice-ledger-validation-2",
        ),
    )
    after_repeat = [row async for row in ledger.load_events(seed.session_id)]

    assert len(after_repeat) == len(before_repeat)
    assert sum(row.schema.name == "finding_recorded" for row in after_repeat) == 1


def test_untrusted_workspace_dot_does_not_bind_without_consent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    state = tmp_path / "obs-state"
    store = LocalObservationStore(_state=state)
    payload = json.dumps({"session_id": "fresh-session", "hook_event_name": "SessionStart"}).encode(
        "utf-8"
    )
    code = handle_observe(
        event_name="SessionStart",
        stdin_bytes=payload,
        workspace=".",
        _state=state,
        skip_service=True,
        stdout=io.BytesIO(),
    )
    assert code == 0
    assert store.find_workspace_for_codex_session("fresh-session") is None


def test_sqlite_session_advice_is_isolated_and_latest_anywhere_is_gone() -> None:
    """Migration-0004 session advice must not leak across Yoetz sessions."""

    import apsw

    from yoetz.adapters.sqlite.migrations import initialize_bundle
    from yoetz.adapters.sqlite.observation import SqliteObservationStore
    from yoetz.domain.findings import FindingId, finding_id
    from yoetz.domain.observation import AdviceSnapshot
    from yoetz.protocol.coverage import (
        ArtifactObservation,
        AuthorshipAssurance,
        CheckType,
        Coverage,
        EvidenceImmutability,
        LedgerFreshness,
        PublicationChannel,
    )
    from yoetz.protocol.ids import PREFIX_BY_KIND, IdKind

    db = apsw.Connection(":memory:")
    initialize_bundle(db, {"task_id": "task_session_advice", "owner_generation": "1"})
    store = SqliteObservationStore(db)
    workspace = "hmac-sha256:" + "c" * 64
    now = Timestamp("2026-07-24T00:00:00.000Z")
    coverage = Coverage(
        publication_channels=(PublicationChannel.ENGINE_DERIVED,),
        authorship_assurance=AuthorshipAssurance.SERVICE_AUTHENTICATED,
        artifact_observation=ArtifactObservation.PUBLISHED_ONLY,
        evidence_immutability=EvidenceImmutability.CONTENT_DIGEST,
        ledger_freshness=LedgerFreshness.CURRENT,
        check_types=(CheckType.DETERMINISTIC,),
        known_gaps=(),
    )
    finding: FindingId = finding_id(
        PREFIX_BY_KIND[IdKind.FINDING] + "00000000-0000-4000-8000-000000000001"
    )

    def _snap(token: str) -> AdviceSnapshot:
        return AdviceSnapshot(
            ranked_finding_ids=(finding,),
            ranked_items=(),
            recommended_next_action="reground_status",
            evidence_basis_digest="sha256:" + (token * 64)[:64],
            confidence_coverage=coverage,
            freshness_frontier=f"frontier-{token}",
            suppression_identity=f"suppress-{token}",
        )

    store.set_session_advice_snapshot(
        workspace=workspace,
        yoetz_session_id="session-a",
        snapshot=_snap("a"),
        updated_at=now,
    )
    store.set_session_advice_snapshot(
        workspace=workspace,
        yoetz_session_id="session-b",
        snapshot=_snap("b"),
        updated_at=now,
    )
    a = store.load_advice_snapshot_for_session(workspace=workspace, yoetz_session_id="session-a")
    b = store.load_advice_snapshot_for_session(workspace=workspace, yoetz_session_id="session-b")
    assert a is not None and b is not None
    assert a.suppression_identity != b.suppression_identity
    assert store.load_latest_advice_snapshot() is None


def test_unknown_hook_event_becomes_unsupported_without_guessing_success() -> None:
    from yoetz.cli.observe_hooks import map_hook_payload_to_envelope
    from yoetz.domain.observation import ObservationGapCode
    from yoetz.domain.values import JsonObject

    payload = JsonObject(
        {
            "session_id": "future-session",
            "hook_event_name": "FutureCodexEvent_v9",
            "visible_text": "hello-future",
        }
    )
    envelope = map_hook_payload_to_envelope(
        event_name="FutureCodexEvent_v9",
        payload=payload,
        session_commitment="hmac-sha256:" + "a" * 64,
        event_ordinal=1,
        key_material=b"k" * 32,
    )
    assert ObservationGapCode.UNSUPPORTED_EVENT.value in envelope.gap_codes
    assert envelope.structural_payload.get("hook_name") == "unsupported"
