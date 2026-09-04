"""Central provider-free privacy and disclosure coordinator."""

from __future__ import annotations

import asyncio
import base64
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from yoetz.domain.findings import SemanticDispatchKind
from yoetz.domain.privacy import (
    ApprovedLocalDisclosureCase,
    ApprovedLocalItem,
    ApprovedOutboundCase,
    AuthorizationScope,
    AuthorizationScopeKind,
    CandidateContext,
    ClassifiedContext,
    ConsentSource,
    DataCategory,
    DataClass,
    DisclosureProposal,
    DisclosureProvenance,
    EgressAuthorization,
    EgressChannel,
    EgressReceipt,
    HumanPrivacyDecision,
    LocalDisclosureApproved,
    LocalDisclosureBlocked,
    LocalDisclosureOmission,
    LocalDisclosureReceipt,
    LocalDisclosureSink,
    LocalDisclosureUnavailable,
    PreDispatchAuditDecision,
    PrivacyDecision,
    PrivacyOutcome,
    PrivacyProfile,
    PrivacyReason,
    ProviderBinding,
    ProviderDataUseProfile,
    ReceiptCounts,
    ReceiptPolicyBinding,
    ReceiptSecretScan,
    ReceiptTransformations,
)
from yoetz.observability.logging import record_unexpected_exception_without_raising
from yoetz.ports.clock import ClockPort
from yoetz.ports.ids import IdPort
from yoetz.ports.privacy import (
    AgentProjectionRequest,
    DisclosureProposalRequest,
    EffectivePrivacyPolicy,
    HumanAuthorityCapability,
    HumanPrivacyControlPort,
    MinimizedDisclosure,
    OutboundGatewayPort,
    PendingHumanDecision,
    PrivacyAuditPort,
    PrivacyClassifierPort,
    PrivacyPolicyStorePort,
    RepositoryPrivacyAuthority,
)
from yoetz.ports.semantic import (
    Deadline,
    SemanticResult,
    SemanticResultInvalid,
    SemanticResultLate,
    SemanticResultRefused,
    SemanticResultSuccess,
    SemanticResultTimeout,
    SemanticResultUnavailable,
)
from yoetz.protocol.canonical import canonical_digest, canonical_encode
from yoetz.protocol.ids import IdKind

if TYPE_CHECKING:
    from yoetz.application.privacy_policy import PrivacyPolicyApplication

__all__ = [
    "PrivacyCoordinator",
    "RepositoryGrantAdmission",
    "SemanticEgressAwaitingHuman",
    "SemanticEgressBlocked",
    "SemanticEgressProviderOutcome",
    "SemanticEgressResult",
    "SemanticEgressSuccess",
]

type LocalDisclosureResult = (
    LocalDisclosureApproved | LocalDisclosureBlocked | LocalDisclosureUnavailable
)


class RepositoryGrantAdmission(StrEnum):
    """Admission-locked repository authority outcome for check composition.

    ``MISSING`` is deliberately the sole nonterminal handoff: the coordinator observed an exactly
    bound, missing standing grant while still open. All other states are fail-closed so callers
    cannot turn an unavailable coordinator or malformed policy state into a trusted ceremony.
    """

    MISSING = "missing"
    GRANTED = "granted"
    UNAVAILABLE = "unavailable"


# This value crosses the privacy-policy boundary, so it must match the
# documented/recipe vocabulary and the stored provider-credential binding.
_SEMANTIC_PURPOSE = "semantic-review"
_CREDENTIAL_PROBE_PURPOSE = "credential-probe"
_MEDIA_TYPE = "application/json"
_SCHEMA_ID = "yoetz-semantic-case-1.0.0"
# Breadth order, matching ``_scope_rank`` in ``yoetz.application.privacy_policy``: a lower rank
# is a *broader* scope. ``machine`` is therefore the widest authorization ceiling a channel can
# carry and ``request`` the narrowest, which is why moving a ceiling from ``task`` to ``machine``
# is classified as ``scope_ceiling_broadened`` by the widen/tighten ceremony.
_SCOPE_KIND_RANK = {
    AuthorizationScopeKind.MACHINE: 0,
    AuthorizationScopeKind.WORKSPACE: 1,
    AuthorizationScopeKind.TASK: 2,
    AuthorizationScopeKind.REQUEST: 3,
}


@dataclass(frozen=True, slots=True)
class SemanticEgressSuccess:
    """Terminal success: durable attempt receipt exists; judgment may steer a check."""

    request_id: str
    privacy_proposal_id: str
    authorization_id: str | None
    dispatch_kind: SemanticDispatchKind
    result: SemanticResultSuccess
    case_digest: str
    privacy_receipt_id: str | None = None
    request_commitment: str | None = None


@dataclass(frozen=True, slots=True)
class SemanticEgressAwaitingHuman:
    """Nonterminal: exact prepared case awaits local human decision; no findings."""

    request_id: str
    privacy_proposal_id: str
    subject_digest: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class SemanticEgressBlocked:
    """Terminal pre-dispatch or human denial/expiry; never invents semantic findings."""

    request_id: str
    outcome: PrivacyOutcome
    reason: PrivacyReason
    privacy_proposal_id: str | None = None
    receipt_id: str | None = None


@dataclass(frozen=True, slots=True)
class SemanticEgressProviderOutcome:
    """Terminal provider attempt that is not a usable success (refuse/timeout/invalid/…)."""

    request_id: str
    privacy_proposal_id: str
    authorization_id: str | None
    dispatch_kind: SemanticDispatchKind
    result: (
        SemanticResultRefused
        | SemanticResultTimeout
        | SemanticResultInvalid
        | SemanticResultLate
        | SemanticResultUnavailable
    )
    case_digest: str
    privacy_receipt_id: str | None = None
    request_commitment: str | None = None


type SemanticEgressResult = (
    SemanticEgressSuccess
    | SemanticEgressAwaitingHuman
    | SemanticEgressBlocked
    | SemanticEgressProviderOutcome
)


def _semantic_result_outcome(
    result: SemanticResult,
) -> tuple[PrivacyOutcome, PrivacyReason | None]:
    if type(result) is SemanticResultSuccess:
        return PrivacyOutcome.COMPLETED, None
    if type(result) is SemanticResultRefused:
        return PrivacyOutcome.PROVIDER_REFUSED, PrivacyReason.PROVIDER_REFUSED
    if type(result) is SemanticResultTimeout:
        return PrivacyOutcome.TIMEOUT, PrivacyReason.PROVIDER_TIMEOUT
    if type(result) is SemanticResultInvalid:
        return PrivacyOutcome.INVALID_RESPONSE, PrivacyReason.PROVIDER_INVALID_RESPONSE
    if type(result) is SemanticResultLate:
        return PrivacyOutcome.LATE, PrivacyReason.LATE
    if type(result) is SemanticResultUnavailable:
        return PrivacyOutcome.TRANSPORT_FAILED, PrivacyReason.TRANSPORT_FAILED
    raise TypeError("semantic_egress_result_invalid")


def _semantic_local_receipt(
    candidate: CandidateContext,
    effective: EffectivePrivacyPolicy,
    proposal: DisclosureProposal,
    minimized: MinimizedDisclosure,
    consent: ConsentSource,
    outcome: PrivacyOutcome,
    reason: PrivacyReason | None,
    finished_at: datetime,
    ids: IdPort,
) -> LocalDisclosureReceipt:
    included_count = len(minimized.included_item_ids)
    candidate_count = len(minimized.source_item_digests)
    omitted_count = max(0, candidate_count - included_count)
    return LocalDisclosureReceipt(
        "1.0.0",
        ids.new(IdKind.EGRESS_RECEIPT),
        candidate.request_id,
        proposal.privacy_proposal_id,
        LocalDisclosureSink.LOCAL_MODEL,
        outcome,
        finished_at,
        candidate.scope,
        candidate.purpose,
        ReceiptPolicyBinding(
            effective.policy.policy_id,
            effective.policy.version,
            effective.effective_digest,
            _scope_digest(candidate.scope),
        ),
        consent,
        minimized.approved_categories,
        minimized.blocked_categories,
        ReceiptCounts(
            candidate_count,
            included_count,
            omitted_count,
            included_count,
            omitted_count,
            minimized.byte_count,
            minimized.byte_count,
            minimized.token_count,
            None,
        ),
        ReceiptTransformations(omitted_count, 0, omitted_count),
        ReceiptSecretScan(
            minimized.scanner_registry_version,
            minimized.scanner_profile_digest,
            len(minimized.forbidden_findings),
            not minimized.forbidden_findings,
        ),
        reason,
        1,
    )


