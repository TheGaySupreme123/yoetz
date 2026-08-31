from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

import yoetz.application.check as check_module
import yoetz.observability.diagnostics as diagnostics_module
from builders.ledger_adapters import FixedClock, MemoryObjects
from builders.policy_cases import (
    FRONTIER,
    act,
    clm,
    make_case,
    obl,
    obligation_record,
    plan_record,
    record,
    res,
)
from yoetz.application.check import FinalSemanticEvaluation
from yoetz.application.check import execute_check as _execute_check
from yoetz.application.check import execute_check_commit as _execute_check_commit
from yoetz.application.service import VerificationPolicy
from yoetz.domain.events import (
    ActionKind,
    ActionRecordedPayload,
    ClaimKind,
    ClaimRecordedPayload,
    NoObligationsReason,
    ObligationPublishedPayload,
    ObligationStatus,
    PlanPublishedPayload,
    ResultOutcome,
    ResultRecordedPayload,
)
from yoetz.domain.findings import (
    Finding,
    FindingKind,
    RankedFindings,
    SemanticDispatchKind,
    SemanticProvenance,
)
from yoetz.domain.receipts import (
    COMPLETION_SCOPE_DECLARED_NONE_GAP,
    COMPLETION_SCOPE_UNDECLARED_GAP,
)
from yoetz.domain.values import Frontier, disclosure_continuation
from yoetz.kernel import deterministic_checks as deterministic_checks_module
from yoetz.ports.diagnostics import RuntimeCapability
from yoetz.ports.ids import IdPort
from yoetz.ports.ledger import (
    CheckAwaitingHuman,
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
from yoetz.ports.semantic import ReviewerChallenge, SamplingParams, SemanticJudgment
from yoetz.protocol.errors import ProtocolValueError, PublicErrorCode, PublicOperationError
from yoetz.protocol.ids import IdKind
from yoetz.protocol.models import CheckRequest, CheckScopeModel, SemanticReason, SemanticStatus

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


async def execute_check(*args: Any, **kwargs: Any) -> CheckCommitResult:
    """Narrow to the committed branch.

    execute_check also returns CheckAwaitingHuman when a check suspends on a local disclosure
    decision. Every test here drives a terminal outcome, so a suspension is a test-setup bug and
    should fail loudly rather than surface as an attribute error 140 lines later.
    """

    result = await _execute_check(*args, **kwargs)
    assert type(result) is CheckCommitResult, f"unexpected nonterminal check: {type(result)}"
    return result


async def execute_check_commit(*args: Any, **kwargs: Any) -> CheckCommitResult:
    """Narrow to the committed branch; see execute_check above."""

    result = await _execute_check_commit(*args, **kwargs)
    assert type(result) is CheckCommitResult, f"unexpected nonterminal check: {type(result)}"
    return result


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
        self.commit_failure: Exception | None = None
        self.fail_count = 0
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

    async def lookup_task_operation(
        self, writer_id: str, operation_id: str
    ) -> OperationRecord | None:
        return await self.lookup_operation(writer_id, operation_id)

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
        *,
        scope: CheckScopeModel | None = None,
    ) -> CheckCommitResult:
        assert frozen == self.frozen
        if self.commit_failure is not None:
            raise self.commit_failure
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

    async def fail_check_if_current(
        self, lease: OperationLease, failure: PublicOperationError
    ) -> None:
        assert lease == self.frozen.lease
        assert failure.code is PublicErrorCode.INTERNAL_ERROR
        assert self.operation is not None
        self.fail_count += 1
        result_canonical = b'{"code":"INTERNAL_ERROR"}'
        self.operation = replace(
            self.operation,
            state=OperationState.COMPLETE,
            phase=CheckPhase.TERMINAL,
            owner_generation=None,
            lease_owner_id=None,
            lease_generation=None,
            lease_expires_at=None,
            resume_object_ref=None,
            result_canonical=result_canonical,
            result_digest=f"sha256:{hashlib.sha256(result_canonical).hexdigest()}",
            terminal_at=datetime(2026, 1, 1, tzinfo=UTC),
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
        self.semantic_calls = 0
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
        runtime: object | None = None,
    ) -> FinalSemanticEvaluation:
        _ = (frozen, deterministic_findings, runtime)
        self.semantic_calls += 1
        if self.crash_semantic:
            raise RuntimeError("semantic_evaluator_crashed")
        return self.semantic_result


