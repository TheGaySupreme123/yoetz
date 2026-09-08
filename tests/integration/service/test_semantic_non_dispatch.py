from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

import yoetz.observability.diagnostics as diagnostics_module
import yoetz.service.ready_composition as ready_composition_module
from builders.ledger_adapters import (
    FixedClock,
    append_command,
    memory_adapter,
    ownership_fence,
    sqlite_adapter,
)
from builders.policy_cases import FRONTIER, make_case
from yoetz.adapters.memory.ledger import MemoryLedgerAdapter
from yoetz.adapters.sqlite.repository import SqliteLedger
from yoetz.application.check import FinalSemanticEvaluation, semantic_coverage_gap_code
from yoetz.application.egress import (
    PrivacyCoordinator,
    RepositoryGrantAdmission,
    SemanticEgressAwaitingHuman,
    SemanticEgressBlocked,
    SemanticEgressProviderOutcome,
)
from yoetz.domain.findings import SamplingParams, SemanticDispatchKind, SemanticFailureClass
from yoetz.domain.privacy import (
    AuthorizationScope,
    AuthorizationScopeKind,
    ChannelPolicy,
    DataClass,
    EgressChannel,
    PrivacyOutcome,
    PrivacyPolicy,
    PrivacyProfile,
    PrivacyReason,
    ProviderBinding,
    ReviewContextProfile,
    ReviewSelectionPolicy,
)
from yoetz.domain.receipts import (
    SEMANTIC_RELEVANCE_REVIEW_NOT_RUN_GAP,
    SEMANTIC_REVIEW_NOT_CONFIGURED_GAP,
)
from yoetz.ports.diagnostics import RuntimeCapability
from yoetz.ports.importer import ImporterPort
from yoetz.ports.ledger import CheckPhase, FrozenCase, OperationLease
from yoetz.ports.objects import ObjectKind, ObjectMetadata, ObjectSource, ObjectStorePort
from yoetz.ports.privacy import (
    EffectivePrivacyPolicy,
    OutboundGatewayPort,
    PrivacyAuditPort,
    PrivacyClassifierPort,
    PrivacyPolicyStorePort,
    RepositoryPrivacyAuthority,
)
from yoetz.ports.runtime import TaskRuntime
from yoetz.ports.semantic import ProviderAttemptProvenance, SemanticResultUnavailable
from yoetz.ports.start_catalog import (
    SessionBinding,
    StartCatalogPort,
    TaskRoute,
    TaskRouteState,
)
from yoetz.protocol.canonical import canonical_digest, canonical_encode
from yoetz.protocol.models import DataCategory, SemanticReason, SemanticStatus

_TASK = "tsk_53000000-0000-4000-8000-000000000001"
_SESSION = "ses_53000000-0000-4000-8000-000000000001"
_WRITER = "wri_53000000-0000-4000-8000-000000000001"
_REQUEST = "req_53000000-0000-4000-8000-000000000001"
_INSTALLATION = "ins_53000000-0000-4000-8000-000000000001"
_REPOSITORY = "hmac-sha256:" + "b" * 64
_PROVIDER = ProviderBinding(
    "sensitive-provider",
    "model",
    "endpoint-profile",
    "1.0.0",
    "external",
)

type _SemanticEvaluator = Callable[
    [FrozenCase, tuple[object, ...]], Awaitable[FinalSemanticEvaluation]
]


def _test_effective_policy() -> EffectivePrivacyPolicy:
    scope = AuthorizationScope(
        AuthorizationScopeKind.TASK,
        _INSTALLATION,
        _REPOSITORY,
        _TASK,
    )

    def _disabled(channel: EgressChannel) -> ChannelPolicy:
        return ChannelPolicy(
            channel,
            False,
            (),
            (),
            None,
            (),
            AuthorizationScopeKind.MACHINE,
            False,
            0,
            0,
            0,
        )

    policy = PrivacyPolicy(
        policy_id="pvy_53000000-0000-4000-8000-000000000001",
        version=1,
        policy_digest="sha256:" + "c" * 64,
        profile=PrivacyProfile.LOCAL_ONLY,
        review_context_profile=ReviewContextProfile.STRUCTURAL,
        review_selection=ReviewSelectionPolicy.for_profile(ReviewContextProfile.STRUCTURAL),
        require_current_provider_data_use_evidence=False,
        network_egress_permitted=False,
        effective_scope=scope,
        channel_policies=tuple(
            _disabled(channel) for channel in sorted(EgressChannel, key=lambda c: c.value)
        ),
        local_model_enabled=False,
        local_model_binding=None,
        local_model_categories=(),
        local_model_data_classes=(),
        agent_context_categories=(DataCategory.FINDING_SUMMARY,),
        agent_context_data_classes=(DataClass.ORDINARY_USER_CONTENT, DataClass.PUBLIC_STRUCTURAL),
        trusted_human_control_categories=tuple(DataCategory),
        trusted_human_control_data_classes=(
            DataClass.ORDINARY_USER_CONTENT,
            DataClass.PUBLIC_STRUCTURAL,
        ),
        created_at=datetime(2030, 1, 1, tzinfo=UTC),
    )
    return EffectivePrivacyPolicy(policy, 1, policy.policy_digest)


