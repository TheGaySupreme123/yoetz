"""Central provider-free privacy and disclosure coordinator."""

from __future__ import annotations

import asyncio
import base64
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta

from yoetz.domain.privacy import (
    ApprovedLocalItem,
    AuthorizationScope,
    CandidateContext,
    ClassifiedContext,
    ConsentSource,
    DataCategory,
    DataClass,
    DisclosureProvenance,
    LocalDisclosureApproved,
    LocalDisclosureBlocked,
    LocalDisclosureOmission,
    LocalDisclosureReceipt,
    LocalDisclosureSink,
    LocalDisclosureUnavailable,
    PreDispatchAuditDecision,
    PrivacyDecision,
    PrivacyOutcome,
    PrivacyReason,
    ReceiptCounts,
    ReceiptPolicyBinding,
    ReceiptSecretScan,
    ReceiptTransformations,
)
from yoetz.ports.clock import ClockPort
from yoetz.ports.ids import IdPort
from yoetz.ports.privacy import (
    AgentProjectionRequest,
    DisclosureProposalRequest,
    EffectivePrivacyPolicy,
    MinimizedDisclosure,
    OutboundGatewayPort,
    PrivacyAuditPort,
    PrivacyClassifierPort,
    PrivacyPolicyStorePort,
)
from yoetz.protocol.canonical import canonical_digest, canonical_encode
from yoetz.protocol.ids import IdKind

__all__ = ["PrivacyCoordinator"]

type LocalDisclosureResult = (
    LocalDisclosureApproved | LocalDisclosureBlocked | LocalDisclosureUnavailable
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
        "_gateway",
        "_ids",
        "_policies",
    )

    def __init__(
        self,
        policies: PrivacyPolicyStorePort,
        classifier: PrivacyClassifierPort,
        audit: PrivacyAuditPort,
        gateway: OutboundGatewayPort,
        clock: ClockPort,
        ids: IdPort,
    ) -> None:
        self._policies = policies
        self._classifier = classifier
        self._audit = audit
        self._admission_lock = asyncio.Lock()
        self._gateway = gateway
        self._clock = clock
        self._ids = ids
        self._closed = False
        self._close_lock = asyncio.Lock()
        self._close_task: asyncio.Task[None] | None = None

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
