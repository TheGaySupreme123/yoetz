from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast

import pytest

from builders.ledger_adapters import FixedClock, MemoryObjects
from builders.policy_cases import FRONTIER, clm, make_case, record
from yoetz.application.check import FinalSemanticEvaluation, execute_check, execute_check_commit
from yoetz.application.service import VerificationPolicy
from yoetz.domain.events import ClaimKind, ClaimRecordedPayload
from yoetz.domain.findings import Finding, RankedFindings, SemanticProvenance
from yoetz.domain.values import Frontier
from yoetz.ports.diagnostics import RuntimeCapability
from yoetz.ports.ids import IdPort
from yoetz.ports.ledger import (
    CheckCommitResult,
    CheckPhase,
    CheckPolicyExecution,
    CheckVersionSlice,
    FrozenCase,
    OperationKind,
    OperationLease,
    OperationRecord,
    OperationState,
)
from yoetz.ports.objects import ObjectKind, ObjectMetadata, ObjectRef
from yoetz.ports.runtime import BundleRuntimePort, OwnershipFence, RouteCommand, TaskRuntime
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError
from yoetz.protocol.ids import IdKind
from yoetz.protocol.models import CheckRequest, SemanticReason, SemanticStatus

_TASK = "tsk_30000000-0000-4000-8000-000000000001"
_SESSION = "ses_30000000-0000-4000-8000-000000000001"
_WRITER = "wri_30000000-0000-4000-8000-000000000001"
_REQUEST = "req_30000000-0000-4000-8000-000000000001"


class _Ids:
    def __init__(self) -> None:
        self.count = 0
        self.object_count = 0

    def new(self, kind: IdKind) -> str:
        if kind is IdKind.FINDING:
            self.count += 1
            return f"fnd_30000000-0000-4000-8000-{self.count:012x}"
        assert kind is IdKind.OBJECT
        self.object_count += 1
        return f"obj_30000000-0000-4000-8000-{self.object_count:012x}"


def _case() -> FrozenCase:
    claim = ClaimRecordedPayload(clm(1), ClaimKind.MATERIAL, "Unsupported", ())
    deterministic = make_case(claims={clm(1): record(claim, 1)})
    return FrozenCase(
        deterministic,
        OperationLease(
            _WRITER,
            _REQUEST,
            _SESSION,
            CheckPhase.RESERVED,
            "owner-generation-1",
            "lease-owner-1",
            1,
            datetime(2030, 1, 1, tzinfo=UTC),
            FRONTIER,
            "sha256:" + "d" * 64,
        ),
    )


class _Ledger:
    def __init__(self, frozen: FrozenCase) -> None:
        self.frozen = frozen
        self.replay: CheckCommitResult | None = None
        self.failure: BaseException | None = None
        self.commit_count = 0
        self.phase_transitions: list[tuple[CheckPhase, CheckPhase]] = []
        self.last_ranked: RankedFindings | None = None
        self.last_executions: tuple[CheckPolicyExecution, ...] | None = None
        self.operation: OperationRecord | None = None

    async def freeze_case(self, *args: object) -> FrozenCase | CheckCommitResult:
        if self.failure is not None:
            raise self.failure
        if self.operation is None:
            lease = self.frozen.lease
            resume = ObjectRef(
                "obj_30000000-0000-4000-8000-00000000aaaa",
                1,
                "hmac-sha256:" + "a" * 64,
                "sha256:" + "b" * 64,
                "yoetz-object/1",
                "bmk-1",
                ObjectMetadata(
                    ObjectKind.CHECK_RESUME,
                    "application/vnd.yoetz.check-resume+json",
                    _TASK,
                    datetime(2026, 1, 1, tzinfo=UTC),
                ),
            )
            self.operation = OperationRecord(
                _WRITER,
                _REQUEST,
                OperationKind.CHECK,
                cast(str, args[4]),
                OperationState.PENDING,
                CheckPhase.RESERVED,
                lease.owner_generation,
                lease.lease_owner_id,
                lease.lease_generation,
                lease.lease_expires_at,
                resume,
                None,
                None,
                None,
                None,
                None,
            )
        return self.frozen if self.replay is None else self.replay

    async def lookup_operation(self, writer_id: str, operation_id: str) -> OperationRecord | None:
        assert (writer_id, operation_id) == (_WRITER, _REQUEST)
        return self.operation

    async def advance_check_phase(
        self,
        lease: OperationLease,
        expected_phase: CheckPhase,
        next_phase: CheckPhase,
        durable_object_ref: object = None,
    ) -> OperationLease:
        assert lease == self.frozen.lease
        assert lease.phase is expected_phase
        assert (durable_object_ref is not None) == (expected_phase is CheckPhase.RESERVED)
        replacement = replace(
            lease,
            phase=next_phase,
            lease_generation=lease.lease_generation + 1,
        )
        self.frozen = FrozenCase(self.frozen.case, replacement)
        assert self.operation is not None
        self.operation = replace(
            self.operation,
            phase=next_phase,
            resume_object_ref=(
                cast(ObjectRef, durable_object_ref)
                if durable_object_ref is not None
                else self.operation.resume_object_ref
            ),
            lease_generation=replacement.lease_generation,
        )
        self.phase_transitions.append((expected_phase, next_phase))
        return replacement

    async def commit_check_if_current(
        self,
        frozen: FrozenCase,
        ranked: RankedFindings,
        executions: tuple[CheckPolicyExecution, ...],
        semantic_status: SemanticStatus,
        semantic_reason: SemanticReason,
        semantic_provenance: SemanticProvenance | None,
        request_id: str,
    ) -> CheckCommitResult:
        assert frozen == self.frozen
        self.commit_count += 1
        self.last_ranked = ranked
        self.last_executions = executions
        return CheckCommitResult(
            "committed",
            _TASK,
            _SESSION,
            _WRITER,
            request_id,
            frozen.case.frontier,
            Frontier(frozen.case.frontier.sequence + 1, "sha256:" + "e" * 64),
            ranked.verdict,
            ranked.findings,
            ranked.suppressed_count,
            executions,
            semantic_status,
            semantic_reason,
            semantic_provenance,
            ranked.coverage,
            CheckVersionSlice(
                "0.1",
                "0.1.0",
                "0.1.0",
                ("research-evidence/0.1.0", "work-integrity/0.1.0"),
            ),
        )