class _PolicyStore:
    def __init__(self, effective: EffectivePrivacyPolicy, *, repository_granted: bool) -> None:
        self._effective = effective
        self._repository_granted = repository_granted

    async def effective_policy(self, scope: AuthorizationScope) -> EffectivePrivacyPolicy:
        del scope
        return self._effective

    async def repository_authority(self, scope: AuthorizationScope) -> RepositoryPrivacyAuthority:
        grant_policy = None
        grant_generation = None
        grant_policy_digest = None
        if self._repository_granted:
            grant_policy = replace(
                self._effective.policy,
                effective_scope=AuthorizationScope(
                    AuthorizationScopeKind.WORKSPACE,
                    _INSTALLATION,
                    _REPOSITORY,
                ),
                policy_digest="sha256:" + "e" * 64,
            )
            grant_generation = 1
            grant_policy_digest = grant_policy.policy_digest
        return RepositoryPrivacyAuthority(
            scope=scope,
            effective=self._effective,
            repository_privacy_commitment=_REPOSITORY,
            grant_state="granted" if self._repository_granted else "missing",
            migration_state="not_applicable",
            authority_digest="sha256:" + "f" * 64,
            ancestors=(),
            grant_generation=grant_generation,
            grant_policy_digest=grant_policy_digest,
            grant_policy=grant_policy,
        )

    def set_repository_granted(self, granted: bool) -> None:
        self._repository_granted = granted


class _PolicyApplication:
    def __init__(self, effective: EffectivePrivacyPolicy, *, repository_granted: bool) -> None:
        self.policy_store = _PolicyStore(effective, repository_granted=repository_granted)


class _Privacy:
    def __init__(self, *, repository_granted: bool = True, task_id: str = _TASK) -> None:
        self.calls = 0
        self.resume_calls = 0
        self.repository_granted = repository_granted
        self.task_id = task_id
        # Real policy path is required for dispatch; never mint synthetic policy identity.
        self.policy_application = _PolicyApplication(
            _test_effective_policy(), repository_granted=repository_granted
        )
        self.terminal_provider_result = False
        self.resume_terminal: tuple[PrivacyOutcome, PrivacyReason] | None = None
        self.cancel_on_resume = False

    async def activate_repository(self, scope: AuthorizationScope) -> bool:
        assert scope == AuthorizationScope(
            AuthorizationScopeKind.TASK,
            _INSTALLATION,
            _REPOSITORY,
            self.task_id,
        )
        return self.repository_granted

    async def admit_repository_grant(self, scope: AuthorizationScope) -> RepositoryGrantAdmission:
        policy_application = cast(
            _PolicyApplication | None, getattr(self, "policy_application", None)
        )
        if policy_application is None:
            return RepositoryGrantAdmission.UNAVAILABLE
        try:
            authority = await policy_application.policy_store.repository_authority(scope)
        except Exception:
            return RepositoryGrantAdmission.UNAVAILABLE
        if (
            type(authority) is not RepositoryPrivacyAuthority
            or authority.scope != scope
            or authority.repository_privacy_commitment != scope.workspace_ref_commitment
        ):
            return RepositoryGrantAdmission.UNAVAILABLE
        if authority.grant_state == "missing":
            return RepositoryGrantAdmission.MISSING
        if authority.grant_state != "granted":
            return RepositoryGrantAdmission.UNAVAILABLE
        try:
            activated = await self.activate_repository(scope)
        except Exception:
            return RepositoryGrantAdmission.UNAVAILABLE
        return (
            RepositoryGrantAdmission.GRANTED if activated else RepositoryGrantAdmission.UNAVAILABLE
        )

    async def evaluate_semantic(self, candidate: object, deadline: object) -> object:
        del deadline
        self.calls += 1
        request_id = cast(str, getattr(candidate, "request_id"))
        if self.terminal_provider_result:
            return SemanticEgressProviderOutcome(
                request_id=request_id,
                privacy_proposal_id="ppr_53000000-0000-4000-8000-000000000002",
                authorization_id="aut_53000000-0000-4000-8000-000000000003",
                dispatch_kind=SemanticDispatchKind.EXTERNAL,
                result=SemanticResultUnavailable(
                    ProviderAttemptProvenance(
                        provider=_PROVIDER.provider_id,
                        endpoint_profile_id=_PROVIDER.endpoint_profile_id,
                        endpoint_profile_version=_PROVIDER.endpoint_profile_version,
                        model=_PROVIDER.model_id,
                        sdk_version="1.0.0",
                        prompt_digest="sha256:" + "1" * 64,
                        schema_digest="sha256:" + "2" * 64,
                        policy_digest="sha256:" + "3" * 64,
                        privacy_policy_digest="sha256:" + "4" * 64,
                        sampling_params=SamplingParams(128),
                        latency_ms=1,
                        status=SemanticStatus.UNAVAILABLE,
                        failure_class=SemanticFailureClass.TRANSPORT,
                    )
                ),
                case_digest="sha256:" + "5" * 64,
                privacy_receipt_id="egr_53000000-0000-4000-8000-000000000004",
                request_commitment="hmac-sha256:" + "6" * 64,
            )
        return SemanticEgressAwaitingHuman(
            request_id,
            "ppr_53000000-0000-4000-8000-000000000001",
            "sha256:" + "a" * 64,
            datetime(2030, 1, 1, tzinfo=UTC),
        )

    async def resume(self, request_id: str, case_digest: str, deadline: object) -> object:
        del case_digest, deadline
        self.resume_calls += 1
        if self.cancel_on_resume:
            raise asyncio.CancelledError
        assert self.resume_terminal is not None
        outcome, reason = self.resume_terminal
        return SemanticEgressBlocked(
            request_id,
            outcome,
            reason,
            privacy_proposal_id="ppr_53000000-0000-4000-8000-000000000001",
        )