def _request(mode: str = "deterministic_only", *, max_findings: str = "1") -> CheckRequest:
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
            "max_findings": max_findings,
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
async def test_post_admission_protocol_failure_is_internal_and_terminalizes_operation() -> None:
    """An internal value failure cannot masquerade as bad input or leave CHECK pending."""

    app = _App()
    app.ledger.commit_failure = ProtocolValueError("invalid_event_value_type")

    with pytest.raises(PublicOperationError) as caught:
        await execute_check_commit(app, _request())

    assert caught.value.code is PublicErrorCode.INTERNAL_ERROR
    assert caught.value.retryable is False
    assert app.ledger.fail_count == 1
    assert app.ledger.operation is not None
    assert app.ledger.operation.state is OperationState.COMPLETE
    assert app.ledger.operation.phase is CheckPhase.TERMINAL
    assert cast(_Runtime, app.runtime).release_count == 1


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ("deterministic_only", "semantic_required"))
@pytest.mark.parametrize(
    ("reason", "expected_gap"),
    (
        (None, COMPLETION_SCOPE_UNDECLARED_GAP),
        (NoObligationsReason.SINGLE_ATOMIC_CHANGE, COMPLETION_SCOPE_DECLARED_NONE_GAP),
    ),
)
async def test_empty_completion_scope_gap_reaches_check_verdict(
    mode: str,
    reason: NoObligationsReason | None,
    expected_gap: str,
) -> None:
    action = ActionRecordedPayload(
        act(1),
        ActionKind.COMMAND,
        "Run the atomic change",
        command="true",
    )
    result = ResultRecordedPayload(res(1), act(1), ResultOutcome.SUCCESS, exit_status=0)
    claim = ClaimRecordedPayload(
        clm(1),
        ClaimKind.COMPLETION,
        "The atomic change is complete.",
        (res(1),),
    )
    plan = PlanPublishedPayload(1, "Atomic change", (), (), reason)
    base = make_case(
        plans={1: plan_record(plan, 1)},
        actions={act(1): record(action, 2)},
        results={res(1): record(result, 3)},
        claims={clm(1): record(claim, 4)},
    )
    gap = deterministic_checks_module.completion_scope_gap(base.projection)
    assert gap is not None
    case = replace(base, gaps=(gap,))
    app = _App(semantic=mode == "semantic_required")
    app.ledger.frozen = FrozenCase(case, app.ledger.frozen.lease)

    checked = await execute_check_commit(app, _request(mode))

    assert checked.findings == ()
    assert checked.verdict.value == "insufficient_coverage"
    assert expected_gap in checked.coverage.known_gaps
    assert checked.policy_executions == (
        CheckPolicyExecution("research-evidence", "0.1.0", "run", "completed"),
        CheckPolicyExecution("work-integrity", "0.1.0", "run", "completed"),
    )


@pytest.mark.anyio
@pytest.mark.parametrize("reason", (None, NoObligationsReason.SINGLE_ATOMIC_CHANGE))
async def test_empty_completion_scope_gap_dominates_mixed_actionable_finding(
    reason: NoObligationsReason | None,
) -> None:
    action = ActionRecordedPayload(
        act(1),
        ActionKind.COMMAND,
        "Run the atomic change",
        command="true",
    )
    result = ResultRecordedPayload(res(1), act(1), ResultOutcome.SUCCESS, exit_status=0)
    obligation = ObligationPublishedPayload(
        obl(1),
        "Resolve the undeclared obligation",
        "Recorded resolution evidence",
        ObligationStatus.OPEN,
    )
    claim = ClaimRecordedPayload(
        clm(1),
        ClaimKind.COMPLETION,
        "The atomic change is complete.",
        (res(1),),
        obligation_refs=(obl(1),),
    )
    plan = PlanPublishedPayload(1, "Atomic change", (), (), reason)
    base = make_case(
        plans={1: plan_record(plan, 1)},
        obligations={obl(1): obligation_record(obligation, 2)},
        actions={act(1): record(action, 3)},
        results={res(1): record(result, 4)},
        claims={clm(1): record(claim, 5)},
    )
    gap = deterministic_checks_module.completion_scope_gap(base.projection)
    assert gap is not None
    app = _App()
    app.ledger.frozen = FrozenCase(replace(base, gaps=(gap,)), app.ledger.frozen.lease)

    checked = await execute_check_commit(app, _request())

    assert tuple(finding.kind for finding in checked.findings) == (
        FindingKind.COMPLETION_WITH_OPEN_OBLIGATIONS,
    )
    assert checked.verdict.value == "insufficient_coverage"
    assert gap.code in checked.coverage.known_gaps