@dataclass(frozen=True, slots=True)
class _LocalCeiling:
    categories: frozenset[DataCategory]
    data_classes: frozenset[DataClass]


class PrivacyCoordinator:
    """One central admission path for local disclosure and semantic egress."""

    __slots__ = (
        "_audit",
        "_admission_lock",
        "_classifier",
        "_clock",
        "_close_lock",
        "_close_task",
        "_closed",
        "_data_use_resolver",
        "_gateway",
        "_human",
        "_human_authority",
        "_ids",
        "_policies",
        "_policy_app",
        "_service_generation",
    )

    def __init__(
        self,
        policies: PrivacyPolicyStorePort,
        classifier: PrivacyClassifierPort,
        audit: PrivacyAuditPort,
        gateway: OutboundGatewayPort,
        clock: ClockPort,
        ids: IdPort,
        *,
        service_generation: int = 1,
        human: HumanPrivacyControlPort | None = None,
        human_authority: HumanAuthorityCapability | None = None,
        data_use_resolver: Callable[[ProviderBinding], ProviderDataUseProfile | None] | None = None,
    ) -> None:
        if type(service_generation) is not int or service_generation <= 0:
            raise ValueError("privacy_service_generation_invalid")
        self._policies = policies
        self._classifier = classifier
        self._audit = audit
        self._admission_lock = asyncio.Lock()
        self._gateway = gateway
        self._clock = clock
        self._ids = ids
        self._service_generation = service_generation
        self._human = human
        self._human_authority = human_authority
        self._data_use_resolver = data_use_resolver
        self._policy_app: PrivacyPolicyApplication | None = None
        self._closed = False
        self._close_lock = asyncio.Lock()
        self._close_task: asyncio.Task[None] | None = None

    @property
    def policy_application(self) -> PrivacyPolicyApplication | None:
        return self._policy_app

    def bind_policy_application(self, app: PrivacyPolicyApplication) -> None:
        self._policy_app = app

    async def activate_repository(self, scope: AuthorizationScope) -> bool:
        """Lazily activate only the exact repository authority named by a trusted task route."""

        async with self._admission_lock:
            if self._closed:
                return False
            return await self._activate_repository_admitted(scope) is not None

    async def admit_repository_grant(self, scope: AuthorizationScope) -> RepositoryGrantAdmission:
        """Classify and activate exact repository authority under the closure fence.

        This is the only API that may authorize the repository-setup continuation.  It shares the
        coordinator admission lock with ``close`` and semantic dispatch, so a closed coordinator
        or a close racing this lookup is never reported as an actionable missing-grant handoff.
        """

        async with self._admission_lock:
            if self._closed:
                return RepositoryGrantAdmission.UNAVAILABLE
            authority = await self._repository_authority_admitted(scope)
            if authority is None:
                return RepositoryGrantAdmission.UNAVAILABLE
            if authority.grant_state == "missing":
                return RepositoryGrantAdmission.MISSING
            if authority.grant_state != "granted":
                return RepositoryGrantAdmission.UNAVAILABLE
            activated = await self._activate_repository_from_authority_admitted(authority, scope)
            return (
                RepositoryGrantAdmission.GRANTED
                if activated is not None
                else RepositoryGrantAdmission.UNAVAILABLE
            )

    async def evaluate_semantic(
        self, candidate: CandidateContext, deadline: Deadline
    ) -> SemanticEgressResult:
        if type(candidate) is not CandidateContext or type(deadline) is not Deadline:
            raise TypeError("semantic_egress_arguments_invalid")
        async with self._admission_lock:
            if self._closed:
                return SemanticEgressBlocked(
                    candidate.request_id,
                    PrivacyOutcome.CHANNEL_UNAVAILABLE,
                    PrivacyReason.CHANNEL_UNAVAILABLE,
                )
            return await self._evaluate_semantic_admitted(candidate, deadline)

    async def resume(
        self, request_id: str, case_digest: str, deadline: Deadline
    ) -> SemanticEgressResult:
        if (
            type(request_id) is not str
            or type(case_digest) is not str
            or type(deadline) is not Deadline
        ):
            raise TypeError("semantic_egress_resume_invalid")
        async with self._admission_lock:
            if self._closed:
                return SemanticEgressBlocked(
                    request_id,
                    PrivacyOutcome.CHANNEL_UNAVAILABLE,
                    PrivacyReason.CHANNEL_UNAVAILABLE,
                )
            return await self._resume_admitted(request_id, case_digest, deadline)

    async def prepare_local_disclosure(self, candidate: CandidateContext) -> LocalDisclosureResult:
        if type(candidate) is not CandidateContext or candidate.local_sink is None:
            raise TypeError("local_disclosure_candidate_invalid")
        async with self._admission_lock:
            if self._closed:
                return LocalDisclosureUnavailable(candidate.request_id, candidate.local_sink)
            return await self._prepare_local_disclosure_admitted(candidate)

    async def _prepare_local_disclosure_admitted(
        self, candidate: CandidateContext
    ) -> LocalDisclosureResult:
        sink = candidate.local_sink
        assert sink is not None
        effective = await self._policies.effective_policy(candidate.scope)
        classified = self._classifier.classify(candidate, effective)
        decision = self._local_decision(classified, effective)
        if decision.outcome is not PrivacyOutcome.COMPLETED:
            return await self._complete_local_block(classified, effective, decision)
        minimized = self._classifier.minimize_and_scan(classified, decision)
        if minimized.forbidden_findings:
            forbidden = PrivacyDecision(
                (),
                minimized.blocked_categories,
                PrivacyOutcome.BLOCKED_FORBIDDEN_DATA,
                PrivacyReason.NEVER_SEND_DETECTED,
            )
            return await self._complete_local_block(classified, effective, forbidden)
        if not minimized.included_item_ids and classified.items:
            if candidate.purpose == "client_result_projection":
                return await self._complete_agent_projection(classified, effective, minimized)
            empty = PrivacyDecision(
                (),
                minimized.blocked_categories,
                PrivacyOutcome.BLOCKED_BY_POLICY,
                PrivacyReason.INSUFFICIENT_APPROVED_CONTEXT,
            )
            return await self._complete_local_block(classified, effective, empty)
        if candidate.purpose == "client_result_projection":
            return await self._complete_agent_projection(classified, effective, minimized)
        task_id = candidate.scope.task_id
        if task_id is None:
            return LocalDisclosureUnavailable(candidate.request_id, sink)
        now = self._clock.now_utc()
        prepared = await self._audit.prepare_disclosure_proposal(
            DisclosureProposalRequest(
                privacy_proposal_id=self._ids.new(IdKind.PRIVACY_PROPOSAL),
                request_id=candidate.request_id,
                task_id=task_id,
                minimized=minimized,
                provider_binding=None,
                local_sink=sink,
                purpose=candidate.purpose,
                scope=candidate.scope,
                policy_id=effective.policy.policy_id,
                policy_version=effective.policy.version,
                policy_generation=effective.generation,
                policy_digest=effective.effective_digest,
                max_bytes=minimized.byte_count,
                max_tokens=minimized.token_count,
                expires_at=now + timedelta(seconds=60),
            )
        )
        proposal = prepared.proposal
        await self._audit.consume_local(
            prepared.reservation.privacy_proposal_id,
            proposal.prepared_case_digest,
            now,
        )
        receipt = self._local_receipt(
            classified,
            effective,
            proposal.privacy_proposal_id,
            minimized.approved_categories,
            minimized.blocked_categories,
            minimized.scanner_registry_version,
            minimized.scanner_profile_digest,
            len(minimized.forbidden_findings),
            PrivacyOutcome.COMPLETED,
            None,
            ConsentSource.BASELINE_POLICY,
            minimized.byte_count,
            minimized.token_count,
            len(minimized.included_item_ids),
        )
        await self._audit.complete_local_disclosure(proposal.privacy_proposal_id, receipt)
        included = set(minimized.included_item_ids)
        approved_items = tuple(
            sorted(
                (
                    ApprovedLocalItem(
                        _pointer(item.candidate.item_id, item.candidate.origin_ref),
                        item.candidate.category,
                        item.candidate.plaintext,
                    )
                    for item in classified.items
                    if item.candidate.item_id in included
                ),
                key=lambda item: item.json_pointer.encode(),
            )
        )
        omissions = _omissions(classified, included)
        return LocalDisclosureApproved(
            proposal.privacy_proposal_id,
            candidate.request_id,
            sink,
            candidate.purpose,
            candidate.scope,
            effective.effective_digest,
            proposal.proposal_commitment,
            approved_items,
            omissions,
            receipt,
        )

    async def close(self) -> None:
        async with self._admission_lock:
            async with self._close_lock:
                if self._close_task is None:
                    self._closed = True
                    self._close_task = asyncio.create_task(self._gateway.close())
                task = self._close_task
        await task

    async def _evaluate_semantic_admitted(
        self, candidate: CandidateContext, deadline: Deadline
    ) -> SemanticEgressResult:
        if candidate.channel is None or candidate.local_sink is not None:
            return SemanticEgressBlocked(
                candidate.request_id,
                PrivacyOutcome.CHANNEL_UNAVAILABLE,
                PrivacyReason.CHANNEL_UNAVAILABLE,
            )
        if candidate.channel is not EgressChannel.LLM_INFERENCE:
            return await self._complete_semantic_predispatch(
                candidate,
                await self._policies.effective_policy(candidate.scope),
                PrivacyOutcome.CHANNEL_UNAVAILABLE,
                PrivacyReason.CHANNEL_UNAVAILABLE,
            )
        if candidate.purpose not in {_SEMANTIC_PURPOSE, _CREDENTIAL_PROBE_PURPOSE}:
            return SemanticEgressBlocked(
                candidate.request_id,
                PrivacyOutcome.BLOCKED_BY_POLICY,
                PrivacyReason.PURPOSE_NOT_ALLOWED,
            )
        if candidate.provider_binding is None or candidate.subject_digest is None:
            return SemanticEgressBlocked(
                candidate.request_id,
                PrivacyOutcome.CHANNEL_UNAVAILABLE,
                PrivacyReason.CHANNEL_UNAVAILABLE,
            )
        if deadline.expired(self._clock.monotonic_seconds()):
            return SemanticEgressBlocked(
                candidate.request_id,
                PrivacyOutcome.TIMEOUT,
                PrivacyReason.DEADLINE_EXPIRED,
            )

        effective = await self._policies.effective_policy(candidate.scope)
        authority_digest: str | None = None
        if candidate.provider_binding.transport == "external":
            activated = await self._activate_repository_admitted(candidate.scope)
            if activated is None:
                return await self._complete_semantic_predispatch(
                    candidate,
                    effective,
                    PrivacyOutcome.BLOCKED_BY_POLICY,
                    PrivacyReason.SCOPE_MISMATCH,
                )
            effective, authority_digest = activated
        return await self._semantic_pipeline(
            candidate, effective, deadline, authority_digest=authority_digest
        )

    async def _activate_repository_admitted(
        self, scope: AuthorizationScope
    ) -> tuple[EffectivePrivacyPolicy, str] | None:
        if scope.kind not in {AuthorizationScopeKind.TASK, AuthorizationScopeKind.REQUEST}:
            return None
        try:
            authority = await self._policies.repository_authority(scope)
        except Exception:
            return None
        return await self._activate_repository_from_authority_admitted(authority, scope)

    async def _repository_authority_admitted(
        self, scope: AuthorizationScope
    ) -> RepositoryPrivacyAuthority | None:
        if scope.kind not in {AuthorizationScopeKind.TASK, AuthorizationScopeKind.REQUEST}:
            return None
        try:
            authority = await self._policies.repository_authority(scope)
        except Exception:
            return None
        if (
            type(authority) is not RepositoryPrivacyAuthority
            or authority.scope != scope
            or authority.repository_privacy_commitment != scope.workspace_ref_commitment
        ):
            return None
        return authority

    async def _activate_repository_from_authority_admitted(
        self, authority: RepositoryPrivacyAuthority, scope: AuthorizationScope
    ) -> tuple[EffectivePrivacyPolicy, str] | None:
        effective = authority.effective
        authority_digest = authority.authority_digest
        repository = authority.repository_privacy_commitment
        if (
            authority.grant_state != "granted"
            or type(effective) is not EffectivePrivacyPolicy
            or repository != scope.workspace_ref_commitment
        ):
            return None
        reconcile = getattr(self._gateway, "reconcile_repository_policy", None)
        human_authority = self._human_authority
        if reconcile is None or type(human_authority) is not HumanAuthorityCapability:
            return None
        try:
            await reconcile(
                effective,
                human_authority,
                repository_privacy_commitment=repository,
                authority_digest=authority_digest,
            )
        except Exception:
            return None
        return effective, authority_digest

    async def _repository_authority_is_current(
        self, scope: AuthorizationScope, expected_authority_digest: str
    ) -> bool:
        try:
            authority = await self._policies.repository_authority(scope)
        except Exception:
            return False
        return (
            authority.grant_state == "granted"
            and authority.repository_privacy_commitment == scope.workspace_ref_commitment
            and authority.authority_digest == expected_authority_digest
        )

    async def _resume_admitted(
        self, request_id: str, case_digest: str, deadline: Deadline
    ) -> SemanticEgressResult:
        state = await self._audit.load(request_id, case_digest)
        if state is None:
            return SemanticEgressBlocked(
                request_id,
                PrivacyOutcome.AUDIT_FAILED,
                PrivacyReason.AUDIT_FAILED,
            )
        status = state.status
        if status in {"reserved", "awaiting_human"}:
            try:
                pending = await self._audit.load_disclosure_proposal(
                    state.reservation.privacy_proposal_id
                )
            except Exception:
                pending = None
            if pending is None or pending.prepared_case_digest != case_digest:
                return SemanticEgressBlocked(
                    request_id,
                    PrivacyOutcome.AUDIT_FAILED,
                    PrivacyReason.AUDIT_FAILED,
                    privacy_proposal_id=state.reservation.privacy_proposal_id,
                )
            if pending.expires_at <= self._clock.now_utc():
                return SemanticEgressBlocked(
                    request_id,
                    PrivacyOutcome.APPROVAL_EXPIRED,
                    PrivacyReason.AUTHORIZATION_EXPIRED,
                    privacy_proposal_id=state.reservation.privacy_proposal_id,
                )
            return SemanticEgressAwaitingHuman(
                request_id,
                state.reservation.privacy_proposal_id,
                state.reservation.subject_digest,
                pending.expires_at,
            )
        if status in {"denied", "decision_receipt_pending", "expired", "decision_completed"}:
            return SemanticEgressBlocked(
                request_id,
                PrivacyOutcome.HUMAN_DENIED
                if status in {"denied", "decision_receipt_pending"}
                else PrivacyOutcome.APPROVAL_EXPIRED
                if status == "expired"
                else PrivacyOutcome.BLOCKED_BY_POLICY,
                PrivacyReason.HUMAN_DENIED
                if status in {"denied", "decision_receipt_pending"}
                else PrivacyReason.AUTHORIZATION_EXPIRED
                if status == "expired"
                else PrivacyReason.POLICY_DENIED,
                privacy_proposal_id=state.reservation.privacy_proposal_id,
                receipt_id=state.receipt_id,
            )
        if status in {"receipt_pending", "attempt_completed"}:
            # Attempt already consumed; automatic retry requires a fresh evaluate_semantic.
            return SemanticEgressBlocked(
                request_id,
                PrivacyOutcome.APPROVAL_EXPIRED,
                PrivacyReason.AUTHORIZATION_REUSED,
                privacy_proposal_id=state.reservation.privacy_proposal_id,
                receipt_id=state.receipt_id,
            )
        if status not in {"reserved", "approved", "authorized"}:
            return SemanticEgressBlocked(
                request_id,
                PrivacyOutcome.AUDIT_FAILED,
                PrivacyReason.AUDIT_FAILED,
                privacy_proposal_id=state.reservation.privacy_proposal_id,
            )
        if deadline.expired(self._clock.monotonic_seconds()):
            return SemanticEgressBlocked(
                request_id,
                PrivacyOutcome.TIMEOUT,
                PrivacyReason.DEADLINE_EXPIRED,
                privacy_proposal_id=state.reservation.privacy_proposal_id,
            )
        try:
            proposal = await self._audit.load_disclosure_proposal(
                state.reservation.privacy_proposal_id
            )
        except Exception:
            proposal = None
        if proposal is None or proposal.prepared_case_digest != case_digest:
            return SemanticEgressBlocked(
                request_id,
                PrivacyOutcome.AUDIT_FAILED,
                PrivacyReason.AUDIT_FAILED,
                privacy_proposal_id=state.reservation.privacy_proposal_id,
            )
        try:
            effective = await self._policies.effective_policy(proposal.scope)
        except Exception:
            return SemanticEgressBlocked(
                request_id,
                PrivacyOutcome.AUDIT_FAILED,
                PrivacyReason.AUDIT_FAILED,
                privacy_proposal_id=proposal.privacy_proposal_id,
            )
        binding = proposal.provider_binding
        if binding is None and proposal.local_sink is LocalDisclosureSink.LOCAL_MODEL:
            # Local AF_UNIX semantic resume needs the standing local-model binding.
            binding = effective.policy.local_model_binding
        if binding is None:
            return SemanticEgressBlocked(
                request_id,
                PrivacyOutcome.AUDIT_FAILED,
                PrivacyReason.AUDIT_FAILED,
                privacy_proposal_id=proposal.privacy_proposal_id,
            )
        channel = (
            EgressChannel.LLM_INFERENCE
            if proposal.provider_binding is not None or binding.transport == "local_af_unix"
            else None
        )
        candidate = CandidateContext(
            request_id=request_id,
            channel=channel,
            local_sink=None if channel is not None else proposal.local_sink,
            purpose=proposal.purpose,
            scope=proposal.scope,
            subject_digest=case_digest,
            provider_binding=binding,
            items=(),
        )
        minimized = MinimizedDisclosure(
            prepared_bytes=proposal.prepared_bytes,
            included_item_ids=proposal.source_item_digests,
            source_item_digests=proposal.source_item_digests,
            approved_categories=proposal.approved_categories,
            blocked_categories=proposal.blocked_categories,
            transformation_summary=proposal.transformation_summary,
            byte_count=len(proposal.prepared_bytes),
            token_count=proposal.max_tokens,
            case_digest=proposal.prepared_case_digest,
            scanner_registry_version="resume",
            scanner_profile_digest=proposal.policy_digest,
            forbidden_findings=(),
        )
        authority_digest: str | None = None
        if binding.transport == "external":
            activated = await self._activate_repository_admitted(candidate.scope)
            if activated is None:
                return SemanticEgressBlocked(
                    request_id,
                    PrivacyOutcome.BLOCKED_BY_POLICY,
                    PrivacyReason.SCOPE_MISMATCH,
                    privacy_proposal_id=proposal.privacy_proposal_id,
                )
            effective, authority_digest = activated
        if status == "authorized":
            auth_id = state.authorization_id
            if type(auth_id) is not str:
                return SemanticEgressBlocked(
                    request_id,
                    PrivacyOutcome.AUDIT_FAILED,
                    PrivacyReason.AUDIT_FAILED,
                    privacy_proposal_id=proposal.privacy_proposal_id,
                )
            try:
                authorization = await self._audit.load_authorization(auth_id)
            except Exception:
                authorization = None
            if authorization is None:
                return SemanticEgressBlocked(
                    request_id,
                    PrivacyOutcome.AUDIT_FAILED,
                    PrivacyReason.AUDIT_FAILED,
                    privacy_proposal_id=proposal.privacy_proposal_id,
                )
            return await self._dispatch_approved(
                candidate,
                effective,
                proposal,
                minimized,
                ConsentSource.PER_REQUEST_LOCAL_HUMAN,
                deadline,
                subject_digest=state.reservation.subject_digest,
                authorization=authorization,
                authority_digest=authority_digest,
            )
        return await self._dispatch_approved(
            candidate,
            effective,
            proposal,
            minimized,
            ConsentSource.PER_REQUEST_LOCAL_HUMAN,
            deadline,
            subject_digest=state.reservation.subject_digest,
            authority_digest=authority_digest,
        )

    async def _semantic_pipeline(
        self,
        candidate: CandidateContext,
        effective: EffectivePrivacyPolicy,
        deadline: Deadline,
        *,
        authority_digest: str | None = None,
    ) -> SemanticEgressResult:
        policy = effective.policy
        binding = candidate.provider_binding
        assert binding is not None

        if candidate.channel is not EgressChannel.LLM_INFERENCE:
            return await self._complete_semantic_predispatch(
                candidate,
                effective,
                PrivacyOutcome.CHANNEL_UNAVAILABLE,
                PrivacyReason.CHANNEL_UNAVAILABLE,
            )

        llm = next(
            item for item in policy.channel_policies if item.channel is EgressChannel.LLM_INFERENCE
        )
        if policy.profile is PrivacyProfile.LOCAL_ONLY and binding.transport == "external":
            return await self._complete_semantic_predispatch(
                candidate,
                effective,
                PrivacyOutcome.BLOCKED_BY_POLICY,
                PrivacyReason.DESTINATION_NOT_ALLOWED,
            )
        if not policy.network_egress_permitted or not llm.enabled:
            if binding.transport == "external":
                return await self._complete_semantic_predispatch(
                    candidate,
                    effective,
                    PrivacyOutcome.CHANNEL_UNAVAILABLE,
                    PrivacyReason.CHANNEL_UNAVAILABLE,
                )
        # Exact membership in the row's authorized destinations: the primary, plus the one
        # fallback the same approval named (#582). Never a prefix, wildcard, or provider-id match.
        if (
            llm.provider_binding is not None
            and binding.transport == "external"
            and binding not in llm.authorized_provider_bindings
        ):
            return await self._complete_semantic_predispatch(
                candidate,
                effective,
                PrivacyOutcome.BLOCKED_BY_POLICY,
                PrivacyReason.DESTINATION_NOT_ALLOWED,
            )
        if candidate.purpose not in llm.allowed_purposes:
            return await self._complete_semantic_predispatch(
                candidate,
                effective,
                PrivacyOutcome.BLOCKED_BY_POLICY,
                PrivacyReason.PURPOSE_NOT_ALLOWED,
            )
        # Block only a candidate whose scope is *broader* than the ceiling the channel commits to.
        # A narrower candidate (task under a workspace ceiling) is inside the consented authority,
        # which is the shipped assisted_review / expanded_review shape.
        if _SCOPE_KIND_RANK[candidate.scope.kind] < _SCOPE_KIND_RANK[llm.scope_ceiling]:
            return await self._complete_semantic_predispatch(
                candidate,
                effective,
                PrivacyOutcome.BLOCKED_BY_POLICY,
                PrivacyReason.SCOPE_MISMATCH,
            )
        if (
            policy.require_current_provider_data_use_evidence
            and binding.transport == "external"
            and not self._data_use_evidence_current(binding)
        ):
            return await self._complete_semantic_predispatch(
                candidate,
                effective,
                PrivacyOutcome.BLOCKED_BY_POLICY,
                PrivacyReason.POLICY_DENIED,
            )

        classified = self._classifier.classify(candidate, effective)
        decision = self._semantic_decision(classified, effective, binding)
        if decision.outcome is not PrivacyOutcome.COMPLETED:
            return await self._complete_semantic_predispatch(
                candidate,
                effective,
                decision.outcome,
                decision.reason or PrivacyReason.POLICY_DENIED,
            )

        minimized = self._classifier.minimize_and_scan(classified, decision)
        # Re-run policy intersection after preparation.
        if binding.transport == "external":
            if authority_digest is None or not await self._repository_authority_is_current(
                candidate.scope, authority_digest
            ):
                return await self._complete_semantic_predispatch(
                    candidate,
                    effective,
                    PrivacyOutcome.BLOCKED_BY_POLICY,
                    PrivacyReason.SCOPE_MISMATCH,
                )
            refreshed = await self._activate_repository_admitted(candidate.scope)
            if refreshed is None or refreshed[1] != authority_digest:
                return await self._complete_semantic_predispatch(
                    candidate,
                    effective,
                    PrivacyOutcome.BLOCKED_BY_POLICY,
                    PrivacyReason.SCOPE_MISMATCH,
                )
            effective = refreshed[0]
        else:
            effective = await self._policies.effective_policy(candidate.scope)
        if minimized.forbidden_findings:
            return await self._complete_semantic_predispatch(
                candidate,
                effective,
                PrivacyOutcome.BLOCKED_FORBIDDEN_DATA,
                PrivacyReason.NEVER_SEND_DETECTED,
            )
        if not minimized.included_item_ids:
            return await self._complete_semantic_predispatch(
                candidate,
                effective,
                PrivacyOutcome.BLOCKED_BY_POLICY,
                PrivacyReason.INSUFFICIENT_APPROVED_CONTEXT,
            )
        # Channel ceilings are operator-visible policy dimensions; fail closed when exceeded.
        llm = next(
            item
            for item in effective.policy.channel_policies
            if item.channel is EgressChannel.LLM_INFERENCE
        )
        if llm.max_bytes > 0 and minimized.byte_count > llm.max_bytes:
            return await self._complete_semantic_predispatch(
                candidate,
                effective,
                PrivacyOutcome.BLOCKED_BY_POLICY,
                PrivacyReason.POLICY_DENIED,
            )
        if llm.max_tokens > 0 and minimized.token_count > llm.max_tokens:
            return await self._complete_semantic_predispatch(
                candidate,
                effective,
                PrivacyOutcome.BLOCKED_BY_POLICY,
                PrivacyReason.POLICY_DENIED,
            )

        task_id = candidate.scope.task_id
        if task_id is None:
            return await self._complete_semantic_predispatch(
                candidate,
                effective,
                PrivacyOutcome.BLOCKED_BY_POLICY,
                PrivacyReason.SCOPE_MISMATCH,
            )

        now = self._clock.now_utc()
        local_sink = (
            LocalDisclosureSink.LOCAL_MODEL if binding.transport == "local_af_unix" else None
        )
        provider_binding = binding if binding.transport == "external" else None
        # Authorization ceilings bind policy ∩ case (never case size alone).
        auth_max_bytes = (
            minimized.byte_count if llm.max_bytes <= 0 else min(llm.max_bytes, minimized.byte_count)
        )
        auth_max_tokens = (
            1
            if candidate.purpose == _CREDENTIAL_PROBE_PURPOSE
            else (
                minimized.token_count
                if llm.max_tokens <= 0
                else min(llm.max_tokens, minimized.token_count)
            )
        )
        try:
            prepared = await self._audit.prepare_disclosure_proposal(
                DisclosureProposalRequest(
                    privacy_proposal_id=self._ids.new(IdKind.PRIVACY_PROPOSAL),
                    request_id=candidate.request_id,
                    task_id=task_id,
                    minimized=minimized,
                    provider_binding=provider_binding,
                    local_sink=local_sink,
                    purpose=candidate.purpose,
                    scope=candidate.scope,
                    policy_id=effective.policy.policy_id,
                    policy_version=effective.policy.version,
                    policy_generation=effective.generation,
                    policy_digest=effective.effective_digest,
                    max_bytes=auth_max_bytes,
                    max_tokens=auth_max_tokens,
                    expires_at=now
                    + timedelta(seconds=max(60, llm.authorization_ttl_seconds or 60)),
                )
            )
        except Exception as exc:
            record_unexpected_exception_without_raising(
                exc,
                component="privacy_egress",
                operation="audit_prepare_failed",
                request_id=candidate.request_id,
            )
            return SemanticEgressBlocked(
                candidate.request_id,
                PrivacyOutcome.AUDIT_FAILED,
                PrivacyReason.AUDIT_FAILED,
            )

        proposal = prepared.proposal
        preview_required = (
            policy.profile is PrivacyProfile.CONFIRM_EVERY_REQUEST or llm.preview_required
        )
        subject_digest = prepared.reservation.subject_digest
        if preview_required:
            return await self._handle_human_gate(
                candidate,
                effective,
                proposal,
                minimized,
                subject_digest,
                deadline,
                authority_digest=authority_digest,
            )
        return await self._dispatch_approved(
            candidate,
            effective,
            proposal,
            minimized,
            ConsentSource.BASELINE_POLICY,
            deadline,
            subject_digest=subject_digest,
            authority_digest=authority_digest,
        )

    async def _handle_human_gate(
        self,
        candidate: CandidateContext,
        effective: EffectivePrivacyPolicy,
        proposal: DisclosureProposal,
        minimized: MinimizedDisclosure,
        subject_digest: str,
        deadline: Deadline,
        *,
        authority_digest: str | None,
    ) -> SemanticEgressResult:
        if self._human is None:
            try:
                await self._audit.mark_awaiting_human(proposal.privacy_proposal_id)
            except Exception:
                pass
            return SemanticEgressAwaitingHuman(
                candidate.request_id,
                proposal.privacy_proposal_id,
                subject_digest,
                proposal.expires_at,
            )
        try:
            decision = await self._human.request_disclosure_decision(proposal)
        except Exception:
            return SemanticEgressBlocked(
                candidate.request_id,
                PrivacyOutcome.AUDIT_FAILED,
                PrivacyReason.AUDIT_FAILED,
                privacy_proposal_id=proposal.privacy_proposal_id,
            )
        if type(decision) is PendingHumanDecision:
            try:
                await self._audit.mark_awaiting_human(proposal.privacy_proposal_id)
            except Exception:
                pass
            return SemanticEgressAwaitingHuman(
                decision.request_id,
                decision.privacy_proposal_id,
                subject_digest,
                decision.expires_at,
            )
        if type(decision) is not HumanPrivacyDecision or not decision.approved:
            try:
                if type(decision) is HumanPrivacyDecision:
                    await self._audit.record_human_decision(proposal.privacy_proposal_id, decision)
            except Exception:
                pass
            return await self._complete_semantic_predispatch(
                candidate,
                effective,
                PrivacyOutcome.HUMAN_DENIED,
                PrivacyReason.HUMAN_DENIED,
                proposal_id=proposal.privacy_proposal_id,
            )
        try:
            await self._audit.record_human_decision(proposal.privacy_proposal_id, decision)
        except Exception:
            return SemanticEgressBlocked(
                candidate.request_id,
                PrivacyOutcome.AUDIT_FAILED,
                PrivacyReason.AUDIT_FAILED,
                privacy_proposal_id=proposal.privacy_proposal_id,
            )
        return await self._dispatch_approved(
            candidate,
            effective,
            proposal,
            minimized,
            decision.consent_source,
            deadline,
            subject_digest=subject_digest,
            authority_digest=authority_digest,
        )

    async def _dispatch_approved(
        self,
        candidate: CandidateContext,
        effective: EffectivePrivacyPolicy,
        proposal: DisclosureProposal,
        minimized: MinimizedDisclosure,
        consent: ConsentSource,
        deadline: Deadline,
        *,
        subject_digest: str,
        authorization: object | None = None,
        authority_digest: str | None = None,
    ) -> SemanticEgressResult:
        binding = candidate.provider_binding
        assert binding is not None
        now = self._clock.now_utc()
        if proposal.expires_at <= now or deadline.expired(self._clock.monotonic_seconds()):
            return SemanticEgressBlocked(
                candidate.request_id,
                PrivacyOutcome.APPROVAL_EXPIRED,
                PrivacyReason.AUTHORIZATION_EXPIRED,
                privacy_proposal_id=proposal.privacy_proposal_id,
            )

        if binding.transport == "local_af_unix":
            local_case = ApprovedLocalDisclosureCase(
                self._ids.new(IdKind.OUTBOUND_CASE),
                candidate.request_id,
                proposal.privacy_proposal_id,
                proposal.prepared_bytes,
                _MEDIA_TYPE,
                minimized.included_item_ids or proposal.source_item_digests,
                proposal.approved_categories,
                proposal.blocked_categories,
                len(proposal.prepared_bytes),
                proposal.max_tokens,
                LocalDisclosureSink.LOCAL_MODEL,
                binding,
                candidate.purpose,
                effective.effective_digest,
                proposal.prepared_case_digest,
            )
            try:
                result = await self._gateway.dispatch_local_semantic(local_case, deadline)
            except Exception:
                return SemanticEgressBlocked(
                    candidate.request_id,
                    PrivacyOutcome.TRANSPORT_FAILED,
                    PrivacyReason.OUTCOME_UNKNOWN,
                    privacy_proposal_id=proposal.privacy_proposal_id,
                )
            outcome, reason = _semantic_result_outcome(result)
            receipt = _semantic_local_receipt(
                candidate,
                effective,
                proposal,
                minimized,
                consent,
                outcome,
                reason,
                self._clock.now_utc(),
                self._ids,
            )
            try:
                await self._audit.complete_local_disclosure(proposal.privacy_proposal_id, receipt)
            except Exception:
                pass
            return await self._map_provider_result(
                candidate.request_id,
                proposal.privacy_proposal_id,
                None,
                SemanticDispatchKind.LOCAL_MODEL,
                proposal.prepared_case_digest,
                subject_digest,
                result,
            )

        if authority_digest is None or not await self._repository_authority_is_current(
            candidate.scope, authority_digest
        ):
            return SemanticEgressBlocked(
                candidate.request_id,
                PrivacyOutcome.BLOCKED_BY_POLICY,
                PrivacyReason.SCOPE_MISMATCH,
                privacy_proposal_id=proposal.privacy_proposal_id,
            )

        minted: EgressAuthorization
        if type(authorization) is EgressAuthorization:
            minted = authorization
        else:
            try:
                minted = await self._audit.authorize(
                    proposal.privacy_proposal_id, proposal.prepared_case_digest, now
                )
            except Exception as exc:
                record_unexpected_exception_without_raising(
                    exc,
                    component="privacy_egress",
                    operation="audit_authorize_failed",
                    request_id=candidate.request_id,
                )
                return SemanticEgressBlocked(
                    candidate.request_id,
                    PrivacyOutcome.AUDIT_FAILED,
                    PrivacyReason.AUDIT_FAILED,
                    privacy_proposal_id=proposal.privacy_proposal_id,
                )
        authorization = minted
        case = ApprovedOutboundCase(
            self._ids.new(IdKind.OUTBOUND_CASE),
            candidate.request_id,
            proposal.prepared_bytes,
            _MEDIA_TYPE,
            _SCHEMA_ID,
            minimized.included_item_ids,
            proposal.approved_categories,
            proposal.blocked_categories,
            len(proposal.prepared_bytes),
            proposal.max_tokens,
            binding,
            candidate.purpose,
            authorization.authorization_id,
            effective.effective_digest,
            proposal.prepared_case_digest,
        )
        try:
            result = await self._gateway.dispatch_external_semantic(case, authorization, deadline)
        except Exception:
            return SemanticEgressBlocked(
                candidate.request_id,
                PrivacyOutcome.TRANSPORT_FAILED,
                PrivacyReason.OUTCOME_UNKNOWN,
                privacy_proposal_id=proposal.privacy_proposal_id,
            )
        dispatch_kind = (
            SemanticDispatchKind.EXTERNAL_RUNTIME_OAUTH
            if result.provenance.runtime_evidence is not None
            else SemanticDispatchKind.EXTERNAL
        )
        return await self._map_provider_result(
            candidate.request_id,
            proposal.privacy_proposal_id,
            authorization.authorization_id,
            dispatch_kind,
            proposal.prepared_case_digest,
            subject_digest,
            result,
        )

    async def _map_provider_result(
        self,
        request_id: str,
        privacy_proposal_id: str,
        authorization_id: str | None,
        dispatch_kind: SemanticDispatchKind,
        case_digest: str,
        subject_digest: str,
        result: SemanticResult,
    ) -> SemanticEgressResult:
        receipt_id: str | None = None
        try:
            state = await self._audit.load(request_id, subject_digest)
            if state is not None:
                receipt_id = state.receipt_id
        except Exception:
            receipt_id = None
        request_commitment = getattr(result.provenance, "request_commitment", None)
        if type(result) is SemanticResultSuccess:
            return SemanticEgressSuccess(
                request_id,
                privacy_proposal_id,
                authorization_id,
                dispatch_kind,
                result,
                case_digest,
                privacy_receipt_id=receipt_id,
                request_commitment=request_commitment,
            )
        if type(result) in {
            SemanticResultRefused,
            SemanticResultTimeout,
            SemanticResultInvalid,
            SemanticResultLate,
            SemanticResultUnavailable,
        }:
            return SemanticEgressProviderOutcome(
                request_id,
                privacy_proposal_id,
                authorization_id,
                dispatch_kind,
                result,  # type: ignore[arg-type]
                case_digest,
                privacy_receipt_id=receipt_id,
                request_commitment=request_commitment,
            )
        return SemanticEgressBlocked(
            request_id,
            PrivacyOutcome.TRANSPORT_FAILED,
            PrivacyReason.OUTCOME_UNKNOWN,
            privacy_proposal_id=privacy_proposal_id,
            receipt_id=receipt_id,
        )

    def _data_use_evidence_current(self, binding: ProviderBinding) -> bool:
        """True when the bound external profile has current recommendation-eligible data-use."""

        resolver = self._data_use_resolver
        if resolver is None:
            return False
        profile = resolver(binding)
        if type(profile) is not ProviderDataUseProfile:
            return False
        return profile.recommendation_eligible(self._clock.now_utc())

    def _semantic_decision(
        self,
        classified: ClassifiedContext,
        effective: EffectivePrivacyPolicy,
        binding: ProviderBinding,
    ) -> PrivacyDecision:
        policy = effective.policy
        llm = next(
            item for item in policy.channel_policies if item.channel is EgressChannel.LLM_INFERENCE
        )
        if binding.transport == "local_af_unix":
            if not policy.local_model_enabled:
                return PrivacyDecision(
                    (), (), PrivacyOutcome.CHANNEL_UNAVAILABLE, PrivacyReason.CHANNEL_UNAVAILABLE
                )
            ceiling = _LocalCeiling(
                frozenset(policy.local_model_categories),
                frozenset(policy.local_model_data_classes),
            )
        else:
            ceiling = _LocalCeiling(
                frozenset(llm.allowed_categories), frozenset(llm.allowed_data_classes)
            )
        if any(item.forbidden_findings for item in classified.items):
            return PrivacyDecision(
                (),
                tuple({item.candidate.category for item in classified.items}),
                PrivacyOutcome.BLOCKED_FORBIDDEN_DATA,
                PrivacyReason.NEVER_SEND_DETECTED,
            )
        if any(not item.scope_valid for item in classified.items):
            return PrivacyDecision(
                (),
                tuple(
                    {item.candidate.category for item in classified.items if not item.scope_valid}
                ),
                PrivacyOutcome.CLASSIFICATION_UNCERTAIN,
                PrivacyReason.CLASSIFICATION_UNCERTAIN,
            )
        approved = tuple(
            item.candidate.item_id
            for item in classified.items
            if item.candidate.category in ceiling.categories
            and item.data_class in ceiling.data_classes
            and item.data_class is not DataClass.SECRET_OR_CRYPTOGRAPHIC
            and item.scope_valid
            and not item.forbidden_findings
        )
        blocked = tuple(
            {
                item.candidate.category
                for item in classified.items
                if item.candidate.item_id not in approved
            }
        )
        if not approved and classified.items:
            return PrivacyDecision(
                (),
                blocked,
                PrivacyOutcome.BLOCKED_BY_POLICY,
                PrivacyReason.CATEGORY_NOT_ALLOWED,
            )
        return PrivacyDecision(approved, blocked, PrivacyOutcome.COMPLETED, None)

    async def _complete_semantic_predispatch(
        self,
        candidate: CandidateContext,
        effective: EffectivePrivacyPolicy,
        outcome: PrivacyOutcome,
        reason: PrivacyReason,
        *,
        proposal_id: str | None = None,
    ) -> SemanticEgressBlocked:
        now = self._clock.now_utc()
        pid = proposal_id or self._ids.new(IdKind.PRIVACY_PROPOSAL)
        binding = candidate.provider_binding
        destination: ProviderBinding | None = (
            binding if binding is not None and binding.transport == "external" else None
        )
        subject_digest = canonical_digest(
            {
                "outcome": outcome.value,
                "policy_digest": effective.effective_digest,
                "request_id": candidate.request_id,
                "channel": EgressChannel.LLM_INFERENCE.value,
            }
        )
        # Categories are a closed set of DataCategory values: unique and sorted for the
        # PreDispatchAuditDecision contract. Multiple case items may share one category.
        categories = tuple(
            sorted(
                {item.category for item in candidate.items},
                key=lambda value: str(value.value).encode("ascii"),
            )
        )
        subject = PreDispatchAuditDecision(
            pid,
            candidate.request_id,
            EgressChannel.LLM_INFERENCE,
            None,
            candidate.purpose,
            candidate.scope,
            effective.policy.policy_id,
            effective.policy.version,
            effective.effective_digest,
            None if destination is None else canonical_digest({"binding": destination.provider_id}),
            categories,
            len(candidate.items),
            len(candidate.items),
            (),
            now,
            subject_digest,
            outcome,
            reason,
        )
        try:
            reservation = await self._audit.reserve(subject)
        except Exception:
            return SemanticEgressBlocked(
                candidate.request_id, PrivacyOutcome.AUDIT_FAILED, PrivacyReason.AUDIT_FAILED
            )
        if destination is None:
            # Structural channel/policy block without an exact external destination still receipts.
            destination = ProviderBinding(
                "unavailable",
                "unavailable",
                "unavailable",
                "1.0.0",
                "external",
            )
        receipt = EgressReceipt(
            "1.0.0",
            self._ids.new(IdKind.EGRESS_RECEIPT),
            candidate.request_id,
            pid,
            EgressChannel.LLM_INFERENCE,
            outcome,
            now,
            candidate.scope,
            candidate.purpose,
            destination,
            ReceiptPolicyBinding(
                effective.policy.policy_id,
                effective.policy.version,
                effective.effective_digest,
                _scope_digest(candidate.scope),
            ),
            ConsentSource.NONE,
            (),
            tuple({item.category for item in candidate.items}),
            ReceiptCounts(
                len(candidate.items),
                0,
                len(candidate.items),
                0,
                len(candidate.items),
                sum(len(item.plaintext) for item in candidate.items),
                0,
                None,
                None,
            ),
            ReceiptTransformations(0, 0, len(candidate.items)),
            ReceiptSecretScan("observability-sensitive-content-v1", f"sha256:{'0' * 64}", 0, True),
            reason,
            1,
        )
        try:
            await self._audit.complete_decision(reservation.privacy_proposal_id, receipt)
        except Exception:
            return SemanticEgressBlocked(
                candidate.request_id,
                PrivacyOutcome.AUDIT_FAILED,
                PrivacyReason.AUDIT_FAILED,
                privacy_proposal_id=pid,
            )
        return SemanticEgressBlocked(
            candidate.request_id,
            outcome,
            reason,
            privacy_proposal_id=pid,
            receipt_id=receipt.receipt_id,
        )

    def _local_decision(
        self, classified: ClassifiedContext, effective: EffectivePrivacyPolicy
    ) -> PrivacyDecision:
        sink = classified.candidate.local_sink
        assert sink is not None
        policy = effective.policy
        if not classified.items:
            return PrivacyDecision((), (), PrivacyOutcome.COMPLETED, None)
        is_projection = classified.candidate.purpose == "client_result_projection"
        if sink is LocalDisclosureSink.LOCAL_HUMAN_VIEW:
            ceiling = _LocalCeiling(
                frozenset(DataCategory),
                frozenset(
                    {
                        DataClass.PUBLIC_STRUCTURAL,
                        DataClass.ORDINARY_USER_CONTENT,
                        DataClass.SENSITIVE_CONFIDENTIAL,
                    }
                ),
            )
        elif sink is LocalDisclosureSink.AGENT_CONTEXT:
            ceiling = _LocalCeiling(
                frozenset(policy.agent_context_categories),
                frozenset(policy.agent_context_data_classes),
            )
        elif sink is LocalDisclosureSink.LOCAL_MODEL:
            if not policy.local_model_enabled:
                return PrivacyDecision(
                    (), (), PrivacyOutcome.CHANNEL_UNAVAILABLE, PrivacyReason.CHANNEL_UNAVAILABLE
                )
            ceiling = _LocalCeiling(
                frozenset(policy.local_model_categories),
                frozenset(policy.local_model_data_classes),
            )
        else:
            ceiling = _LocalCeiling(
                frozenset(policy.trusted_human_control_categories),
                frozenset(policy.trusted_human_control_data_classes),
            )
        if not is_projection and any(item.forbidden_findings for item in classified.items):
            return PrivacyDecision(
                (),
                tuple({item.candidate.category for item in classified.items}),
                PrivacyOutcome.BLOCKED_FORBIDDEN_DATA,
                PrivacyReason.NEVER_SEND_DETECTED,
            )
        if not is_projection and any(not item.scope_valid for item in classified.items):
            return PrivacyDecision(
                (),
                tuple(
                    {item.candidate.category for item in classified.items if not item.scope_valid}
                ),
                PrivacyOutcome.CLASSIFICATION_UNCERTAIN,
                PrivacyReason.CLASSIFICATION_UNCERTAIN,
            )
        approved = tuple(
            item.candidate.item_id
            for item in classified.items
            if (
                item.candidate.category in ceiling.categories
                or (
                    sink is LocalDisclosureSink.AGENT_CONTEXT
                    and item.provenance
                    in {
                        DisclosureProvenance.SELF_AUTHORED,
                        DisclosureProvenance.ENGINE_DERIVED_FROM_SELF_AUTHORED,
                    }
                )
            )
            and item.data_class in ceiling.data_classes
            and not (
                sink is LocalDisclosureSink.AGENT_CONTEXT
                and item.data_class is DataClass.SENSITIVE_CONFIDENTIAL
            )
            and item.data_class is not DataClass.SECRET_OR_CRYPTOGRAPHIC
            and item.scope_valid
            and not item.forbidden_findings
        )
        blocked = tuple(
            {
                item.candidate.category
                for item in classified.items
                if item.candidate.item_id not in approved
            }
        )
        if not approved and not is_projection:
            return PrivacyDecision(
                (),
                blocked,
                PrivacyOutcome.BLOCKED_BY_POLICY,
                PrivacyReason.CATEGORY_NOT_ALLOWED,
            )
        return PrivacyDecision(approved, blocked, PrivacyOutcome.COMPLETED, None)

    async def _complete_agent_projection(
        self,
        classified: ClassifiedContext,
        effective: EffectivePrivacyPolicy,
        minimized: MinimizedDisclosure,
    ) -> LocalDisclosureResult:
        candidate = classified.candidate
        sink = candidate.local_sink
        context = candidate.projection_audit_context
        assert sink is not None and context is not None
        proposal_id = self._ids.new(IdKind.PRIVACY_PROPOSAL)
        included = set(minimized.included_item_ids)
        approved_items = tuple(
            sorted(
                (
                    ApprovedLocalItem(
                        _pointer(item.candidate.item_id, item.candidate.origin_ref),
                        item.candidate.category,
                        item.candidate.plaintext,
                    )
                    for item in classified.items
                    if item.candidate.item_id in included
                ),
                key=lambda item: item.json_pointer.encode(),
            )
        )
        omissions = _omissions(classified, included)
        projected = canonical_encode(
            {
                "approved": [
                    {
                        "category": item.category.value,
                        "content_base64": base64.b64encode(item.bounded_bytes).decode("ascii"),
                        "pointer": item.json_pointer,
                    }
                    for item in approved_items
                ],
                "omissions": [
                    {
                        "category": item.category.value,
                        "pointer": item.json_pointer,
                        "reason": item.reason,
                    }
                    for item in omissions
                ],
                "schema": "yoetz.client-result-projection/1",
            }
        )
        receipt = self._local_receipt(
            classified,
            effective,
            proposal_id,
            minimized.approved_categories,
            minimized.blocked_categories,
            minimized.scanner_registry_version,
            minimized.scanner_profile_digest,
            sum(len(item.forbidden_findings) for item in classified.items)
            + len(minimized.forbidden_findings),
            PrivacyOutcome.COMPLETED,
            None,
            ConsentSource.BASELINE_POLICY,
            len(projected),
            minimized.token_count,
            len(included),
        )
        field_decisions = tuple(
            sorted(
                (
                    (
                        _pointer(item.candidate.item_id, item.candidate.origin_ref),
                        item.candidate.category,
                        item.candidate.item_id in included,
                        (
                            None
                            if item.candidate.item_id in included
                            else "never_send_redacted"
                            if item.forbidden_findings
                            else "local_disclosure_not_authorized"
                        ),
                    )
                    for item in classified.items
                ),
                key=lambda value: value[0].encode(),
            )
        )
        try:
            completed = await self._audit.complete_agent_projection(
                AgentProjectionRequest(
                    proposal_id,
                    candidate.request_id,
                    context.rpc_id,
                    context.method,
                    context.service_instance_id,
                    context.service_generation,
                    context.original_request_id,
                    context.control_request_canonical,
                    candidate.scope,
                    candidate.scope.task_id,
                    context.route_identity_digest,
                    effective.policy.policy_id,
                    effective.policy.version,
                    effective.generation,
                    effective.effective_digest,
                    sink,
                    tuple(
                        sorted(
                            {
                                item.provenance
                                for item in classified.items
                                if item.provenance is not None
                            },
                            key=lambda value: value.value.encode(),
                        )
                    ),
                    context.internal_result_canonical,
                    projected,
                    field_decisions,
                    len(classified.items),
                    len(included),
                    len(classified.items) - len(included),
                    self._clock.now_utc(),
                ),
                receipt,
            )
        except Exception:
            return LocalDisclosureUnavailable(candidate.request_id, sink)
        return LocalDisclosureApproved(
            proposal_id,
            candidate.request_id,
            sink,
            candidate.purpose,
            candidate.scope,
            effective.effective_digest,
            completed.subject.projection_commitment,
            approved_items,
            omissions,
            receipt,
        )

    async def _complete_local_block(
        self,
        classified: ClassifiedContext,
        effective: EffectivePrivacyPolicy,
        decision: PrivacyDecision,
    ) -> LocalDisclosureResult:
        candidate = classified.candidate
        sink = candidate.local_sink
        assert sink is not None
        now = self._clock.now_utc()
        proposal_id = self._ids.new(IdKind.PRIVACY_PROPOSAL)
        forbidden_counts = Counter(
            finding for item in classified.items for finding in item.forbidden_findings
        )
        subject_digest = canonical_digest(
            {
                "outcome": decision.outcome.value,
                "policy_digest": effective.effective_digest,
                "request_id": candidate.request_id,
                "sink": sink.value,
            }
        )
        subject = PreDispatchAuditDecision(
            proposal_id,
            candidate.request_id,
            None,
            sink,
            candidate.purpose,
            candidate.scope,
            effective.policy.policy_id,
            effective.policy.version,
            effective.effective_digest,
            None,
            tuple({item.candidate.category for item in classified.items}),
            len(classified.items),
            len(classified.items) - len(decision.approved_item_ids),
            tuple(sorted(forbidden_counts.items(), key=lambda item: item[0].value)),
            now,
            subject_digest,
            decision.outcome,
            decision.reason or PrivacyReason.POLICY_DENIED,
        )
        try:
            reservation = await self._audit.reserve(subject)
        except Exception:
            return LocalDisclosureUnavailable(candidate.request_id, sink)
        receipt = self._local_receipt(
            classified,
            effective,
            proposal_id,
            (),
            decision.blocked_categories,
            "observability-sensitive-content-v1",
            f"sha256:{'0' * 64}",
            sum(forbidden_counts.values()),
            decision.outcome,
            decision.reason,
            ConsentSource.NONE,
            0,
            0,
            0,
        )
        await self._audit.complete_decision(reservation.privacy_proposal_id, receipt)
        omitted = _omissions(classified, set())
        return LocalDisclosureBlocked(
            proposal_id,
            candidate.request_id,
            sink,
            candidate.purpose,
            candidate.scope,
            effective.effective_digest,
            subject_digest,
            omitted,
            receipt,
        )

    def _local_receipt(
        self,
        classified: ClassifiedContext,
        effective: EffectivePrivacyPolicy,
        proposal_id: str,
        approved_categories: tuple[DataCategory, ...],
        blocked_categories: tuple[DataCategory, ...],
        scanner_version: str,
        scanner_digest: str,
        match_count: int,
        outcome: PrivacyOutcome,
        reason: PrivacyReason | None,
        consent: ConsentSource,
        final_bytes: int,
        tokens: int,
        included_count: int,
    ) -> LocalDisclosureReceipt:
        candidate = classified.candidate
        sink = candidate.local_sink
        assert sink is not None
        candidate_bytes = sum(len(item.candidate.plaintext) for item in classified.items)
        return LocalDisclosureReceipt(
            "1.0.0",
            self._ids.new(IdKind.EGRESS_RECEIPT),
            candidate.request_id,
            proposal_id,
            sink,
            outcome,
            self._clock.now_utc(),
            candidate.scope,
            candidate.purpose,
            ReceiptPolicyBinding(
                effective.policy.policy_id,
                effective.policy.version,
                effective.effective_digest,
                _scope_digest(candidate.scope),
            ),
            consent,
            approved_categories,
            blocked_categories,
            ReceiptCounts(
                len(classified.items),
                included_count,
                len(classified.items) - included_count,
                included_count,
                len(classified.items) - included_count,
                candidate_bytes,
                final_bytes,
                tokens,
                None,
            ),
            ReceiptTransformations(
                len(classified.items) - included_count,
                0,
                len(classified.items) - included_count,
            ),
            ReceiptSecretScan(scanner_version, scanner_digest, match_count, match_count == 0),
            reason,
            1,
        )


def _scope_digest(scope: AuthorizationScope) -> str:
    return canonical_digest(
        {
            "installation_id": scope.installation_id,
            "kind": scope.kind.value,
            "request_id": scope.request_id,
            "task_id": scope.task_id,
            "workspace_ref_commitment": scope.workspace_ref_commitment,
        }
    )


def _pointer(item_id: str, origin_ref: str) -> str:
    if origin_ref.startswith("/") and len(origin_ref) <= 256:
        return origin_ref
    escaped = item_id.replace("~", "~0").replace("/", "~1")
    return f"/{escaped}"


def _omissions(
    classified: ClassifiedContext, included: set[str]
) -> tuple[LocalDisclosureOmission, ...]:
    return tuple(
        sorted(
            (
                LocalDisclosureOmission(
                    _pointer(item.candidate.item_id, item.candidate.origin_ref),
                    item.candidate.category,
                    (
                        "never_send_redacted"
                        if item.forbidden_findings
                        else "local_disclosure_not_authorized"
                    ),
                )
                for item in classified.items
                if item.candidate.item_id not in included
            ),
            key=lambda item: item.json_pointer.encode(),
        )
    )