class _Catalog:
    def __init__(self, route: TaskRoute | None) -> None:
        self.route = route

    async def resolve_route(self, session: str) -> TaskRoute | None:
        assert self.route is None or session == self.route.session_id
        return self.route

    async def session_binding(self, session: str) -> SessionBinding | None:
        del session
        return None


def _route(state: TaskRouteState = TaskRouteState.ACTIVE) -> TaskRoute:
    route_generation = 1
    bundle_relpath = f"tasks/{_TASK}"
    identity = canonical_digest(
        {
            "task_id": _TASK,
            "bundle_relpath": bundle_relpath,
            "route_generation": route_generation,
        }
    )
    return TaskRoute(
        _TASK,
        _SESSION,
        bundle_relpath,
        route_generation,
        state,
        identity,
        _REPOSITORY,
    )


def _frozen() -> FrozenCase:
    return FrozenCase(
        make_case(),
        OperationLease(
            _WRITER,
            _REQUEST,
            _SESSION,
            CheckPhase.LOCAL_READY,
            "owner-generation-1",
            "lease-owner-1",
            1,
            datetime(2030, 1, 1, tzinfo=UTC),
            FRONTIER,
            "sha256:" + "d" * 64,
        ),
    )


def _route_for(task_id: str, session_id: str) -> TaskRoute:
    route_generation = 1
    bundle_relpath = f"tasks/{task_id}"
    return TaskRoute(
        task_id,
        session_id,
        bundle_relpath,
        route_generation,
        TaskRouteState.ACTIVE,
        canonical_digest(
            {
                "task_id": task_id,
                "bundle_relpath": bundle_relpath,
                "route_generation": route_generation,
            }
        ),
        _REPOSITORY,
    )


