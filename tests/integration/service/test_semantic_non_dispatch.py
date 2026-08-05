from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

import yoetz.observability.diagnostics as diagnostics_module
import yoetz.service.ready_composition as ready_composition_module
from builders.ledger_adapters import FixedClock
from builders.policy_cases import FRONTIER, make_case
from yoetz.application.check import FinalSemanticEvaluation, semantic_coverage_gap_code
from yoetz.application.egress import PrivacyCoordinator, SemanticEgressAwaitingHuman
from yoetz.domain.privacy import (
    AuthorizationScope,
    AuthorizationScopeKind,
    ChannelPolicy,
    DataClass,
    EgressChannel,
    PrivacyPolicy,
    PrivacyProfile,
    ProviderBinding,
    ReviewContextProfile,
    ReviewSelectionPolicy,
)
from yoetz.domain.receipts import (
    SEMANTIC_RELEVANCE_REVIEW_NOT_RUN_GAP,
    SEMANTIC_REVIEW_NOT_CONFIGURED_GAP,
)
from yoetz.ports.keys import MacKeyHandle
from yoetz.ports.ledger import CheckPhase, FrozenCase, OperationLease
from yoetz.ports.privacy import EffectivePrivacyPolicy
from yoetz.ports.start_catalog import StartCatalogPort, TaskRoute, TaskRouteState
from yoetz.protocol.canonical import canonical_digest
from yoetz.protocol.models import DataCategory, SemanticReason, SemanticStatus

_TASK = "tsk_53000000-0000-4000-8000-000000000001"
_SESSION = "ses_53000000-0000-4000-8000-000000000001"
_WRITER = "wri_53000000-0000-4000-8000-000000000001"
_REQUEST = "req_53000000-0000-4000-8000-000000000001"
_INSTALLATION = "ins_53000000-0000-4000-8000-000000000001"
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
        "hmac-sha256:" + "b" * 64,
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
    def __init__(self, effective: EffectivePrivacyPolicy) -> None:
        self._effective = effective

    async def effective_policy(self, scope: AuthorizationScope) -> EffectivePrivacyPolicy:
        del scope
        return self._effective


class _PolicyApplication:
    def __init__(self, effective: EffectivePrivacyPolicy) -> None:
        self.policy_store = _PolicyStore(effective)


class _Privacy:
    def __init__(self) -> None:
        self.calls = 0
        # Real policy path is required for dispatch; never mint synthetic policy identity.
        self.policy_application = _PolicyApplication(_test_effective_policy())

    async def evaluate_semantic(self, candidate: object, deadline: object) -> object:
        del deadline
        self.calls += 1
        request_id = cast(str, getattr(candidate, "request_id"))
        return SemanticEgressAwaitingHuman(
            request_id,
            "ppr_53000000-0000-4000-8000-000000000001",
            "sha256:" + "a" * 64,
            datetime(2030, 1, 1, tzinfo=UTC),
        )


class _Catalog:
    def __init__(self, route: TaskRoute | None) -> None:
        self.route = route

    async def resolve_route(self, session: str) -> TaskRoute | None:
        assert session == _SESSION
        return self.route


class _Lookup:
    def mac(self, domain: bytes, message: bytes) -> str:
        del domain, message
        return "hmac-sha256:" + "b" * 64


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
    return TaskRoute(_TASK, _SESSION, bundle_relpath, route_generation, state, identity)


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
        cast(MacKeyHandle, _Lookup()),
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