class _Runtime:
    def __init__(self, task: TaskRuntime) -> None:
        self.task = task
        self.release_count = 0
        self.last_command: RouteCommand | None = None

    async def route(self, command: RouteCommand) -> TaskRuntime:
        self.last_command = command
        return self.task

    async def release(self, runtime: TaskRuntime) -> None:
        assert runtime is self.task
        self.release_count += 1


class _App:
    def __init__(self, *, semantic: bool = False, crash_semantic: bool = False) -> None:
        self.id_source = _Ids()
        self.ids: IdPort = self.id_source
        self.clock = FixedClock()
        self.verification_policy = VerificationPolicy()
        self.ledger = _Ledger(_case())
        self.crash_semantic = crash_semantic
        capabilities = {
            RuntimeCapability.WRITE,
            RuntimeCapability.PAYLOAD_READ,
        }
        if semantic:
            capabilities.add(RuntimeCapability.SEMANTIC)
        task = TaskRuntime(
            _TASK,
            _SESSION,
            _WRITER,
            frozenset(capabilities),
            cast(object, self.ledger),  # pyright: ignore[reportArgumentType]
            MemoryObjects(self.id_source),  # pyright: ignore[reportArgumentType]
            object(),  # pyright: ignore[reportArgumentType]
            "0.1.0",
            "0.1.0",
            "0.1",
            "1",
            OwnershipFence(
                "svc_30000000-0000-4000-8000-000000000001",
                1,
                1,
                "0123456789abcdef",
            ),
        )
        self.runtime = cast(BundleRuntimePort, _Runtime(task))
        self.semantic_result = FinalSemanticEvaluation(
            SemanticStatus.NOT_CONFIGURED,
            SemanticReason.PROVIDER_NOT_CONFIGURED,
        )

    async def evaluate_semantic_check(
        self,
        frozen: FrozenCase,
        deterministic_findings: tuple[Finding, ...],
    ) -> FinalSemanticEvaluation:
        _ = (frozen, deterministic_findings)
        if self.crash_semantic:
            raise RuntimeError("semantic_evaluator_crashed")
        return self.semantic_result


def _request(mode: str = "deterministic_only") -> CheckRequest:
    return CheckRequest.model_validate(
        {
            "protocol_version": "0.1",
            "schema_version": "1.0.0",
            "request_id": _REQUEST,
            "session_id": _SESSION,
            "writer_id": _WRITER,
            "expected_frontier": {
                "sequence": str(FRONTIER.sequence),
                "head_digest": FRONTIER.head_digest,
            },
            "mode": mode,
            "max_findings": "1",
            "actor": {"actor_id": "harness:test", "actor_type": "harness"},
            "client": {
                "kind": "test_client",
                "version": "0.1.0",
                "integration": "local_cli",
            },
        }
    )