async def _durable_semantic_case(
    adapter: MemoryLedgerAdapter | SqliteLedger,
) -> tuple[FrozenCase, TaskRuntime]:
    command = append_command()
    await adapter.append_batch(command)
    frozen = await adapter.freeze_case(
        command.session_id,
        command.writer_id,
        1,
        _REQUEST,
        "sha256:" + "7" * 64,
    )
    assert type(frozen) is FrozenCase
    operation = await adapter.lookup_operation(command.writer_id, _REQUEST)
    assert operation is not None and operation.resume_object_ref is not None
    prior = operation.resume_object_ref
    local_canonical = canonical_encode(
        {
            "schema_version": "1.0.0",
            "request_id": _REQUEST,
            "request_digest": "sha256:" + "7" * 64,
            "task_id": command.task_id,
            "session_id": command.session_id,
            "writer_id": command.writer_id,
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
    objects = cast(ObjectStorePort, adapter._objects)  # pyright: ignore[reportPrivateUsage]
    staged = await objects.stage(
        ObjectSource(data=local_canonical, declared_size=len(local_canonical)),
        ObjectMetadata(
            ObjectKind.DETERMINISTIC_RESULT,
            "application/vnd.yoetz.deterministic-result+json",
            command.task_id,
            datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
        ),
    )
    local_result = await objects.finalize(staged)
    lease = await adapter.advance_check_phase(
        frozen.lease,
        CheckPhase.RESERVED,
        CheckPhase.LOCAL_READY,
        local_result,
    )
    lease = await adapter.advance_check_phase(
        lease,
        CheckPhase.LOCAL_READY,
        CheckPhase.SEMANTIC_WAIT,
    )
    runtime = TaskRuntime(
        command.task_id,
        command.session_id,
        command.writer_id,
        frozenset(
            {
                RuntimeCapability.WRITE,
                RuntimeCapability.STRUCTURAL_READ,
                RuntimeCapability.PAYLOAD_READ,
                RuntimeCapability.SEMANTIC,
            }
        ),
        adapter,
        objects,
        cast(ImporterPort, object()),
        "0.1.0",
        "0.1.0",
        "0.1",
        "1.0.0",
        ownership_fence(),
    )
    return FrozenCase(frozen.case, lease), runtime


def _evaluator(
    privacy: _Privacy,
    resolver: Callable[[], ProviderBinding | None],
    route: TaskRoute | None,
) -> _SemanticEvaluator:
    async def resolve_provider() -> ProviderBinding | None:
        return resolver()

    factory = cast(
        "Callable[..., _SemanticEvaluator]",
        getattr(ready_composition_module, "_privacy_gated_semantic_evaluator"),
    )
    return factory(
        cast(PrivacyCoordinator, privacy),
        FixedClock(),
        _INSTALLATION,
        resolve_provider,
        cast(StartCatalogPort, _Catalog(route)),
        ready_composition_module.IdPort(),
    )


def _records(tmp_path: Path) -> tuple[Mapping[str, object], ...]:
    path = diagnostics_module.diagnostic_log_path(root=tmp_path)
    return tuple(
        cast(Mapping[str, object], json.loads(line))
        for line in path.read_text(encoding="ascii").splitlines()
        if line
    )


def _assert_record(tmp_path: Path, operation: str, reason: SemanticReason) -> None:
    records = _records(tmp_path)
    assert records == (
        {
            "timestamp": records[0]["timestamp"],
            "correlation_id": records[0]["correlation_id"],
            "component": "semantic_composition",
            "operation": operation,
            "reason": reason.value,
            "request_id": _REQUEST,
        },
    )
    raw = diagnostics_module.diagnostic_log_path(root=tmp_path).read_text(encoding="ascii")
    assert "sensitive-provider" not in raw
    assert "payload" not in raw
    assert "exception" not in raw
    assert str(tmp_path) not in raw


@pytest.mark.anyio
async def test_missing_repository_grant_suspends_same_request_before_provider_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(diagnostics_module, "log_dir", lambda: tmp_path)
    privacy = _Privacy(repository_granted=False)
    provider_resolutions = 0

    def resolve() -> ProviderBinding | None:
        nonlocal provider_resolutions
        provider_resolutions += 1
        return _PROVIDER

    result = await _evaluator(privacy, resolve, _route())(_frozen(), ())

    assert (result.status, result.reason) == (
        SemanticStatus.AWAITING_HUMAN,
        SemanticReason.HUMAN_APPROVAL_REQUIRED,
    )
    assert result.continuation is not None
    assert result.continuation.kind == "repository_privacy_setup"
    assert result.continuation.command == ("yoetz", "--privacy")
    assert result.continuation.request_id == _REQUEST
    assert provider_resolutions == 0
    assert privacy.calls == 0
    _assert_record(
        tmp_path,
        "semantic_suspended_repository_grant_missing",
        SemanticReason.HUMAN_APPROVAL_REQUIRED,
    )


@pytest.mark.anyio
async def test_exact_same_request_resumes_after_trusted_grant_to_terminal_provider_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(diagnostics_module, "log_dir", lambda: tmp_path)
    privacy = _Privacy(repository_granted=False)
    provider_resolutions = 0

    def resolve() -> ProviderBinding | None:
        nonlocal provider_resolutions
        provider_resolutions += 1
        return _PROVIDER

    evaluator = _evaluator(privacy, resolve, _route())
    original = _frozen()
    suspended = await evaluator(original, ())

    assert suspended.status is SemanticStatus.AWAITING_HUMAN
    assert suspended.continuation is not None
    assert suspended.continuation.request_id == original.lease.operation_id == _REQUEST
    assert provider_resolutions == privacy.calls == 0

    privacy.repository_granted = True
    privacy.policy_application.policy_store.set_repository_granted(True)
    privacy.terminal_provider_result = True
    resumed = await evaluator(original, ())

    assert (resumed.status, resumed.reason) == (
        SemanticStatus.UNAVAILABLE,
        SemanticReason.TRANSPORT_UNAVAILABLE,
    )
    assert resumed.continuation is None
    assert provider_resolutions == privacy.calls == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "adapter_factory",
    (memory_adapter, sqlite_adapter),
    ids=("memory", "sqlite"),
)
@pytest.mark.parametrize(
    ("terminal", "expected"),
    (
        (
            (PrivacyOutcome.HUMAN_DENIED, PrivacyReason.HUMAN_DENIED),
            (SemanticStatus.HUMAN_DENIED, SemanticReason.HUMAN_DENIED),
        ),
        (
            (PrivacyOutcome.APPROVAL_EXPIRED, PrivacyReason.AUTHORIZATION_EXPIRED),
            (SemanticStatus.APPROVAL_EXPIRED, SemanticReason.HUMAN_APPROVAL_EXPIRED),
        ),
        (
            (PrivacyOutcome.BLOCKED_BY_POLICY, PrivacyReason.SCOPE_MISMATCH),
            (SemanticStatus.BLOCKED_BY_POLICY, SemanticReason.SCOPE_NOT_AUTHORIZED),
        ),
    ),
)
async def test_disclosure_wait_replay_uses_resume_and_terminalizes_one_exact_attempt(
    adapter_factory: Callable[[object], MemoryLedgerAdapter | SqliteLedger],
    terminal: tuple[PrivacyOutcome, PrivacyReason],
    expected: tuple[SemanticStatus, SemanticReason],
) -> None:
    command = append_command()
    adapter = adapter_factory(command)
    frozen, runtime = await _durable_semantic_case(adapter)
    privacy = _Privacy(task_id=runtime.task_id)
    evaluator = cast(
        Callable[[FrozenCase, tuple[object, ...], TaskRuntime], Awaitable[FinalSemanticEvaluation]],
        _evaluator(
            privacy,
            lambda: _PROVIDER,
            _route_for(runtime.task_id, runtime.session_id),
        ),
    )

    waiting = await evaluator(frozen, (), runtime)
    assert waiting.status is SemanticStatus.AWAITING_HUMAN
    assert waiting.operation_lease is not None
    job = await adapter.load_semantic_job(runtime.writer_id or "", _REQUEST)
    assert job is not None
    attempts = await adapter.list_semantic_attempts(job.job_id)
    assert len(attempts) == 1 and attempts[0].state == "started"
    original_attempt = attempts[0]

    privacy.resume_terminal = terminal
    resumed = await evaluator(
        FrozenCase(frozen.case, waiting.operation_lease),
        (),
        runtime,
    )

    assert (resumed.status, resumed.reason) == expected
    assert privacy.calls == 1
    assert privacy.resume_calls == 1
    terminal_job = await adapter.load_semantic_job(runtime.writer_id or "", _REQUEST)
    assert terminal_job is not None and terminal_job.state == "failed"
    terminal_attempts = await adapter.list_semantic_attempts(terminal_job.job_id)
    assert len(terminal_attempts) == 1
    assert terminal_attempts[0].attempt_id == original_attempt.attempt_id
    assert terminal_attempts[0].provider_request_id == original_attempt.provider_request_id
    assert terminal_attempts[0].state == "failed"
    wait = await adapter.load_disclosure_wait(runtime.writer_id or "", _REQUEST)
    assert wait is not None and wait.state == "resolved"


@pytest.mark.anyio
async def test_cancellation_while_resuming_a_wait_terminalizes_before_propagating() -> None:
    command = append_command()
    adapter = memory_adapter(command)
    frozen, runtime = await _durable_semantic_case(adapter)
    privacy = _Privacy(task_id=runtime.task_id)
    evaluator = cast(
        Callable[[FrozenCase, tuple[object, ...], TaskRuntime], Awaitable[FinalSemanticEvaluation]],
        _evaluator(
            privacy,
            lambda: _PROVIDER,
            _route_for(runtime.task_id, runtime.session_id),
        ),
    )
    waiting = await evaluator(frozen, (), runtime)
    assert waiting.operation_lease is not None
    privacy.cancel_on_resume = True

    with pytest.raises(asyncio.CancelledError):
        await evaluator(FrozenCase(frozen.case, waiting.operation_lease), (), runtime)

    job = await adapter.load_semantic_job(runtime.writer_id or "", _REQUEST)
    assert job is not None and job.state == "failed"
    assert job.terminal_code is SemanticReason.COORDINATOR_FAILURE
    attempts = await adapter.list_semantic_attempts(job.job_id)
    assert len(attempts) == 1 and attempts[0].state == "failed"
    wait = await adapter.load_disclosure_wait(runtime.writer_id or "", _REQUEST)
    assert wait is not None and wait.state == "resolved"
    assert privacy.calls == privacy.resume_calls == 1


@pytest.mark.anyio
async def test_sqlite_restart_mid_wait_recovers_the_same_attempt_and_resume_route() -> None:
    command = append_command()
    original = sqlite_adapter(command)
    frozen, runtime = await _durable_semantic_case(original)
    privacy = _Privacy(task_id=runtime.task_id)
    evaluator = cast(
        Callable[[FrozenCase, tuple[object, ...], TaskRuntime], Awaitable[FinalSemanticEvaluation]],
        _evaluator(
            privacy,
            lambda: _PROVIDER,
            _route_for(runtime.task_id, runtime.session_id),
        ),
    )
    waiting = await evaluator(frozen, (), runtime)
    assert waiting.operation_lease is not None

    original._db.execute(  # pyright: ignore[reportPrivateUsage]
        "UPDATE bundle_meta SET value='2' WHERE key='owner_generation'"
    )
    restarted = SqliteLedger(
        db=original._db,  # pyright: ignore[reportPrivateUsage]
        task_id=runtime.task_id,
        ownership_fence=ownership_fence(generation=2),
        clock=FixedClock(),
        ids=original._ids,  # pyright: ignore[reportPrivateUsage]
        objects=original._objects,  # pyright: ignore[reportPrivateUsage]
    )
    restarted_runtime = replace(
        runtime,
        ledger=restarted,
        fence=ownership_fence(generation=2),
    )
    recovered = await restarted.load_semantic_job(runtime.writer_id or "", _REQUEST)
    assert recovered is not None and recovered.state == "leased"
    recovered_attempts = await restarted.list_semantic_attempts(recovered.job_id)
    assert len(recovered_attempts) == 1 and recovered_attempts[0].state == "started"
    recovered_wait = await restarted.load_disclosure_wait(runtime.writer_id or "", _REQUEST)
    assert recovered_wait is not None and recovered_wait.state == "awaiting"
    reclaimed = await restarted.freeze_case(
        runtime.session_id,
        runtime.writer_id or "",
        1,
        _REQUEST,
        "sha256:" + "7" * 64,
    )
    assert type(reclaimed) is FrozenCase

    privacy.resume_terminal = (PrivacyOutcome.HUMAN_DENIED, PrivacyReason.HUMAN_DENIED)
    result = await evaluator(
        reclaimed,
        (),
        restarted_runtime,
    )

    assert (result.status, result.reason) == (
        SemanticStatus.HUMAN_DENIED,
        SemanticReason.HUMAN_DENIED,
    )
    terminal = await restarted.load_semantic_job(runtime.writer_id or "", _REQUEST)
    assert terminal is not None and terminal.state == "failed"
    attempts = await restarted.list_semantic_attempts(terminal.job_id)
    assert len(attempts) == 1
    assert attempts[0].attempt_id == recovered_attempts[0].attempt_id
    assert attempts[0].provider_request_id == recovered_attempts[0].provider_request_id
    assert privacy.calls == privacy.resume_calls == 1

    # A second crash after the terminal semantic write but before the outer check commit must
    # recover that terminal answer without calling either privacy entrypoint again.
    assert result.operation_lease is not None
    original._db.execute(  # pyright: ignore[reportPrivateUsage]
        "UPDATE bundle_meta SET value='3' WHERE key='owner_generation'"
    )
    recovered_terminal = SqliteLedger(
        db=original._db,  # pyright: ignore[reportPrivateUsage]
        task_id=runtime.task_id,
        ownership_fence=ownership_fence(generation=3),
        clock=FixedClock(),
        ids=original._ids,  # pyright: ignore[reportPrivateUsage]
        objects=original._objects,  # pyright: ignore[reportPrivateUsage]
    )
    terminal_runtime = replace(
        runtime,
        ledger=recovered_terminal,
        fence=ownership_fence(generation=3),
    )
    terminal_reclaimed = await recovered_terminal.freeze_case(
        runtime.session_id,
        runtime.writer_id or "",
        1,
        _REQUEST,
        "sha256:" + "7" * 64,
    )
    assert type(terminal_reclaimed) is FrozenCase
    replayed = await evaluator(
        terminal_reclaimed,
        (),
        terminal_runtime,
    )
    assert (replayed.status, replayed.reason) == (
        SemanticStatus.HUMAN_DENIED,
        SemanticReason.HUMAN_DENIED,
    )
    replayed_wait = await recovered_terminal.load_disclosure_wait(runtime.writer_id or "", _REQUEST)
    assert replayed_wait is not None and replayed_wait.state == "resolved"
    assert privacy.calls == privacy.resume_calls == 1


class _ClosingGateway:
    async def close(self) -> None:
        return None


@pytest.mark.anyio
async def test_closed_real_coordinator_is_terminal_without_repository_setup_or_dispatch() -> None:
    coordinator = PrivacyCoordinator(
        cast(
            PrivacyPolicyStorePort, _PolicyStore(_test_effective_policy(), repository_granted=False)
        ),
        cast(PrivacyClassifierPort, object()),
        cast(PrivacyAuditPort, object()),
        cast(OutboundGatewayPort, _ClosingGateway()),
        FixedClock(),
        ready_composition_module.IdPort(),
    )
    await coordinator.close()
    provider_resolutions = 0

    def resolve() -> ProviderBinding | None:
        nonlocal provider_resolutions
        provider_resolutions += 1
        return _PROVIDER

    result = await _evaluator(cast(_Privacy, coordinator), resolve, _route())(_frozen(), ())

    assert (result.status, result.reason) == (
        SemanticStatus.BLOCKED_BY_POLICY,
        SemanticReason.SCOPE_NOT_AUTHORIZED,
    )
    assert result.continuation is None
    assert provider_resolutions == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    (
        "live_provider",
        "route",
        "status",
        "reason",
        "gap",
        "operation",
    ),
    (
        (
            None,
            _route(),
            SemanticStatus.UNAVAILABLE,
            SemanticReason.CREDENTIAL_UNAVAILABLE,
            SEMANTIC_RELEVANCE_REVIEW_NOT_RUN_GAP,
            "semantic_not_dispatched_credential_unavailable",
        ),
        (
            _PROVIDER,
            None,
            SemanticStatus.NOT_CONFIGURED,
            SemanticReason.PROVIDER_NOT_CONFIGURED,
            SEMANTIC_REVIEW_NOT_CONFIGURED_GAP,
            "semantic_not_dispatched_route_inactive",
        ),
        (
            _PROVIDER,
            _route(TaskRouteState.QUARANTINED),
            SemanticStatus.NOT_CONFIGURED,
            SemanticReason.PROVIDER_NOT_CONFIGURED,
            SEMANTIC_REVIEW_NOT_CONFIGURED_GAP,
            "semantic_not_dispatched_route_inactive",
        ),
    ),
)
async def test_semantic_non_dispatch_records_exact_bounded_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    live_provider: ProviderBinding | None,
    route: TaskRoute | None,
    status: SemanticStatus,
    reason: SemanticReason,
    gap: str,
    operation: str,
) -> None:
    monkeypatch.setattr(diagnostics_module, "log_dir", lambda: tmp_path)
    privacy = _Privacy()
    evaluator = _evaluator(privacy, lambda: live_provider, route)

    result = await evaluator(_frozen(), ())

    assert (result.status, result.reason) == (status, reason)
    assert semantic_coverage_gap_code(result.status, result.reason) == gap
    assert privacy.calls == 0
    _assert_record(tmp_path, operation, reason)