@pytest.mark.anyio
async def test_resolved_declared_scope_reaches_clean_check_verdict() -> None:
    action = ActionRecordedPayload(
        act(1),
        ActionKind.COMMAND,
        "Run the declared change",
        obligation_refs=(obl(1),),
        command="true",
    )
    result = ResultRecordedPayload(res(1), act(1), ResultOutcome.SUCCESS, exit_status=0)
    obligation = ObligationPublishedPayload(
        obl(1),
        "Complete the declared change",
        "A successful recorded result",
        ObligationStatus.RESOLVED,
        resolution_evidence_refs=(res(1),),
    )
    claim = ClaimRecordedPayload(
        clm(1),
        ClaimKind.COMPLETION,
        "The declared change is complete.",
        (res(1),),
        obligation_refs=(obl(1),),
    )
    plan = PlanPublishedPayload(1, "Declared change", (obl(1),), ())
    case = make_case(
        plans={1: plan_record(plan, 1)},
        obligations={obl(1): obligation_record(obligation, 2)},
        actions={act(1): record(action, 3)},
        results={res(1): record(result, 4)},
        claims={clm(1): record(claim, 5)},
    )
    assert deterministic_checks_module.completion_scope_gap(case.projection) is None
    app = _App(semantic=True)
    app.semantic_result = _succeeded(SemanticJudgment("no_material_discrepancy", ()))
    app.ledger.frozen = FrozenCase(case, app.ledger.frozen.lease)

    checked = await execute_check_commit(app, _request("semantic_if_configured"))

    assert checked.findings == ()
    assert checked.coverage.known_gaps == ()
    assert checked.verdict.value == "no_issue_detected"


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
async def test_strict_route_ceiling_never_requests_or_dispatches_semantic_capability() -> None:
    app = _App(semantic=True)

    result = await execute_check_commit(
        app,
        _request("semantic_required"),
        route_profile="strict",
    )

    assert result.verdict.value == "incomplete_check"
    assert result.semantic_status is SemanticStatus.BLOCKED_BY_POLICY
    assert result.semantic_reason is SemanticReason.ROUTE_SEMANTIC_CEILING
    assert result.coverage.known_gaps == ("optional_semantic_review_blocked_by_policy",)
    assert app.semantic_calls == 0
    runtime = cast(_Runtime, app.runtime)
    assert runtime.last_command is not None
    assert RuntimeCapability.SEMANTIC not in runtime.last_command.required_capabilities


