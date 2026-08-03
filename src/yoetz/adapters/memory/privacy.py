"""In-memory reference privacy policy and audit adapters."""

from __future__ import annotations

import asyncio
import base64
import hmac
from dataclasses import dataclass, field
from datetime import datetime
from typing import cast

from yoetz.adapters.privacy.catalog import (
    _json,  # pyright: ignore[reportPrivateUsage]
    _mac,  # pyright: ignore[reportPrivateUsage]
    _scope_digest,  # pyright: ignore[reportPrivateUsage]
)
from yoetz.domain.privacy import (
    AgentProjectionAuditSubject,
    AuthorizationScope,
    AuthorizationScopeKind,
    DisclosureProposal,
    EgressAuthorization,
    EgressReceipt,
    HumanPrivacyDecision,
    LocalDisclosureReceipt,
    PreDispatchAuditDecision,
    PrivacyAuditSubject,
    PrivacyPolicy,
)
from yoetz.ports.clock import ClockPort
from yoetz.ports.keys import MacKeyHandle
from yoetz.ports.objects import ObjectKind, ObjectMetadata, ObjectRef, ObjectSource, ObjectStorePort
from yoetz.ports.privacy import (
    AgentProjectionRequest,
    CompletedAgentProjection,
    ConsumedAuthorization,
    ConsumedLocalDisclosure,
    DisclosureProposalRequest,
    EffectivePrivacyPolicy,
    HumanPolicyDecision,
    LocalDisclosureReceiptView,
    PolicyCommitResult,
    PolicyOverlay,
    PolicyTransitionProposal,
    PreparedDisclosureReservation,
    PreparedPolicyTransition,
    PrivacyAuditObjectRoots,
    PrivacyAuditReservation,
    PrivacyAuditState,
    PrivacyReceiptAudience,
    PrivacyReceiptPage,
    PrivacyReceiptQuery,
    PrivacyReceiptView,
)
from yoetz.protocol.canonical import JsonValue, canonical_digest, canonical_encode

__all__ = [
    "MemoryPrivacyAudit",
    "MemoryPrivacyCatalogState",
    "MemoryPrivacyPolicyStore",
]

_LOOKUP_DOMAIN = b"yoetz/privacy-audit/lookup/v1\x00"
_PROPOSAL_DOMAIN = b"yoetz/privacy-audit/proposal/v1\x00"
_CONTROL_DOMAIN = b"yoetz/privacy-audit/control-request/v1\x00"
_INTERNAL_RESULT_DOMAIN = b"yoetz/privacy-audit/internal-result/v1\x00"
_PROJECTION_DOMAIN = b"yoetz/privacy-audit/projection/v1\x00"
_APPROVAL_DOMAIN = b"yoetz/privacy-audit/local-approval/v1\x00"
_CURSOR_DOMAIN = b"yoetz/privacy-audit/receipt-cursor/v1\x00"


@dataclass(frozen=True, slots=True)
class _PolicyRow:
    policy: PrivacyPolicy
    generation: int


@dataclass(frozen=True, slots=True)
class _AuditRow:
    reservation: PrivacyAuditReservation
    subject: PrivacyAuditSubject
    status: str
    receipt: LocalDisclosureReceipt | EgressReceipt | None = None
    object_ref: ObjectRef | None = None
    consumed_digest: str | None = None


@dataclass(slots=True, repr=False)
class MemoryPrivacyCatalogState:
    policies: dict[str, _PolicyRow] = field(default_factory=dict[str, _PolicyRow])
    transitions: dict[str, PreparedPolicyTransition] = field(
        default_factory=dict[str, PreparedPolicyTransition]
    )
    audit: dict[str, _AuditRow] = field(default_factory=dict[str, _AuditRow])
    routes: dict[str, str] = field(default_factory=dict[str, str])
    root_generation: dict[str, int] = field(default_factory=dict[str, int])
    generation: int = 0

    def __repr__(self) -> str:
        return "MemoryPrivacyCatalogState(<redacted>)"