@pytest.mark.anyio
async def test_provider_unbound_records_not_configured_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(diagnostics_module, "log_dir", lambda: tmp_path)
    evaluator = cast(
        _SemanticEvaluator,
        getattr(ready_composition_module, "_semantic_provider_unbound"),
    )

    result = await evaluator(_frozen(), ())

    assert (result.status, result.reason) == (
        SemanticStatus.NOT_CONFIGURED,
        SemanticReason.PROVIDER_NOT_CONFIGURED,
    )
    assert semantic_coverage_gap_code(result.status, result.reason) == (
        SEMANTIC_REVIEW_NOT_CONFIGURED_GAP
    )
    _assert_record(
        tmp_path,
        "semantic_not_dispatched_provider_unbound",
        SemanticReason.PROVIDER_NOT_CONFIGURED,
    )


@pytest.mark.anyio
async def test_provider_binding_is_re_resolved_without_rebuilding_evaluator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(diagnostics_module, "log_dir", lambda: tmp_path)
    privacy = _Privacy()
    connected: ProviderBinding | None = None

    def resolve() -> ProviderBinding | None:
        return connected

    evaluator = _evaluator(privacy, resolve, _route())
    unavailable = await evaluator(_frozen(), ())
    connected = _PROVIDER
    dispatched = await evaluator(_frozen(), ())

    assert (unavailable.status, unavailable.reason) == (
        SemanticStatus.UNAVAILABLE,
        SemanticReason.CREDENTIAL_UNAVAILABLE,
    )
    assert (dispatched.status, dispatched.reason) == (
        SemanticStatus.AWAITING_HUMAN,
        SemanticReason.HUMAN_APPROVAL_REQUIRED,
    )
    assert privacy.calls == 1
    assert [record["operation"] for record in _records(tmp_path)] == [
        "semantic_not_dispatched_credential_unavailable"
    ]