@pytest.mark.anyio
async def test_semantic_evaluator_crash_degrades_to_not_run_without_false_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement: evaluator crash/timeout degrades to not-run disclosure, never false clean."""

    from yoetz.domain.receipts import (
        SEMANTIC_RELEVANCE_REVIEW_NOT_RUN_GAP,
        SEMANTIC_REVIEW_NOT_CONFIGURED_GAP,
    )

    monkeypatch.setattr(diagnostics_module, "log_dir", lambda: tmp_path)
    crashed = _App(semantic=True, crash_semantic=True)
    crash_result = await execute_check_commit(crashed, _request("semantic_if_configured"))
    assert crash_result.findings
    assert crash_result.semantic_status is SemanticStatus.FAILED
    assert crash_result.semantic_reason is SemanticReason.COORDINATOR_FAILURE
    assert crash_result.verdict.value != "no_issue_detected"
    assert SEMANTIC_RELEVANCE_REVIEW_NOT_RUN_GAP in crash_result.coverage.known_gaps
    assert SEMANTIC_REVIEW_NOT_CONFIGURED_GAP not in crash_result.coverage.known_gaps
    raw = diagnostics_module.diagnostic_log_path(root=tmp_path).read_text(encoding="ascii")
    records = tuple(json.loads(line) for line in raw.splitlines() if line)
    assert len(records) == 1
    assert records[0]["component"] == "check"
    assert records[0]["operation"] == "semantic_not_dispatched_coordinator_failure"
    assert records[0]["reason"] == "exception_runtime_error"
    assert records[0]["request_id"] == _REQUEST
    assert "semantic_evaluator_crashed" not in raw
    assert "payload" not in raw
    assert str(tmp_path) not in raw

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
async def test_awaiting_human_replay_commits_only_after_a_terminal_decision() -> None:
    app = _App(semantic=True)
    objects = cast(MemoryObjects, cast(_Runtime, app.runtime).task.objects)
    prior = ObjectRef(
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
    objects._refs[prior.object_id] = prior  # pyright: ignore[reportPrivateUsage]
    objects._data[prior.object_id] = b"{}"  # pyright: ignore[reportPrivateUsage]
    app.semantic_result = FinalSemanticEvaluation(
        SemanticStatus.AWAITING_HUMAN,
        SemanticReason.HUMAN_APPROVAL_REQUIRED,
        continuation=disclosure_continuation(
            pending_id="ppr_30000000-0000-4000-8000-000000000009",
            expires_at=datetime(2030, 1, 1, tzinfo=UTC),
            request_id=_REQUEST,
        ),
    )

    suspended = await _execute_check_commit(app, _request("semantic_if_configured"))

    assert type(suspended) is CheckAwaitingHuman
    assert app.ledger.commit_count == 0
    assert app.ledger.operation is not None
    assert app.ledger.operation.state is OperationState.PENDING
    assert app.ledger.operation.phase is CheckPhase.SEMANTIC_WAIT

    app.semantic_result = FinalSemanticEvaluation(
        SemanticStatus.HUMAN_DENIED,
        SemanticReason.HUMAN_DENIED,
    )
    committed = await _execute_check_commit(app, _request("semantic_if_configured"))

    assert type(committed) is CheckCommitResult
    assert committed.semantic_status is SemanticStatus.HUMAN_DENIED
    assert committed.semantic_reason is SemanticReason.HUMAN_DENIED
    assert app.ledger.commit_count == 1
    assert app.ledger.phase_transitions == [
        (CheckPhase.RESERVED, CheckPhase.LOCAL_READY),
        (CheckPhase.LOCAL_READY, CheckPhase.SEMANTIC_WAIT),
        (CheckPhase.SEMANTIC_WAIT, CheckPhase.READY_TO_FINALIZE),
    ]


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


@pytest.mark.anyio
async def test_succeeded_review_with_withheld_context_is_not_reported_as_full_coverage() -> None:
    """A review that ran without material its own profile selected must show up in coverage.

    A live installation ran review profile ``assisted`` while its inference channel permitted
    neither ``obligation_text`` nor ``finding_summary``. The reviewer was asked whether the work
    satisfied its obligations with the obligations withheld, produced zero findings, and reported
    ``semantic_status: succeeded`` — which reads as a clean, complete review. Coverage has to
    carry the difference, or the receipt inherits the same false impression.
    """

    from yoetz.domain.receipts import SEMANTIC_REVIEW_CONTEXT_WITHHELD_GAP

    app = _App(semantic=True)
    digest = "sha256:" + "a" * 64
    app.semantic_result = FinalSemanticEvaluation(
        SemanticStatus.SUCCEEDED,
        SemanticReason.SEMANTIC_COMPLETED,
        judgment=SemanticJudgment("no_material_discrepancy", ()),
        provenance=SemanticProvenance(
            provider="fake",
            endpoint_profile_id="fake",
            endpoint_profile_version="1.0.0",
            model="fake/model",
            sdk_version="1.0.0",
            prompt_digest=digest,
            schema_digest=digest,
            policy_digest=digest,
            privacy_policy_digest=digest,
            sampling_params=SamplingParams(128),
            latency_ms=1,
            semantic_attempt_id="att_30000000-0000-4000-8000-000000000001",
            dispatch_kind=SemanticDispatchKind.EXTERNAL,
            privacy_receipt_id="egr_30000000-0000-4000-8000-000000000001",
            status=SemanticStatus.SUCCEEDED,
            reason=SemanticReason.SEMANTIC_COMPLETED,
            provider_request_id="fake-semantic-request-1",
            egress_authorization_id="aut_30000000-0000-4000-8000-000000000001",
            request_commitment="hmac-sha256:" + "b" * 64,
        ),
        withheld_review_categories=("finding_summary", "obligation_text"),
    )
    result = await execute_check_commit(app, _request("semantic_if_configured"))
    assert SEMANTIC_REVIEW_CONTEXT_WITHHELD_GAP in result.coverage.known_gaps
    assert result.verdict.value != "no_issue_detected"

    # A review whose profile and channel agree declares no such gap.
    agreed = _App(semantic=True)
    agreed.semantic_result = replace(app.semantic_result, withheld_review_categories=())
    clean = await execute_check_commit(agreed, _request("semantic_if_configured"))
    assert SEMANTIC_REVIEW_CONTEXT_WITHHELD_GAP not in clean.coverage.known_gaps


def _succeeded(judgment: SemanticJudgment) -> FinalSemanticEvaluation:
    digest = "sha256:" + "a" * 64
    return FinalSemanticEvaluation(
        SemanticStatus.SUCCEEDED,
        SemanticReason.SEMANTIC_COMPLETED,
        judgment=judgment,
        provenance=SemanticProvenance(
            provider="fake",
            endpoint_profile_id="fake",
            endpoint_profile_version="1.0.0",
            model="fake/model",
            sdk_version="1.0.0",
            prompt_digest=digest,
            schema_digest=digest,
            policy_digest=digest,
            privacy_policy_digest=digest,
            sampling_params=SamplingParams(128),
            latency_ms=1,
            semantic_attempt_id="att_30000000-0000-4000-8000-000000000001",
            dispatch_kind=SemanticDispatchKind.EXTERNAL,
            privacy_receipt_id="egr_30000000-0000-4000-8000-000000000001",
            status=SemanticStatus.SUCCEEDED,
            reason=SemanticReason.SEMANTIC_COMPLETED,
            provider_request_id="fake-semantic-request-1",
            egress_authorization_id="aut_30000000-0000-4000-8000-000000000001",
            request_commitment="hmac-sha256:" + "b" * 64,
        ),
    )


@pytest.mark.anyio
async def test_response_content_invalid_is_a_committed_semantic_outcome() -> None:
    app = _App(semantic=True)
    succeeded = _succeeded(SemanticJudgment("no_material_discrepancy", ()))
    assert succeeded.provenance is not None
    app.semantic_result = replace(
        succeeded,
        status=SemanticStatus.INVALID,
        reason=SemanticReason.RESPONSE_CONTENT_INVALID,
        judgment=None,
        provenance=replace(
            succeeded.provenance,
            status=SemanticStatus.INVALID,
            reason=SemanticReason.RESPONSE_CONTENT_INVALID,
        ),
    )

    result = await execute_check_commit(app, _request("semantic_if_configured"))

    assert result.outcome == "committed"
    assert result.semantic_status is SemanticStatus.INVALID
    assert result.semantic_reason is SemanticReason.RESPONSE_CONTENT_INVALID
    assert result.coverage.known_gaps
    assert app.ledger.commit_count == 1
    assert app.ledger.fail_count == 0


def _reviewer_challenge(ref: str, *, summary: str = "Evidence gap") -> ReviewerChallenge:
    return ReviewerChallenge(
        FindingKind.CLAIM_WITHOUT_ADMISSIBLE_EVIDENCE,
        summary,
        (ref,),
        "The claim lacks a recorded basis.",
        "The claim may remain unresolved.",
        "Main agent: provide evidence for the claim.",
        "provide_evidence",
        "The missing material may exist outside the case.",
    )


@pytest.mark.anyio
async def test_rejected_judgment_commits_the_check_instead_of_failing_the_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reviewer answer the fence refuses costs the reviewer's output, never the whole check.

    Regression for a live failure: the post-validation ``ValueError`` escaped ``execute_check_commit``
    (which caught only ``ProtocolValueError``), reached the daemon catch-all, and became a
    non-retryable ``INVALID_REQUEST`` with no correlation id. No check was recorded at all, so the
    deterministic findings were lost and nothing said why. ``SemanticStatus.INVALID`` /
    ``SEMANTIC_JUDGMENT_REJECTED`` existed for exactly this and had never once been written.

    The structural fence is unreachable through the ordinary call (the coordinator passes the frozen
    case's own frontier and a SUCCEEDED provenance by construction), so the raise is injected here:
    what is under test is the disposition of the failure, not its trigger.
    """

    monkeypatch.setattr(diagnostics_module, "log_dir", lambda: tmp_path)

    def _raise(*_args: object, **_kwargs: object) -> object:
        raise check_module.SemanticJudgmentRejected("semantic_judgment_invalid")

    monkeypatch.setattr(check_module, "validate_semantic_judgment", _raise)

    app = _App(semantic=True)
    app.semantic_result = _succeeded(
        SemanticJudgment("challenges_returned", (_reviewer_challenge(str(clm(1))),))
    )

    result = await execute_check_commit(app, _request("semantic_if_configured"))

    assert result.semantic_status is SemanticStatus.INVALID
    assert result.semantic_reason is SemanticReason.SEMANTIC_JUDGMENT_REJECTED
    assert result.semantic_provenance is not None
    assert result.semantic_provenance.status is SemanticStatus.INVALID
    assert result.semantic_provenance.reason is SemanticReason.SEMANTIC_JUDGMENT_REJECTED
    # The whole point: the deterministic findings the user paid for still committed.
    assert result.findings
    assert all(finding.origin.value == "deterministic" for finding in result.findings)
    assert app.ledger.commit_count == 1
    assert result.verdict.value != "no_issue_detected"

    raw = diagnostics_module.diagnostic_log_path(root=tmp_path).read_text(encoding="ascii")
    operations = {json.loads(line)["operation"] for line in raw.splitlines() if line}
    assert "semantic_judgment_rejected" in operations