class MemoryPrivacyPolicyStore:
    __slots__ = ("_clock", "_lock", "_state")

    def __init__(self, state: MemoryPrivacyCatalogState, clock: ClockPort) -> None:
        self._state = state
        self._clock = clock
        self._lock = asyncio.Lock()

    async def seed_if_absent(self, policy: PrivacyPolicy) -> PrivacyPolicy:
        digest = _scope_digest(policy.effective_scope)
        async with self._lock:
            existing = self._state.policies.get(digest)
            if existing is not None:
                if existing.policy != policy:
                    raise ValueError("privacy_policy_seed_conflict")
                return existing.policy
            self._state.generation += 1
            self._state.policies[digest] = _PolicyRow(policy, self._state.generation)
        return policy

    async def effective_policy(self, scope: AuthorizationScope) -> EffectivePrivacyPolicy:
        eligible = [
            row
            for row in self._state.policies.values()
            if row.policy.effective_scope.contains(scope)
        ]
        if not eligible:
            raise ValueError("privacy_policy_missing")
        # ADR-009 / protocol: intersection of every containing current row — not rank-max alone.
        rank = {
            AuthorizationScopeKind.MACHINE: 0,
            AuthorizationScopeKind.WORKSPACE: 1,
            AuthorizationScopeKind.TASK: 2,
            AuthorizationScopeKind.REQUEST: 3,
        }
        ordered = sorted(
            eligible,
            key=lambda item: (rank[item.policy.effective_scope.kind], item.generation),
        )
        composed = ordered[0].policy
        for row in ordered[1:]:
            composed = composed.meet(row.policy)
        generation = max(row.generation for row in eligible)
        return EffectivePrivacyPolicy(composed, generation, composed.policy_digest)

    async def prepare_transition(
        self, proposal: PolicyTransitionProposal
    ) -> PreparedPolicyTransition:
        if proposal.privacy_proposal_id is None or proposal.expected_policy_digest is None:
            raise ValueError("privacy_policy_proposal_identity_missing")
        current = await self.effective_policy(proposal.scope)
        if current.generation != proposal.expected_generation or (
            current.effective_digest != proposal.expected_policy_digest
        ):
            raise ValueError("privacy_policy_stale")
        exact_diff = canonical_digest(
            {
                "base_policy_digest": current.effective_digest,
                "candidate_policy_digest": proposal.proposed_policy.policy_digest,
            }
        )
        prepared_digest = canonical_digest(
            {
                "diff_digest": exact_diff,
                "proposal_digest": proposal.proposal_digest,
                "proposal_id": proposal.privacy_proposal_id,
            }
        )
        prepared = PreparedPolicyTransition(proposal, prepared_digest, exact_diff, True)
        async with self._lock:
            if proposal.privacy_proposal_id in self._state.transitions:
                raise ValueError("privacy_policy_transition_conflict")
            self._state.transitions[proposal.privacy_proposal_id] = prepared
        return prepared

    async def load_pending_transition(self, proposal_id: str) -> PreparedPolicyTransition:
        prepared = self._state.transitions.get(proposal_id)
        if prepared is None:
            raise ValueError("privacy_policy_transition_unavailable")
        return prepared

    async def commit_transition(
        self, prepared: PreparedPolicyTransition, decision: HumanPolicyDecision
    ) -> PolicyCommitResult:
        proposal = prepared.proposal
        proposal_id = proposal.privacy_proposal_id
        if proposal_id is None or decision.prepared_digest != prepared.prepared_digest:
            raise ValueError("privacy_policy_decision_mismatch")
        async with self._lock:
            if self._state.transitions.get(proposal_id) != prepared:
                raise ValueError("privacy_policy_transition_unavailable")
            current = self._state.policies.get(_scope_digest(proposal.scope))
            if current is None or current.generation != proposal.expected_generation:
                raise ValueError("privacy_policy_stale")
            if decision.decided_at >= proposal.expires_at:
                raise ValueError("privacy_policy_decision_expired")
            del self._state.transitions[proposal_id]
            if not decision.approved:
                return PolicyCommitResult(current.policy, current.generation, 0, 0)
            self._state.generation += 1
            self._state.policies[_scope_digest(proposal.scope)] = _PolicyRow(
                proposal.proposed_policy, self._state.generation
            )
        return PolicyCommitResult(proposal.proposed_policy, self._state.generation, 0, 0)

    async def tighten(
        self, scope: AuthorizationScope, overlay: PolicyOverlay, expected_policy_digest: str
    ) -> PolicyCommitResult:
        async with self._lock:
            current = self._state.policies.get(_scope_digest(scope))
            if current is None or current.policy.policy_digest != expected_policy_digest:
                raise ValueError("privacy_policy_stale")
            self._state.generation += 1
            self._state.policies[_scope_digest(scope)] = _PolicyRow(
                overlay.candidate_policy, self._state.generation
            )
        return PolicyCommitResult(overlay.candidate_policy, self._state.generation, 0, 0)

    async def watch_generation(self) -> int:
        return self._state.generation


