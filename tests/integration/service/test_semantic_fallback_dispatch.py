"""Composition-level dispatch through a declared fallback endpoint (issue #582).

Drives the real ``_privacy_gated_semantic_evaluator`` over a durable ledger with a fake privacy
coordinator that answers per destination, so the test observes exactly which binding each
physical attempt was built against, what the check finally reports, and how the provenance names
the primary the fallback stood in for.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import cast

import pytest

import integration.service.test_semantic_non_dispatch as non_dispatch
import yoetz.service.ready_composition as ready_composition_module
from builders.ledger_adapters import FixedClock, append_command, memory_adapter, sqlite_adapter
from yoetz.adapters.memory.ledger import MemoryLedgerAdapter
from yoetz.adapters.sqlite.repository import SqliteLedger
from yoetz.application.check import FinalSemanticEvaluation
from yoetz.application.egress import (
    PrivacyCoordinator,
    SemanticEgressProviderOutcome,
    SemanticEgressSuccess,
)
from yoetz.application.semantic_attempts import SemanticAttemptAccounting
from yoetz.domain.findings import (
    SamplingParams,
    SemanticDispatchKind,
    SemanticFailureClass,
    SemanticFallbackOrigin,
)
from yoetz.domain.privacy import ProviderBinding
from yoetz.ports.ledger import FrozenCase
from yoetz.ports.runtime import TaskRuntime
from yoetz.ports.semantic import (
    ProviderAttemptProvenance,
    SemanticJudgment,
    SemanticResultInvalid,
    SemanticResultSuccess,
    SemanticResultUnavailable,
)
from yoetz.ports.start_catalog import StartCatalogPort
from yoetz.protocol.models import SemanticReason, SemanticStatus

# The single-endpoint harness owns the durable case, route, catalog, and coordinator fakes; this
# module only adds the second destination, so it reuses them rather than duplicating them.
_INSTALLATION = non_dispatch._INSTALLATION  # pyright: ignore[reportPrivateUsage]
_PROVIDER = non_dispatch._PROVIDER  # pyright: ignore[reportPrivateUsage]
_Catalog = non_dispatch._Catalog  # pyright: ignore[reportPrivateUsage]
_Privacy = non_dispatch._Privacy  # pyright: ignore[reportPrivateUsage]
_durable_semantic_case = non_dispatch._durable_semantic_case  # pyright: ignore[reportPrivateUsage]
_route_for = non_dispatch._route_for  # pyright: ignore[reportPrivateUsage]

_FALLBACK = ProviderBinding(
    "fallback-provider",
    "fallback-model",
    "fallback-profile",
    "1.0.0",
    "external",
)

type _Evaluator = Callable[
    [FrozenCase, tuple[object, ...], TaskRuntime], Awaitable[FinalSemanticEvaluation]
]


def _provenance(
    binding: ProviderBinding,
    *,
    status: SemanticStatus,
    failure_class: SemanticFailureClass | None,
) -> ProviderAttemptProvenance:
    return ProviderAttemptProvenance(
        provider=binding.provider_id,
        endpoint_profile_id=binding.endpoint_profile_id,
        endpoint_profile_version=binding.endpoint_profile_version,
        model=binding.model_id,
        sdk_version="1.0.0",
        prompt_digest="sha256:" + "1" * 64,
        schema_digest="sha256:" + "2" * 64,
        policy_digest="sha256:" + "3" * 64,
        privacy_policy_digest="sha256:" + "4" * 64,
        sampling_params=SamplingParams(128),
        latency_ms=1,
        status=status,
        failure_class=failure_class,
    )


class _PairedPrivacy(_Privacy):
    """Answers the primary with a scripted failure and the fallback with a judgment."""

    def __init__(
        self,
        *,
        task_id: str,
        primary_failure_class: SemanticFailureClass = SemanticFailureClass.TRANSPORT,
        primary_content_invalid: bool = False,
    ) -> None:
        super().__init__(task_id=task_id)
        self.bindings: list[ProviderBinding] = []
        self.primary_failure_class = primary_failure_class
        self.primary_content_invalid = primary_content_invalid

    async def evaluate_semantic(self, candidate: object, deadline: object) -> object:
        del deadline
        self.calls += 1
        binding = cast(ProviderBinding, getattr(candidate, "provider_binding"))
        request_id = cast(str, getattr(candidate, "request_id"))
        self.bindings.append(binding)
        ordinal = len(self.bindings)
        proposal = f"ppr_53000000-0000-4000-8000-00000000{ordinal:04d}"
        authorization = f"aut_53000000-0000-4000-8000-00000000{ordinal:04d}"
        receipt = f"egr_53000000-0000-4000-8000-00000000{ordinal:04d}"
        if binding == _PROVIDER:
            if self.primary_content_invalid:
                result: object = SemanticResultInvalid(
                    _provenance(
                        binding,
                        status=SemanticStatus.INVALID,
                        failure_class=SemanticFailureClass.RESPONSE_SCHEMA,
                    ),
                    12,
                )
            else:
                result = SemanticResultUnavailable(
                    _provenance(
                        binding,
                        status=SemanticStatus.UNAVAILABLE,
                        failure_class=self.primary_failure_class,
                    )
                )
            return SemanticEgressProviderOutcome(
                request_id=request_id,
                privacy_proposal_id=proposal,
                authorization_id=authorization,
                dispatch_kind=SemanticDispatchKind.EXTERNAL,
                result=cast(SemanticResultUnavailable, result),
                case_digest="sha256:" + "5" * 64,
                privacy_receipt_id=receipt,
                request_commitment="hmac-sha256:" + "6" * 64,
            )
        assert binding == _FALLBACK
        return SemanticEgressSuccess(
            request_id=request_id,
            privacy_proposal_id=proposal,
            authorization_id=authorization,
            dispatch_kind=SemanticDispatchKind.EXTERNAL,
            result=SemanticResultSuccess(
                SemanticJudgment("no_material_discrepancy", ()),
                _provenance(binding, status=SemanticStatus.SUCCEEDED, failure_class=None),
            ),
            case_digest="sha256:" + "5" * 64,
            privacy_receipt_id=receipt,
            request_commitment="hmac-sha256:" + "6" * 64,
        )


def _paired_evaluator(
    privacy: _PairedPrivacy,
    runtime: TaskRuntime,
    *,
    primary_resolves: bool = True,
) -> _Evaluator:
    async def resolve_provider() -> ProviderBinding | None:
        return _PROVIDER if primary_resolves else None

    async def resolve_fallback() -> ProviderBinding | None:
        return _FALLBACK

    factory = cast(
        "Callable[..., _Evaluator]",
        getattr(ready_composition_module, "_privacy_gated_semantic_evaluator"),
    )
    return factory(
        cast(PrivacyCoordinator, privacy),
        FixedClock(),
        _INSTALLATION,
        resolve_provider,
        cast(StartCatalogPort, _Catalog(_route_for(runtime.task_id, runtime.session_id))),
        ready_composition_module.IdPort(),
        timeout_seconds=60,
        max_retries=2,
        resolve_fallback=resolve_fallback,
        fallback_timeout_seconds=60,
        fallback_max_retries=2,
        configured_primary=_PROVIDER,
    )


def _accounting(result: FinalSemanticEvaluation) -> SemanticAttemptAccounting:
    accounting = result.attempt_accounting
    assert type(accounting) is SemanticAttemptAccounting
    return accounting


@pytest.mark.anyio
@pytest.mark.parametrize(
    "adapter_factory",
    (memory_adapter, sqlite_adapter),
    ids=("memory", "sqlite"),
)
async def test_two_primary_failures_hand_the_same_case_to_the_fallback(
    adapter_factory: Callable[[object], MemoryLedgerAdapter | SqliteLedger],
) -> None:
    adapter = adapter_factory(append_command())
    frozen, runtime = await _durable_semantic_case(adapter)
    privacy = _PairedPrivacy(task_id=runtime.task_id)

    result = await _paired_evaluator(privacy, runtime)(frozen, (), runtime)

    assert privacy.bindings == [_PROVIDER, _PROVIDER, _FALLBACK]
    assert (result.status, result.reason) == (
        SemanticStatus.SUCCEEDED,
        SemanticReason.SEMANTIC_COMPLETED,
    )
    provenance = result.provenance
    assert provenance is not None
    assert provenance.provider == _FALLBACK.provider_id
    assert provenance.model == _FALLBACK.model_id
    assert provenance.fallback_from == SemanticFallbackOrigin(
        provider=_PROVIDER.provider_id,
        endpoint_profile_id=_PROVIDER.endpoint_profile_id,
        endpoint_profile_version=_PROVIDER.endpoint_profile_version,
        model=_PROVIDER.model_id,
        attempted_count=2,
        reason=SemanticReason.TRANSPORT_UNAVAILABLE,
    )
    accounting = _accounting(result)
    assert accounting.attempted_count == 3
    primary = accounting.endpoint("primary")
    fallback = accounting.endpoint("fallback")
    assert primary is not None and fallback is not None
    assert primary.attempted_count == 2
    assert primary.terminal_reason_counts == (("transport_unavailable", 2),)
    assert primary.last_terminal_reason == "transport_unavailable"
    assert fallback.attempted_count == 1
    assert fallback.terminal_reason_counts == (("semantic_completed", 1),)
    job = await adapter.load_semantic_job(frozen.lease.writer_id, frozen.lease.operation_id)
    assert job is not None
    rows = await adapter.list_semantic_attempts(job.job_id)
    assert [row.state for row in sorted(rows, key=lambda row: row.attempt_ordinal)] == [
        "expired",
        "expired",
        "selected",
    ]


@pytest.mark.anyio
async def test_quota_exhaustion_engages_the_fallback_after_one_primary_attempt() -> None:
    adapter = memory_adapter(append_command())
    frozen, runtime = await _durable_semantic_case(adapter)
    privacy = _PairedPrivacy(
        task_id=runtime.task_id,
        primary_failure_class=SemanticFailureClass.QUOTA_EXHAUSTED,
    )

    result = await _paired_evaluator(privacy, runtime)(frozen, (), runtime)

    assert privacy.bindings == [_PROVIDER, _FALLBACK]
    assert result.status is SemanticStatus.SUCCEEDED
    assert result.provenance is not None
    assert result.provenance.fallback_from is not None
    assert result.provenance.fallback_from.reason is SemanticReason.PROVIDER_QUOTA_EXHAUSTED
    assert result.provenance.fallback_from.attempted_count == 1


@pytest.mark.anyio
async def test_content_shaped_primary_failure_never_reaches_the_fallback() -> None:
    adapter = memory_adapter(append_command())
    frozen, runtime = await _durable_semantic_case(adapter)
    privacy = _PairedPrivacy(task_id=runtime.task_id, primary_content_invalid=True)

    result = await _paired_evaluator(privacy, runtime)(frozen, (), runtime)

    assert privacy.bindings == [_PROVIDER]
    assert (result.status, result.reason) == (
        SemanticStatus.INVALID,
        SemanticReason.RESPONSE_SCHEMA_INVALID,
    )
    assert result.provenance is not None
    assert result.provenance.provider == _PROVIDER.provider_id
    assert result.provenance.fallback_from is None
    accounting = _accounting(result)
    fallback = accounting.endpoint("fallback")
    assert fallback is not None and fallback.attempted_count == 0


@pytest.mark.anyio
async def test_unresolvable_primary_is_named_with_zero_attempts_on_fallback_provenance() -> None:
    adapter = memory_adapter(append_command())
    frozen, runtime = await _durable_semantic_case(adapter)
    privacy = _PairedPrivacy(task_id=runtime.task_id)

    result = await _paired_evaluator(privacy, runtime, primary_resolves=False)(frozen, (), runtime)

    assert privacy.bindings == [_FALLBACK]
    assert result.status is SemanticStatus.SUCCEEDED
    assert result.provenance is not None
    assert result.provenance.fallback_from == SemanticFallbackOrigin(
        provider=_PROVIDER.provider_id,
        endpoint_profile_id=_PROVIDER.endpoint_profile_id,
        endpoint_profile_version=_PROVIDER.endpoint_profile_version,
        model=_PROVIDER.model_id,
        attempted_count=0,
        reason=SemanticReason.CREDENTIAL_UNAVAILABLE,
    )
    primary = _accounting(result).endpoint("primary")
    assert primary is not None
    assert primary.attempted_count == 0
    assert primary.predispatch_reason == "credential_unavailable"
