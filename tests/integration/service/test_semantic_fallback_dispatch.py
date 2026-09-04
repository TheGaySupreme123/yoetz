"""Composition-level dispatch through a declared fallback endpoint (issue #582).

Drives the real ``_privacy_gated_semantic_evaluator`` over a durable ledger with a fake privacy
coordinator that answers per destination, so the test observes exactly which binding each
physical attempt was built against, what the check finally reports, and how the provenance names
the primary the fallback stood in for.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime, timedelta
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
from yoetz.domain.privacy import PrivacyOutcome, PrivacyReason, ProviderBinding
from yoetz.ports.ledger import AttemptOutcome, FrozenCase
from yoetz.ports.objects import ObjectKind, ObjectMetadata, ObjectSource
from yoetz.ports.runtime import TaskRuntime
from yoetz.ports.semantic import (
    Deadline,
    ProviderAttemptProvenance,
    SemanticJudgment,
    SemanticResultInvalid,
    SemanticResultSuccess,
    SemanticResultUnavailable,
)
from yoetz.ports.start_catalog import StartCatalogPort
from yoetz.protocol.canonical import JsonValue, canonical_encode, strict_json_parse
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
    clock: FixedClock | None = None,
    primary_retries: int = 2,
    fallback_retries: int = 2,
    fallback_binding: ProviderBinding | None = _FALLBACK,
    primary_timeout: int = 60,
    fallback_timeout: int = 60,
    primary_binding: ProviderBinding = _PROVIDER,
) -> _Evaluator:
    async def resolve_provider() -> ProviderBinding | None:
        return primary_binding if primary_resolves else None

    async def resolve_fallback() -> ProviderBinding | None:
        return fallback_binding

    factory = cast(
        "Callable[..., _Evaluator]",
        getattr(ready_composition_module, "_privacy_gated_semantic_evaluator"),
    )
    return factory(
        cast(PrivacyCoordinator, privacy),
        clock or FixedClock(),
        _INSTALLATION,
        resolve_provider,
        cast(StartCatalogPort, _Catalog(_route_for(runtime.task_id, runtime.session_id))),
        ready_composition_module.IdPort(),
        timeout_seconds=primary_timeout,
        max_retries=primary_retries,
        resolve_fallback=resolve_fallback,
        fallback_timeout_seconds=fallback_timeout,
        fallback_max_retries=fallback_retries,
        configured_primary=primary_binding,
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


class _MovingClock(FixedClock):
    elapsed = 0.0

    def now_utc(self) -> datetime:
        return super().now_utc() + timedelta(seconds=self.elapsed)

    def monotonic_seconds(self) -> float:
        return super().monotonic_seconds() + self.elapsed


class _WaitingFallback(_PairedPrivacy):
    def __init__(self, task_id: str, clock: _MovingClock) -> None:
        super().__init__(task_id=task_id)
        self.clock = clock
        self.budgets: list[float] = []

    async def evaluate_semantic(self, candidate: object, deadline: object) -> object:
        assert type(deadline) is Deadline
        self.budgets.append(deadline.remaining_seconds(self.clock.monotonic_seconds()))
        if getattr(candidate, "provider_binding") == _FALLBACK:
            self.bindings.append(_FALLBACK)
            return await _Privacy.evaluate_semantic(self, candidate, deadline)
        return await super().evaluate_semantic(candidate, deadline)

    async def resume(self, request_id: str, case_digest: str, deadline: object) -> object:
        assert type(deadline) is Deadline
        self.budgets.append(deadline.remaining_seconds(self.clock.monotonic_seconds()))
        return await super().resume(request_id, case_digest, deadline)


@pytest.mark.anyio
@pytest.mark.parametrize("primary_resolves", (True, False))
@pytest.mark.parametrize("fallback_disappears", (True, False))
@pytest.mark.parametrize(
    "adapter_factory", (memory_adapter, sqlite_adapter), ids=("memory", "sqlite")
)
async def test_replay_keeps_frozen_readiness_binding_budgets_and_time(
    primary_resolves: bool,
    fallback_disappears: bool,
    adapter_factory: Callable[[object], MemoryLedgerAdapter | SqliteLedger],
) -> None:
    adapter = adapter_factory(append_command())
    clock = _MovingClock()
    adapter._clock = clock  # pyright: ignore[reportPrivateUsage]
    frozen, runtime = await _durable_semantic_case(adapter)
    privacy = _WaitingFallback(runtime.task_id, clock)
    waiting = await _paired_evaluator(
        privacy, runtime, primary_resolves=primary_resolves, clock=clock
    )(frozen, (), runtime)
    assert waiting.status is SemanticStatus.AWAITING_HUMAN
    assert waiting.operation_lease is not None
    primary_count = 2 if primary_resolves else 0
    assert privacy.bindings == [_PROVIDER] * primary_count + [_FALLBACK]
    assert privacy.budgets == [60.0] * (primary_count + 1)

    clock.elapsed = 20.0
    if isinstance(adapter, SqliteLedger):
        # Rehydrate the exact durable attempt timestamps and encrypted case after restart.
        restarted = SqliteLedger(
            db=adapter._db,  # pyright: ignore[reportPrivateUsage]
            task_id=runtime.task_id,
            ownership_fence=runtime.fence,
            clock=clock,
            ids=adapter._ids,  # pyright: ignore[reportPrivateUsage]
            objects=adapter._objects,  # pyright: ignore[reportPrivateUsage]
        )
        runtime = replace(runtime, ledger=restarted)
    privacy.resume_terminal = (PrivacyOutcome.HUMAN_DENIED, PrivacyReason.HUMAN_DENIED)
    changed_binding = ProviderBinding(
        "changed-provider", "new-model", "new-profile", "2.0.0", "external"
    )
    resumed = await _paired_evaluator(
        privacy,
        runtime,
        primary_resolves=not primary_resolves,
        clock=clock,
        primary_retries=0,
        fallback_retries=0,
        fallback_binding=None if fallback_disappears else changed_binding,
    )(FrozenCase(frozen.case, waiting.operation_lease), (), runtime)

    assert resumed.status is SemanticStatus.HUMAN_DENIED
    assert privacy.resume_calls == 1
    assert privacy.budgets[-1] == 40.0
    accounting = _accounting(resumed)
    primary, fallback = accounting.endpoint("primary"), accounting.endpoint("fallback")
    assert primary is not None and fallback is not None
    assert primary.attempted_count == primary_count
    assert fallback.attempted_count == 1
    assert fallback.provider_id == _FALLBACK.provider_id
    assert primary.predispatch_reason == (None if primary_resolves else "credential_unavailable")


@pytest.mark.anyio
@pytest.mark.parametrize("primary_retries", (0, 2))
async def test_primary_full_timeout_preserves_fallback_reservation_without_widening_engagement(
    primary_retries: int,
) -> None:
    clock = _MovingClock()
    adapter = memory_adapter(append_command())
    adapter._clock = clock  # pyright: ignore[reportPrivateUsage]
    frozen, runtime = await _durable_semantic_case(adapter)

    class TimedPrivacy(_PairedPrivacy):
        async def evaluate_semantic(self, candidate: object, deadline: object) -> object:
            assert type(deadline) is Deadline
            if getattr(candidate, "provider_binding") == _PROVIDER:
                assert deadline.remaining_seconds(clock.monotonic_seconds()) == 10.0
                clock.elapsed += 10.0
            else:
                assert deadline.remaining_seconds(clock.monotonic_seconds()) == 60.0
            return await super().evaluate_semantic(candidate, deadline)

    privacy = TimedPrivacy(task_id=runtime.task_id)
    result = await _paired_evaluator(
        privacy, runtime, clock=clock, primary_retries=primary_retries, primary_timeout=10
    )(frozen, (), runtime)
    assert privacy.bindings == ([_PROVIDER, _FALLBACK] if primary_retries == 0 else [_PROVIDER])
    assert result.status is (
        SemanticStatus.SUCCEEDED if primary_retries == 0 else SemanticStatus.UNAVAILABLE
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "state",
    ("succeeded", "awaiting", "uncertain", "timeout", "refused", "invalid", "retained_invalid"),
)
async def test_legacy_case_recovers_selected_result_or_retires_wait_without_dispatch(
    state: str,
) -> None:
    terminal = state == "succeeded"
    adapter = memory_adapter(append_command())
    frozen, runtime = await _durable_semantic_case(adapter)
    privacy = _WaitingFallback(runtime.task_id, _MovingClock())
    if terminal or state in {"invalid", "retained_invalid"}:
        privacy = _PairedPrivacy(task_id=runtime.task_id, primary_content_invalid=not terminal)
    evaluator = _paired_evaluator(
        privacy,
        runtime,
        primary_binding=_PROVIDER if state in {"invalid", "retained_invalid"} else _FALLBACK,
        fallback_binding=None,
    )
    original = await evaluator(frozen, (), runtime)
    assert original.status is (
        SemanticStatus.SUCCEEDED
        if terminal
        else SemanticStatus.INVALID
        if state in {"invalid", "retained_invalid"}
        else SemanticStatus.AWAITING_HUMAN
    )
    assert original.operation_lease is not None
    job = await adapter.load_semantic_job(frozen.lease.writer_id, frozen.lease.operation_id)
    assert job is not None
    terminal_reason = {
        "timeout": SemanticReason.PROVIDER_TIMEOUT,
        "refused": SemanticReason.PROVIDER_REFUSED,
    }.get(state)
    if terminal_reason is not None:
        handle = await adapter.claim_semantic_job(original.operation_lease, job.job_id)
        await adapter.record_attempt_outcome(
            handle, AttemptOutcome.FAILED, terminal_code=terminal_reason
        )
        job = await adapter.load_semantic_job(frozen.lease.writer_id, frozen.lease.operation_id)
        assert job is not None
    if state == "retained_invalid":
        rows = await adapter.list_semantic_attempts(job.job_id)
        response = await ready_composition_module._publish_semantic_response_object(  # pyright: ignore[reportPrivateUsage]
            runtime, attempt_id=rows[-1].attempt_id, evaluation=original, clock=FixedClock()
        )
        prior = adapter._state.attempts[rows[-1].attempt_id]  # pyright: ignore[reportPrivateUsage]
        adapter._state.attempts[rows[-1].attempt_id] = replace(prior, result_object_ref=response)  # pyright: ignore[reportPrivateUsage]
    resolved = await runtime.objects.resolve_verified(
        job.case_object_ref.object_id, job.case_object_ref.envelope_digest
    )
    payload = b"".join([chunk async for chunk in runtime.objects.open_verified(resolved)])
    parsed = strict_json_parse(payload)
    assert type(parsed) is dict
    body = cast(dict[str, JsonValue], parsed)
    body["schema"] = "yoetz.semantic-case/1"
    body.pop("execution")
    legacy_payload = canonical_encode(body)
    staged = await runtime.objects.stage(
        ObjectSource(data=legacy_payload, declared_size=len(legacy_payload)),
        ObjectMetadata(
            ObjectKind.SEMANTIC_CASE, "application/json", runtime.task_id, FixedClock().now_utc()
        ),
    )
    legacy_ref = await runtime.objects.finalize(staged)
    adapter._state.jobs[job.job_id] = replace(job, case_object_ref=legacy_ref)  # pyright: ignore[reportPrivateUsage]
    if state == "uncertain":
        adapter._state.disclosure_waits.clear()  # pyright: ignore[reportPrivateUsage]
    calls_before = cast(int, getattr(privacy, "calls"))

    recovered = await evaluator(FrozenCase(frozen.case, original.operation_lease), (), runtime)

    assert getattr(privacy, "calls") == calls_before
    expected_status = (
        SemanticStatus.SUCCEEDED
        if terminal
        else SemanticStatus.INVALID
        if state == "retained_invalid"
        else SemanticStatus.UNAVAILABLE
        if state in {"uncertain", "timeout", "refused", "invalid"}
        else SemanticStatus.FAILED
    )
    assert recovered.status is expected_status
    if terminal or state == "retained_invalid":
        assert recovered.judgment == original.judgment
        assert recovered.provenance == original.provenance
    else:
        assert recovered.reason is (
            SemanticReason.RECEIPT_PERSISTENCE_UNKNOWN
            if state in {"uncertain", "timeout", "refused", "invalid"}
            else SemanticReason.COORDINATOR_FAILURE
        )
    retired = await adapter.load_semantic_job(frozen.lease.writer_id, frozen.lease.operation_id)
    assert retired is not None and retired.state == ("succeeded" if terminal else "failed")
    if state == "uncertain":
        assert retired.terminal_code is SemanticReason.OUTCOME_UNKNOWN


@pytest.mark.anyio
@pytest.mark.parametrize("uncertain", (False, True))
async def test_expired_resumed_attempt_preserves_dispatch_uncertainty(uncertain: bool) -> None:
    clock = _MovingClock()
    adapter = memory_adapter(append_command())
    adapter._clock = clock  # pyright: ignore[reportPrivateUsage]
    frozen, runtime = await _durable_semantic_case(adapter)
    privacy = _WaitingFallback(runtime.task_id, clock)
    evaluator = _paired_evaluator(privacy, runtime, clock=clock, fallback_timeout=10)
    waiting = await evaluator(frozen, (), runtime)
    assert waiting.status is SemanticStatus.AWAITING_HUMAN
    assert waiting.operation_lease is not None
    if uncertain:
        adapter._state.disclosure_waits.clear()  # pyright: ignore[reportPrivateUsage]
    calls = cast(int, getattr(privacy, "calls"))
    clock.elapsed = 11.0
    result = await evaluator(FrozenCase(frozen.case, waiting.operation_lease), (), runtime)
    assert result.reason is SemanticReason.RECEIPT_PERSISTENCE_UNKNOWN
    assert result.provenance is None
    assert getattr(privacy, "calls") == calls
    job = await adapter.load_semantic_job(frozen.lease.writer_id, frozen.lease.operation_id)
    assert job is not None and job.state == "failed"
    assert job.terminal_code is (
        SemanticReason.OUTCOME_UNKNOWN if uncertain else SemanticReason.PROVIDER_TIMEOUT
    )
    assert result.operation_lease is not None
    recovered = await evaluator(FrozenCase(frozen.case, result.operation_lease), (), runtime)
    assert recovered.reason is SemanticReason.RECEIPT_PERSISTENCE_UNKNOWN
    assert getattr(privacy, "calls") == calls