class MemoryPrivacyAudit:
    __slots__ = ("_clock", "_key", "_lock", "_objects", "_state")

    def __init__(
        self,
        state: MemoryPrivacyCatalogState,
        objects: ObjectStorePort,
        audit_key: MacKeyHandle,
        clock: ClockPort,
    ) -> None:
        self._state = state
        self._objects = objects
        self._key = audit_key
        self._clock = clock
        self._lock = asyncio.Lock()

    async def complete_agent_projection(
        self, request: AgentProjectionRequest, receipt: LocalDisclosureReceipt
    ) -> CompletedAgentProjection:
        control = _mac(self._key, _CONTROL_DOMAIN, request.control_request_canonical)
        internal = _mac(self._key, _INTERNAL_RESULT_DOMAIN, request.internal_result_canonical)
        projection = _mac(self._key, _PROJECTION_DOMAIN, request.projection_canonical)
        del control
        subject = AgentProjectionAuditSubject(
            request.privacy_proposal_id,
            request.projection_request_id,
            request.rpc_id,
            request.method,
            request.service_instance_id,
            request.service_generation,
            request.original_request_id,
            request.scope,
            request.task_id,
            request.route_identity_digest,
            request.policy_id,
            request.policy_version,
            request.policy_digest,
            request.sink,
            request.provenance,
            internal,
            projection,
            request.field_decisions,
            request.candidate_count,
            request.approved_count,
            request.omitted_count,
            request.finished_at,
        )
        now = self._clock.now_utc()
        reservation = PrivacyAuditReservation(
            request.privacy_proposal_id,
            request.projection_request_id,
            projection,
            "local_disclosure_completed",
            request.policy_generation,
            now,
        )
        row = _AuditRow(reservation, subject, "local_disclosure_completed", receipt)
        async with self._lock:
            existing = self._state.audit.get(request.privacy_proposal_id)
            if existing is not None and existing != row:
                raise ValueError("privacy_projection_replay_conflict")
            self._state.audit[request.privacy_proposal_id] = row
        return CompletedAgentProjection(subject, reservation)

    async def prepare_disclosure_proposal(
        self, request: DisclosureProposalRequest
    ) -> PreparedDisclosureReservation:
        now = self._clock.now_utc()
        route = self._state.routes.get(request.task_id)
        if route is None:
            raise ValueError("privacy_audit_route_unavailable")
        value: dict[str, JsonValue] = {
            "approved_categories": [item.value for item in request.minimized.approved_categories],
            "blocked_categories": [item.value for item in request.minimized.blocked_categories],
            "expires_at": request.expires_at.isoformat(timespec="milliseconds").replace(
                "+00:00", "Z"
            ),
            "policy_digest": request.policy_digest,
            "prepared_bytes_base64": base64.b64encode(request.minimized.prepared_bytes).decode(
                "ascii"
            ),
            "prepared_case_digest": request.minimized.case_digest,
            "privacy_proposal_id": request.privacy_proposal_id,
            "purpose": request.purpose,
            "request_id": request.request_id,
            "schema": "yoetz.disclosure-proposal/1",
            "scope": _json(request.scope),
            "source_item_digests": list(request.minimized.source_item_digests),
            "transformation_summary": [
                list(item) for item in request.minimized.transformation_summary
            ],
        }
        body = canonical_encode(value)
        commitment = _mac(self._key, _PROPOSAL_DOMAIN, body)
        staged = await self._objects.stage(
            ObjectSource(data=body),
            ObjectMetadata(
                ObjectKind.PRIVACY_AUDIT,
                "application/vnd.yoetz.privacy-disclosure+json",
                request.task_id,
                now,
            ),
        )
        ref = await self._objects.finalize(staged)
        proposal = DisclosureProposal(
            request.privacy_proposal_id,
            request.request_id,
            request.task_id,
            request.minimized.source_item_digests,
            request.minimized.prepared_bytes,
            request.minimized.approved_categories,
            request.minimized.blocked_categories,
            request.minimized.transformation_summary,
            request.minimized.case_digest,
            request.provider_binding,
            request.local_sink,
            request.purpose,
            request.scope,
            request.policy_version,
            request.policy_digest,
            request.max_bytes,
            request.max_tokens,
            request.expires_at,
            commitment,
        )
        lookup = _mac(self._key, _LOOKUP_DOMAIN, canonical_encode(_json(proposal)))
        reservation = PrivacyAuditReservation(
            request.privacy_proposal_id,
            request.request_id,
            lookup,
            "reserved",
            request.policy_generation,
            now,
        )
        async with self._lock:
            if request.privacy_proposal_id in self._state.audit:
                raise ValueError("privacy_audit_reservation_conflict")
            self._state.audit[request.privacy_proposal_id] = _AuditRow(
                reservation, proposal, "reserved", object_ref=ref
            )
            self._state.root_generation[request.task_id] = (
                self._state.root_generation.get(request.task_id, 0) + 1
            )
        return PreparedDisclosureReservation(proposal, reservation)

    async def reserve(self, subject: PrivacyAuditSubject) -> PrivacyAuditReservation:
        if type(subject) is not PreDispatchAuditDecision:
            raise ValueError("privacy_audit_subject_requires_typed_prepare")
        canonical = canonical_encode(_json(subject))
        lookup = _mac(self._key, _LOOKUP_DOMAIN, canonical)
        reservation = PrivacyAuditReservation(
            subject.privacy_proposal_id,
            subject.request_id,
            lookup,
            "decision_receipt_pending",
            1,
            self._clock.now_utc(),
        )
        async with self._lock:
            self._state.audit[subject.privacy_proposal_id] = _AuditRow(
                reservation, subject, "decision_receipt_pending"
            )
        return reservation

    async def load(self, request_id: str, subject_digest: str) -> PrivacyAuditState | None:
        for row in self._state.audit.values():
            if row.reservation.request_id == request_id and (
                row.reservation.subject_digest == subject_digest
            ):
                return PrivacyAuditState(
                    row.reservation,
                    row.status,
                    receipt_id=None if row.receipt is None else row.receipt.receipt_id,
                )
        return None

    async def load_disclosure_proposal(self, proposal_id: str) -> DisclosureProposal | None:
        async with self._lock:
            row = self._state.audit.get(proposal_id)
            if row is None or type(row.subject) is not DisclosureProposal:
                return None
            return row.subject

    async def load_authorization(self, authorization_id: str) -> EgressAuthorization | None:
        del authorization_id
        return None

    async def consume_local(
        self, reservation_id: str, approved_case_digest: str, now: datetime
    ) -> ConsumedLocalDisclosure:
        async with self._lock:
            row = self._state.audit.get(reservation_id)
            if (
                row is None
                or row.status != "reserved"
                or type(row.subject) is not DisclosureProposal
            ):
                raise ValueError("privacy_local_reservation_unavailable")
            if row.subject.prepared_case_digest != approved_case_digest:
                raise ValueError("privacy_local_case_digest_mismatch")
            if row.subject.expires_at <= now:
                raise ValueError("privacy_local_reservation_expired")
            self._state.audit[reservation_id] = _AuditRow(
                row.reservation,
                row.subject,
                "local_disclosure_pending",
                object_ref=row.object_ref,
                consumed_digest=approved_case_digest,
            )
        assert row.subject.local_sink is not None
        return ConsumedLocalDisclosure(
            reservation_id, approved_case_digest, row.subject.local_sink, now
        )

    async def complete_local_disclosure(
        self, reservation_id: str, receipt: LocalDisclosureReceipt
    ) -> None:
        await self._complete(
            reservation_id, receipt, "local_disclosure_pending", "local_disclosure_completed"
        )

    async def complete_decision(
        self, reservation_id: str, receipt: EgressReceipt | LocalDisclosureReceipt
    ) -> None:
        await self._complete(
            reservation_id, receipt, "decision_receipt_pending", "decision_completed"
        )

    async def _complete(
        self,
        reservation_id: str,
        receipt: LocalDisclosureReceipt | EgressReceipt,
        expected: str,
        terminal: str,
    ) -> None:
        async with self._lock:
            row = self._state.audit.get(reservation_id)
            if row is None or row.status != expected:
                raise ValueError("privacy_audit_state_conflict")
            self._state.audit[reservation_id] = _AuditRow(
                row.reservation,
                row.subject,
                terminal,
                receipt,
                row.object_ref,
                row.consumed_digest,
            )

    async def get_receipt(
        self, receipt_id: str, audience: PrivacyReceiptAudience
    ) -> PrivacyReceiptView | None:
        if audience is not PrivacyReceiptAudience.TRUSTED_LOCAL_CONTROL:
            raise ValueError("privacy_receipt_audience_invalid")
        for row in self._state.audit.values():
            if type(row.receipt) is LocalDisclosureReceipt and row.receipt.receipt_id == receipt_id:
                return LocalDisclosureReceiptView("local_disclosure", row.receipt)
        return None

    async def list_receipts(
        self, query: PrivacyReceiptQuery, audience: PrivacyReceiptAudience
    ) -> PrivacyReceiptPage:
        if audience is not PrivacyReceiptAudience.TRUSTED_LOCAL_CONTROL:
            raise ValueError("privacy_receipt_audience_invalid")
        values = [
            LocalDisclosureReceiptView("local_disclosure", row.receipt)
            for row in self._state.audit.values()
            if type(row.receipt) is LocalDisclosureReceipt
            and (query.receipt_id is None or row.receipt.receipt_id == query.receipt_id)
            and (query.outcome is None or row.receipt.outcome is query.outcome)
            and (query.local_sink is None or row.receipt.sink is query.local_sink)
            and (query.policy_version is None or row.receipt.policy.version == query.policy_version)
            and (query.scope_kind is None or row.receipt.scope.kind is query.scope_kind)
            and (
                query.finished_at_from is None or row.receipt.finished_at >= query.finished_at_from
            )
            and (
                query.finished_at_through is None
                or row.receipt.finished_at <= query.finished_at_through
            )
        ]
        values.sort(
            key=lambda view: (view.receipt.finished_at, view.receipt.receipt_id), reverse=True
        )
        query_identity: dict[str, JsonValue] = {
            "limit": query.limit,
            "receipt_id": query.receipt_id,
        }
        start = 0
        if query.cursor is not None:
            payload = self._decode_cursor(query.cursor)
            if payload.get("query_digest") != canonical_digest(query_identity):
                raise ValueError("privacy_receipt_cursor_query_mismatch")
            after = cast(str, payload["after_id"])
            start = next(
                (
                    index + 1
                    for index, view in enumerate(values)
                    if view.receipt.receipt_id == after
                ),
                len(values),
            )
        selected = values[start : start + query.limit]
        next_cursor = None
        if start + query.limit < len(values) and selected:
            next_cursor = self._encode_cursor(
                {
                    "after_id": selected[-1].receipt.receipt_id,
                    "query_digest": canonical_digest(query_identity),
                    "version": 1,
                }
            )
        return PrivacyReceiptPage(
            max(1, self._state.generation + len(self._state.audit)), tuple(selected), next_cursor
        )

    def _encode_cursor(self, payload: dict[str, JsonValue]) -> str:
        body = canonical_encode(payload)
        envelope = canonical_encode(
            {
                "body": base64.urlsafe_b64encode(body).decode("ascii").rstrip("="),
                "mac": _mac(self._key, _CURSOR_DOMAIN, body),
            }
        )
        return base64.urlsafe_b64encode(envelope).decode("ascii").rstrip("=")

    def _decode_cursor(self, cursor: str) -> dict[str, JsonValue]:
        from yoetz.protocol.canonical import strict_json_parse

        try:
            envelope = cast(
                dict[str, JsonValue],
                strict_json_parse(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))),
            )
            body_text = cast(str, envelope["body"])
            body = base64.urlsafe_b64decode(body_text + "=" * (-len(body_text) % 4))
            if not hmac.compare_digest(
                cast(str, envelope["mac"]), _mac(self._key, _CURSOR_DOMAIN, body)
            ):
                raise ValueError
            return cast(dict[str, JsonValue], strict_json_parse(body))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("privacy_receipt_cursor_invalid") from exc

    async def live_object_roots(
        self, task_id: str, route_identity_digest: str
    ) -> PrivacyAuditObjectRoots:
        if self._state.routes.get(task_id) != route_identity_digest:
            raise ValueError("privacy_audit_route_unavailable")
        refs = tuple(
            sorted(
                (
                    row.object_ref
                    for row in self._state.audit.values()
                    if row.object_ref is not None and row.object_ref.metadata.task_id == task_id
                ),
                key=lambda ref: ref.object_id.encode(),
            )
        )
        return PrivacyAuditObjectRoots(
            task_id,
            route_identity_digest,
            self._state.root_generation.get(task_id, 0),
            refs,
            canonical_digest([_json(ref) for ref in refs]),
        )

    async def revoke_policy_generation(self, generation: int, reason: str) -> int:
        del generation, reason
        return 0

    async def mark_awaiting_human(self, reservation_id: str) -> PrivacyAuditState:
        async with self._lock:
            row = self._state.audit.get(reservation_id)
            if row is None or row.status != "reserved":
                raise ValueError("privacy_audit_state_conflict")
            self._state.audit[reservation_id] = _AuditRow(
                row.reservation, row.subject, "awaiting_human", object_ref=row.object_ref
            )
            return PrivacyAuditState(
                PrivacyAuditReservation(
                    row.reservation.privacy_proposal_id,
                    row.reservation.request_id,
                    row.reservation.subject_digest,
                    "awaiting_human",
                    row.reservation.policy_generation,
                    row.reservation.reserved_at,
                ),
                "awaiting_human",
            )

    async def record_human_decision(
        self, reservation_id: str, decision: HumanPrivacyDecision
    ) -> PrivacyAuditState:
        del reservation_id, decision
        raise ValueError("network_privacy_authority_unavailable_until_b8")

    async def authorize(
        self, reservation_id: str, approved_case_digest: str, now: datetime
    ) -> EgressAuthorization:
        del reservation_id, approved_case_digest, now
        raise ValueError("network_privacy_authority_unavailable_until_b8")

    async def consume(
        self, authorization_id: str, dispatch_id: str, now: datetime
    ) -> ConsumedAuthorization:
        del authorization_id, dispatch_id, now
        raise ValueError("network_privacy_dispatch_unavailable_until_b8")

    async def complete_egress(self, dispatch_id: str, receipt: EgressReceipt) -> None:
        del dispatch_id, receipt
        raise ValueError("network_privacy_dispatch_unavailable_until_b8")