@pytest.mark.anyio
async def test_deterministic_check_freezes_ranks_commits_and_releases() -> None:
    app = _App()

    result = await execute_check(app, _request())

    assert result.verdict.value == "action_required"
    assert len(result.findings) == 1
    assert result.semantic_status is SemanticStatus.NOT_REQUESTED
    assert result.semantic_reason is SemanticReason.DETERMINISTIC_MODE
    assert not hasattr(app, "finalize_check_result")
    assert app.ledger.commit_count == 1
    assert app.ledger.phase_transitions == [
        (CheckPhase.RESERVED, CheckPhase.LOCAL_READY),
        (CheckPhase.LOCAL_READY, CheckPhase.READY_TO_FINALIZE),
    ]
    assert cast(_Runtime, app.runtime).release_count == 1


@pytest.mark.anyio
async def test_semantic_required_unavailable_preserves_deterministic_truth() -> None:
    app = _App(semantic=True)

    result = await execute_check_commit(app, _request("semantic_required"))

    assert result.verdict.value == "incomplete_check"
    assert result.findings
    assert result.semantic_status is SemanticStatus.NOT_CONFIGURED
    assert result.semantic_provenance is None
    assert result.coverage.known_gaps == ("semantic_review_not_configured",)
    runtime = cast(_Runtime, app.runtime)
    assert runtime.last_command is not None
    assert RuntimeCapability.SEMANTIC in runtime.last_command.required_capabilities


@pytest.mark.anyio
async def test_semantic_evaluator_crash_degrades_to_not_run_without_false_clean() -> None:
    """Requirement: evaluator crash/timeout degrades to not-run disclosure, never false clean."""

    from yoetz.domain.receipts import (
        SEMANTIC_RELEVANCE_REVIEW_NOT_RUN_GAP,
        SEMANTIC_REVIEW_NOT_CONFIGURED_GAP,
    )

    crashed = _App(semantic=True, crash_semantic=True)
    crash_result = await execute_check_commit(crashed, _request("semantic_if_configured"))
    assert crash_result.findings
    assert crash_result.semantic_status is SemanticStatus.FAILED
    assert crash_result.semantic_reason is SemanticReason.COORDINATOR_FAILURE
    assert crash_result.verdict.value != "no_issue_detected"
    assert SEMANTIC_RELEVANCE_REVIEW_NOT_RUN_GAP in crash_result.coverage.known_gaps
    assert SEMANTIC_REVIEW_NOT_CONFIGURED_GAP not in crash_result.coverage.known_gaps

    timed_out = _App(semantic=True)
    timed_out.semantic_result = FinalSemanticEvaluation(
        SemanticStatus.UNAVAILABLE,
        SemanticReason.CREDENTIAL_UNAVAILABLE,
    )
    timeout_result = await execute_check_commit(timed_out, _request("semantic_if_configured"))
    assert timeout_result.findings
    assert timeout_result.semantic_status is SemanticStatus.UNAVAILABLE
    assert timeout_result.verdict.value != "no_issue_detected"
    assert SEMANTIC_RELEVANCE_REVIEW_NOT_RUN_GAP in timeout_result.coverage.known_gaps

    # Deterministic findings remain intact (same unsupported-claim material from the frozen case).
    assert {finding.kind.value for finding in crash_result.findings} == {
        finding.kind.value for finding in timeout_result.findings
    }


@pytest.mark.anyio
async def test_check_replay_skips_policy_ids_and_second_commit() -> None:
    app = _App()
    first = await execute_check_commit(app, _request())
    app.ledger.replay = first
    allocated = app.id_source.count

    replayed = await execute_check_commit(app, _request())

    assert replayed is first
    assert app.id_source.count == allocated
    assert app.ledger.commit_count == 1


@pytest.mark.anyio
async def test_check_conflict_and_cancellation_release_runtime() -> None:
    app = _App()
    app.ledger.failure = PublicOperationError(
        PublicErrorCode.FRONTIER_CONFLICT,
        "The frontier changed.",
        True,
    )
    with pytest.raises(PublicOperationError) as caught:
        await execute_check_commit(app, _request())
    assert caught.value.code is PublicErrorCode.FRONTIER_CONFLICT
    assert cast(_Runtime, app.runtime).release_count == 1

    cancelled = _App()
    cancelled.ledger.failure = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await execute_check_commit(cancelled, _request())
    assert cast(_Runtime, cancelled.runtime).release_count == 1


@pytest.mark.anyio
async def test_route_identity_mismatch_maps_to_session_conflict() -> None:
    app = _App()
    cast(_Runtime, app.runtime).task = cast(
        TaskRuntime,
        type("WrongRoute", (), {"session_id": _SESSION, "writer_id": None})(),
    )

    with pytest.raises(PublicOperationError) as caught:
        await execute_check_commit(app, _request())

    assert caught.value.code is PublicErrorCode.SESSION_CONFLICT
    assert cast(_Runtime, app.runtime).release_count == 1