@pytest.mark.anyio
async def test_partial_rejection_keeps_accepted_challenges_and_declares_the_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One unusable challenge costs itself; the others become findings and the loss is declared."""

    from yoetz.domain.receipts import SEMANTIC_CHALLENGES_REJECTED_GAP

    monkeypatch.setattr(diagnostics_module, "log_dir", lambda: tmp_path)
    app = _App(semantic=True)
    app.semantic_result = _succeeded(
        SemanticJudgment(
            "challenges_returned",
            (
                _reviewer_challenge(str(clm(1)), summary="Accepted challenge"),
                _reviewer_challenge(
                    "clm_20000000-0000-4000-8000-000000000099", summary="Invented ref"
                ),
            ),
        )
    )

    result = await execute_check_commit(app, _request("semantic_if_configured", max_findings="4"))

    semantic_findings = [
        finding for finding in result.findings if finding.origin.value == "semantic_model_derived"
    ]
    assert [finding.summary for finding in semantic_findings] == ["Accepted challenge"]
    assert result.semantic_status is SemanticStatus.SUCCEEDED
    assert SEMANTIC_CHALLENGES_REJECTED_GAP in result.coverage.known_gaps

    raw = diagnostics_module.diagnostic_log_path(root=tmp_path).read_text(encoding="ascii")
    accounting = [
        json.loads(line)
        for line in raw.splitlines()
        if line and json.loads(line)["operation"] == "semantic_review_accounting"
    ]
    assert len(accounting) == 1
    record_json = accounting[0]
    assert record_json["semantic_conclusion"] == "challenges_returned"
    assert record_json["semantic_challenges_returned"] == 2
    assert record_json["semantic_candidates_accepted"] == 1
    assert record_json["semantic_challenges_rejected"] == 1
    assert record_json["semantic_findings_selected"] == 1
    assert record_json["semantic_findings_suppressed"] == 0
    # The record reconciles: nothing the reviewer returned is unaccounted for.
    assert record_json["semantic_challenges_returned"] == (
        record_json["semantic_candidates_accepted"] + record_json["semantic_challenges_rejected"]
    )
    assert record_json["semantic_candidates_accepted"] == (
        record_json["semantic_findings_selected"] + record_json["semantic_findings_suppressed"]
    )
    assert "Invented ref" not in raw
    assert "Accepted challenge" not in raw