@pytest.mark.anyio
async def test_provider_resolution_failure_stays_inside_composition_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(diagnostics_module, "log_dir", lambda: tmp_path)
    privacy = _Privacy()

    def resolve() -> ProviderBinding | None:
        raise RuntimeError("resolver-detail-must-not-leak")

    evaluator = _evaluator(privacy, resolve, _route())

    result = await evaluator(_frozen(), ())

    assert (result.status, result.reason) == (
        SemanticStatus.FAILED,
        SemanticReason.COORDINATOR_FAILURE,
    )
    assert semantic_coverage_gap_code(result.status, result.reason) == (
        SEMANTIC_RELEVANCE_REVIEW_NOT_RUN_GAP
    )
    records = _records(tmp_path)
    assert len(records) == 1
    assert records[0]["component"] == "semantic_composition"
    assert records[0]["operation"] == "semantic_evaluation_failed"
    assert records[0]["reason"] == "exception_runtime_error"
    assert records[0]["request_id"] == _REQUEST
    raw = diagnostics_module.diagnostic_log_path(root=tmp_path).read_text(encoding="ascii")
    assert "resolver-detail-must-not-leak" not in raw


@pytest.mark.anyio
@pytest.mark.parametrize(
    "failure_class",
    (
        "route_commitment_absent",
        "authority_capability_absent",
        "policy_store_failure",
        "invalid_effective_policy",
        "repository_mismatch",
        "coordinator_closed",
        "effective_policy_unbound",
        "reconcile_capability_absent",
        "reconciliation_failure",
    ),
)
async def test_only_explicit_trusted_missing_authority_advertises_repository_setup(
    failure_class: str,
) -> None:
    privacy = _Privacy(repository_granted=True)
    route = _route()
    provider_resolutions = 0

    def resolve() -> ProviderBinding | None:
        nonlocal provider_resolutions
        provider_resolutions += 1
        return _PROVIDER

    if failure_class == "route_commitment_absent":
        route = replace(route, repository_privacy_commitment=None)
    elif failure_class == "authority_capability_absent":
        object.__setattr__(privacy, "policy_application", None)
    elif failure_class in {"policy_store_failure", "invalid_effective_policy"}:

        async def authority_failure(scope: AuthorizationScope) -> RepositoryPrivacyAuthority:
            del scope
            raise RuntimeError(failure_class)

        privacy.policy_application.policy_store.repository_authority = authority_failure
    elif failure_class == "repository_mismatch":
        original = privacy.policy_application.policy_store.repository_authority

        async def mismatched(scope: AuthorizationScope) -> RepositoryPrivacyAuthority:
            authority = await original(scope)
            return replace(
                authority,
                scope=AuthorizationScope(
                    AuthorizationScopeKind.TASK,
                    _INSTALLATION,
                    _REPOSITORY,
                    "tsk_53000000-0000-4000-8000-000000000099",
                ),
            )

        privacy.policy_application.policy_store.repository_authority = mismatched
    elif failure_class in {
        "coordinator_closed",
        "effective_policy_unbound",
        "reconcile_capability_absent",
    }:

        async def activation_unavailable(scope: AuthorizationScope) -> bool:
            del scope
            return False

        privacy.activate_repository = activation_unavailable
    else:

        async def activation_failure(scope: AuthorizationScope) -> bool:
            del scope
            raise RuntimeError("reconciliation_failure")

        privacy.activate_repository = activation_failure

    result = await _evaluator(privacy, resolve, route)(_frozen(), ())

    assert (result.status, result.reason) == (
        SemanticStatus.BLOCKED_BY_POLICY,
        SemanticReason.SCOPE_NOT_AUTHORIZED,
    )
    assert result.continuation is None
    assert provider_resolutions == 0
    assert privacy.calls == 0
