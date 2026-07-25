"""Privacy policy, audit, local-human-control, and outbound-gateway ports."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Literal, Protocol

from yoetz.domain.privacy import (
    AgentProjectionAuditSubject,
    ApprovedLocalDisclosureCase,
    ApprovedOutboundCase,
    AuthorizationScope,
    AuthorizationScopeKind,
    CandidateContext,
    ClassifiedContext,
    DisclosureProposal,
    DisclosureProvenance,
    EgressAuthorization,
    EgressChannel,
    EgressReceipt,
    ForbiddenDataKind,
    HumanPrivacyDecision,
    LocalDisclosureReceipt,
    LocalDisclosureSink,
    PolicyOverlay,
    PrivacyAuditSubject,
    PrivacyDecision,
    PrivacyOutcome,
    PrivacyPolicy,
    ProviderBinding,
)
from yoetz.domain.values import format_rfc3339_millis, validate_commitment, validate_sha256_digest
from yoetz.ports.objects import ObjectKind, ObjectRef
from yoetz.ports.semantic import Deadline, SemanticResult
from yoetz.protocol.canonical import canonical_encode, strict_json_parse
from yoetz.protocol.ids import IdKind, validate_id
from yoetz.protocol.models import DataCategory

__all__ = [
    "ConsumedAuthorization",
    "ConsumedLocalDisclosure",
    "AgentProjectionRequest",
    "CompletedAgentProjection",
    "EffectivePrivacyPolicy",
    "DisclosureProposalRequest",
    "HumanAuthorityCapability",
    "HumanPolicyDecision",
    "HumanPrivacyControlPort",
    "LocalDisclosureReceiptView",
    "NetworkEgressReceiptView",
    "OutboundGatewayPort",
    "PendingHumanDecision",
    "PolicyCommitResult",
    "MinimizedDisclosure",
    "PolicyTransitionProposal",
    "PreparedOutboundCase",
    "PreparedDisclosureReservation",
    "PreparedPolicyTransition",
    "PrivacyAuditObjectRoots",
    "PrivacyAuditPort",
    "PrivacyAuditReservation",
    "PrivacyAuditState",
    "PrivacyClassifierPort",
    "PrivacyPolicyStorePort",
    "PrivacyReceiptAudience",
    "PrivacyReceiptPage",
    "PrivacyReceiptQuery",
    "PrivacyReceiptView",
    "ProviderReconciliation",
]

_AUDIT_STATUSES = frozenset(
    {
        "decision_receipt_pending",
        "decision_completed",
        "reserved",
        "awaiting_human",
        "approved",
        "authorized",
        "receipt_pending",
        "attempt_completed",
        "local_disclosure_pending",
        "local_disclosure_completed",
        "denied",
        "expired",
        "quarantined",
    }
)
_CURSOR = re.compile(
    r"^(?:[A-Za-z0-9_-]{4})*(?:[A-Za-z0-9_-][AQgw]|[A-Za-z0-9_-]{2}[AEIMQUYcgkosw048])?$",
    re.ASCII,
)


def _invalid() -> ValueError:
    return ValueError("invalid_privacy_port_value")


def _positive(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 2**63 - 1:
        raise _invalid()
    return value


def _nonnegative(value: object, *, maximum: int = 2**63 - 1) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise _invalid()
    return value


def _time(value: object) -> datetime:
    if type(value) is not datetime:
        raise _invalid()
    try:
        format_rfc3339_millis(value)
    except ValueError as exc:
        raise _invalid() from exc
    return value


@dataclass(frozen=True, slots=True)
class EffectivePrivacyPolicy:
    policy: PrivacyPolicy
    generation: int
    effective_digest: str

    def __post_init__(self) -> None:
        if type(self.policy) is not PrivacyPolicy:
            raise _invalid()
        _positive(self.generation)
        validate_sha256_digest(self.effective_digest)


@dataclass(frozen=True, slots=True)
class PolicyTransitionProposal:
    scope: AuthorizationScope
    expected_generation: int
    proposed_policy: PrivacyPolicy
    proposal_digest: str
    created_at: datetime
    expires_at: datetime
    privacy_proposal_id: str | None = None
    expected_policy_digest: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.scope) is not AuthorizationScope
            or type(self.proposed_policy) is not PrivacyPolicy
        ):
            raise _invalid()
        _positive(self.expected_generation)
        validate_sha256_digest(self.proposal_digest)
        if _time(self.expires_at) <= _time(self.created_at):
            raise _invalid()
        if self.privacy_proposal_id is not None:
            validate_id(IdKind.PRIVACY_PROPOSAL, self.privacy_proposal_id)
        if self.expected_policy_digest is not None:
            validate_sha256_digest(self.expected_policy_digest)
        # P0-4: unsupported v0.1 non-LLM enablement is rejected before a
        # prepared transition or any pending consent can exist.
        if self.proposed_policy.unsupported_enabled_channels:
            raise ValueError("channel_unavailable")


@dataclass(frozen=True, slots=True)
class PreparedPolicyTransition:
    proposal: PolicyTransitionProposal
    prepared_digest: str
    exact_diff_digest: str
    requires_human_authority: bool

    def __post_init__(self) -> None:
        if type(self.proposal) is not PolicyTransitionProposal:
            raise _invalid()
        validate_sha256_digest(self.prepared_digest)
        validate_sha256_digest(self.exact_diff_digest)
        if type(self.requires_human_authority) is not bool:
            raise _invalid()


@dataclass(frozen=True, slots=True)
class HumanPolicyDecision:
    prepared_digest: str
    approved: bool
    decided_at: datetime
    authority_commitment: str

    def __post_init__(self) -> None:
        validate_sha256_digest(self.prepared_digest)
        if type(self.approved) is not bool:
            raise _invalid()
        _time(self.decided_at)
        validate_commitment(self.authority_commitment)


@dataclass(frozen=True, slots=True)
class PolicyCommitResult:
    policy: PrivacyPolicy
    generation: int
    revoked_authorization_count: int
    closed_session_count: int

    def __post_init__(self) -> None:
        if type(self.policy) is not PrivacyPolicy:
            raise _invalid()
        _positive(self.generation)
        _nonnegative(self.revoked_authorization_count)
        _nonnegative(self.closed_session_count)


@dataclass(frozen=True, slots=True)
class PrivacyAuditReservation:
    privacy_proposal_id: str
    request_id: str
    subject_digest: str
    status: str
    policy_generation: int
    reserved_at: datetime

    def __post_init__(self) -> None:
        validate_id(IdKind.PRIVACY_PROPOSAL, self.privacy_proposal_id)
        validate_id(IdKind.REQUEST, self.request_id)
        if self.subject_digest.startswith("hmac-sha256:"):
            validate_commitment(self.subject_digest)
        else:
            validate_sha256_digest(self.subject_digest)
        if self.status not in _AUDIT_STATUSES:
            raise _invalid()
        _positive(self.policy_generation)
        _time(self.reserved_at)


@dataclass(frozen=True, slots=True)
class PrivacyAuditState:
    reservation: PrivacyAuditReservation
    status: str
    authorization_id: str | None = None
    dispatch_id: str | None = None
    receipt_id: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.reservation) is not PrivacyAuditReservation
            or self.status not in _AUDIT_STATUSES
        ):
            raise _invalid()
        if self.authorization_id is not None:
            validate_id(IdKind.EGRESS_AUTHORIZATION, self.authorization_id)
        if self.dispatch_id is not None:
            validate_id(IdKind.EGRESS_DISPATCH, self.dispatch_id)
        if self.receipt_id is not None:
            validate_id(IdKind.EGRESS_RECEIPT, self.receipt_id)


@dataclass(frozen=True, slots=True)
class PendingHumanDecision:
    privacy_proposal_id: str
    request_id: str
    expires_at: datetime

    def __post_init__(self) -> None:
        validate_id(IdKind.PRIVACY_PROPOSAL, self.privacy_proposal_id)
        validate_id(IdKind.REQUEST, self.request_id)
        _time(self.expires_at)


@dataclass(frozen=True, slots=True)
class PreparedOutboundCase:
    proposal: DisclosureProposal
    approved_case_digest: str

    def __post_init__(self) -> None:
        if type(self.proposal) is not DisclosureProposal:
            raise _invalid()
        validate_sha256_digest(self.approved_case_digest)
        if self.proposal.prepared_case_digest != self.approved_case_digest:
            raise _invalid()


@dataclass(frozen=True, slots=True)
class MinimizedDisclosure:
    """Authority-free deterministic minimization and exact-scan result."""

    prepared_bytes: bytes
    included_item_ids: tuple[str, ...]
    source_item_digests: tuple[str, ...]
    approved_categories: tuple[DataCategory, ...]
    blocked_categories: tuple[DataCategory, ...]
    transformation_summary: tuple[tuple[str, int], ...]
    byte_count: int
    token_count: int
    case_digest: str
    scanner_registry_version: str
    scanner_profile_digest: str
    forbidden_findings: tuple[ForbiddenDataKind, ...]

    def __post_init__(self) -> None:
        if type(self.prepared_bytes) is not bytes or len(self.prepared_bytes) > 262_144:
            raise _invalid()
        for values in (self.included_item_ids, self.source_item_digests):
            if type(values) is not tuple or values != tuple(sorted(set(values), key=str.encode)):
                raise _invalid()
        for digest in self.source_item_digests:
            validate_sha256_digest(digest)
        for values, enum_type in (
            (self.approved_categories, DataCategory),
            (self.blocked_categories, DataCategory),
            (self.forbidden_findings, ForbiddenDataKind),
        ):
            if type(values) is not tuple or any(type(value) is not enum_type for value in values):
                raise _invalid()
            if values != tuple(sorted(set(values), key=lambda value: value.value.encode())):
                raise _invalid()
        if type(self.transformation_summary) is not tuple:
            raise _invalid()
        if self.transformation_summary != tuple(
            sorted(set(self.transformation_summary), key=lambda item: item[0].encode())
        ):
            raise _invalid()
        for name, count in self.transformation_summary:
            if type(name) is not str or not name or type(count) is not int or count < 0:
                raise _invalid()
        if self.byte_count != len(self.prepared_bytes):
            raise _invalid()
        _nonnegative(self.token_count)
        validate_sha256_digest(self.case_digest)
        if type(self.scanner_registry_version) is not str or not self.scanner_registry_version:
            raise _invalid()
        validate_sha256_digest(self.scanner_profile_digest)


@dataclass(frozen=True, slots=True)
class DisclosureProposalRequest:
    privacy_proposal_id: str
    request_id: str
    task_id: str
    minimized: MinimizedDisclosure
    provider_binding: ProviderBinding | None
    local_sink: LocalDisclosureSink | None
    purpose: str
    scope: AuthorizationScope
    policy_id: str
    policy_version: int
    policy_generation: int
    policy_digest: str
    max_bytes: int
    max_tokens: int
    expires_at: datetime

    def __post_init__(self) -> None:
        validate_id(IdKind.PRIVACY_PROPOSAL, self.privacy_proposal_id)
        validate_id(IdKind.REQUEST, self.request_id)
        validate_id(IdKind.TASK, self.task_id)
        if type(self.minimized) is not MinimizedDisclosure:
            raise _invalid()
        if (self.provider_binding is None) == (self.local_sink is None):
            raise _invalid()
        if self.provider_binding is not None and type(self.provider_binding) is not ProviderBinding:
            raise _invalid()
        if type(self.local_sink) not in {LocalDisclosureSink, type(None)}:
            raise _invalid()
        if type(self.purpose) is not str or not self.purpose:
            raise _invalid()
        if type(self.scope) is not AuthorizationScope:
            raise _invalid()
        validate_id(IdKind.PRIVACY_POLICY, self.policy_id)
        _positive(self.policy_version)
        _positive(self.policy_generation)
        validate_sha256_digest(self.policy_digest)
        _nonnegative(self.max_bytes, maximum=262_144)
        _nonnegative(self.max_tokens)
        _time(self.expires_at)


@dataclass(frozen=True, slots=True)
class PreparedDisclosureReservation:
    proposal: DisclosureProposal
    reservation: PrivacyAuditReservation

    def __post_init__(self) -> None:
        if (
            type(self.proposal) is not DisclosureProposal
            or type(self.reservation) is not PrivacyAuditReservation
        ):
            raise _invalid()
        if self.proposal.privacy_proposal_id != self.reservation.privacy_proposal_id:
            raise _invalid()


@dataclass(frozen=True, slots=True, repr=False)
class AgentProjectionRequest:
    privacy_proposal_id: str
    projection_request_id: str
    rpc_id: str
    method: str
    service_instance_id: str
    service_generation: int
    original_request_id: str | None
    control_request_canonical: bytes
    scope: AuthorizationScope
    task_id: str | None
    route_identity_digest: str | None
    policy_id: str
    policy_version: int
    policy_generation: int
    policy_digest: str
    sink: LocalDisclosureSink
    provenance: tuple[DisclosureProvenance, ...]
    internal_result_canonical: bytes
    projection_canonical: bytes
    field_decisions: tuple[tuple[str, DataCategory, bool, str | None], ...]
    candidate_count: int
    approved_count: int
    omitted_count: int
    finished_at: datetime

    def __post_init__(self) -> None:
        validate_id(IdKind.PRIVACY_PROPOSAL, self.privacy_proposal_id)
        validate_id(IdKind.REQUEST, self.projection_request_id)
        validate_id(IdKind.CONTROL_RPC, self.rpc_id)
        validate_id(IdKind.SERVICE_INSTANCE, self.service_instance_id)
        if type(self.method) is not str or not self.method:
            raise _invalid()
        _positive(self.service_generation)
        if self.original_request_id is not None:
            validate_id(IdKind.REQUEST, self.original_request_id)
        for value in (
            self.control_request_canonical,
            self.internal_result_canonical,
            self.projection_canonical,
        ):
            if type(value) is not bytes or not value or len(value) > 262_144:
                raise _invalid()
            if canonical_encode(strict_json_parse(value)) != value:
                raise _invalid()
        if type(self.scope) is not AuthorizationScope:
            raise _invalid()
        needs_task = self.scope.kind in {
            AuthorizationScopeKind.TASK,
            AuthorizationScopeKind.REQUEST,
        }
        if (self.task_id is not None) != needs_task or (
            self.route_identity_digest is not None
        ) != needs_task:
            raise _invalid()
        if self.task_id is not None:
            validate_id(IdKind.TASK, self.task_id)
        if self.route_identity_digest is not None:
            validate_sha256_digest(self.route_identity_digest)
        validate_id(IdKind.PRIVACY_POLICY, self.policy_id)
        _positive(self.policy_version)
        _positive(self.policy_generation)
        validate_sha256_digest(self.policy_digest)
        if self.sink not in {
            LocalDisclosureSink.AGENT_CONTEXT,
            LocalDisclosureSink.LOCAL_HUMAN_VIEW,
        }:
            raise _invalid()
        if type(self.provenance) is not tuple or any(
            type(value) is not DisclosureProvenance for value in self.provenance
        ):
            raise _invalid()
        if self.provenance != tuple(
            sorted(set(self.provenance), key=lambda value: value.value.encode())
        ):
            raise _invalid()
        if type(self.field_decisions) is not tuple or self.field_decisions != tuple(
            sorted(self.field_decisions, key=lambda value: value[0].encode())
        ):
            raise _invalid()
        _nonnegative(self.candidate_count)
        _nonnegative(self.approved_count)
        _nonnegative(self.omitted_count)
        if self.approved_count + self.omitted_count != self.candidate_count:
            raise _invalid()
        _time(self.finished_at)

    def __repr__(self) -> str:
        return "AgentProjectionRequest(<redacted>)"


@dataclass(frozen=True, slots=True)
class CompletedAgentProjection:
    subject: AgentProjectionAuditSubject
    reservation: PrivacyAuditReservation

    def __post_init__(self) -> None:
        if (
            type(self.subject) is not AgentProjectionAuditSubject
            or type(self.reservation) is not PrivacyAuditReservation
            or self.subject.privacy_proposal_id != self.reservation.privacy_proposal_id
        ):
            raise _invalid()


@dataclass(frozen=True, slots=True)
class ConsumedAuthorization:
    authorization: EgressAuthorization
    dispatch_id: str
    consumed_at: datetime

    def __post_init__(self) -> None:
        if type(self.authorization) is not EgressAuthorization:
            raise _invalid()
        validate_id(IdKind.EGRESS_DISPATCH, self.dispatch_id)
        _time(self.consumed_at)


@dataclass(frozen=True, slots=True)
class ConsumedLocalDisclosure:
    privacy_proposal_id: str
    approved_case_digest: str
    sink: LocalDisclosureSink
    consumed_at: datetime

    def __post_init__(self) -> None:
        validate_id(IdKind.PRIVACY_PROPOSAL, self.privacy_proposal_id)
        if self.approved_case_digest.startswith("hmac-sha256:"):
            validate_commitment(self.approved_case_digest)
        else:
            validate_sha256_digest(self.approved_case_digest)
        if type(self.sink) is not LocalDisclosureSink:
            raise _invalid()
        _time(self.consumed_at)


@dataclass(frozen=True, slots=True)
class PrivacyAuditObjectRoots:
    task_id: str
    route_identity_digest: str
    privacy_root_generation: int
    object_refs: tuple[ObjectRef, ...]
    root_set_digest: str

    def __post_init__(self) -> None:
        validate_id(IdKind.TASK, self.task_id)
        validate_sha256_digest(self.route_identity_digest)
        _nonnegative(self.privacy_root_generation)
        if type(self.object_refs) is not tuple:
            raise _invalid()
        if any(
            type(ref) is not ObjectRef or ref.metadata.kind is not ObjectKind.PRIVACY_AUDIT
            for ref in self.object_refs
        ):
            raise _invalid()
        expected = tuple(sorted(self.object_refs, key=lambda ref: ref.object_id.encode()))
        if self.object_refs != expected or len({ref.object_id for ref in expected}) != len(
            expected
        ):
            raise _invalid()
        validate_sha256_digest(self.root_set_digest)


class PrivacyReceiptAudience(str, Enum):  # noqa: UP042 - closed audience enum
    TRUSTED_LOCAL_CONTROL = "trusted_local_control"


@dataclass(frozen=True, slots=True)
class PrivacyReceiptQuery:
    receipt_id: str | None = None
    outcome: PrivacyOutcome | None = None
    channel: EgressChannel | None = None
    local_sink: LocalDisclosureSink | None = None
    provider_id: str | None = None
    endpoint_profile_id: str | None = None
    policy_version: int | None = None
    scope_kind: AuthorizationScopeKind | None = None
    finished_at_from: datetime | None = None
    finished_at_through: datetime | None = None
    limit: int = 100
    cursor: str | None = None

    def __post_init__(self) -> None:
        if self.receipt_id is not None:
            validate_id(IdKind.EGRESS_RECEIPT, self.receipt_id)
        if self.policy_version is not None:
            _positive(self.policy_version)
        if self.finished_at_from is not None:
            _time(self.finished_at_from)
        if self.finished_at_through is not None:
            _time(self.finished_at_through)
        if (
            self.finished_at_from is not None
            and self.finished_at_through is not None
            and self.finished_at_from > self.finished_at_through
        ):
            raise _invalid()
        _positive(self.limit)
        if self.limit > 100:
            raise _invalid()
        if self.cursor is not None and (
            type(self.cursor) is not str
            or not 1 <= len(self.cursor) <= 1024
            or _CURSOR.fullmatch(self.cursor) is None
        ):
            raise _invalid()


@dataclass(frozen=True, slots=True)
class NetworkEgressReceiptView:
    kind: Literal["network_egress"]
    receipt: EgressReceipt

    def __post_init__(self) -> None:
        if self.kind != "network_egress" or type(self.receipt) is not EgressReceipt:
            raise _invalid()


@dataclass(frozen=True, slots=True)
class LocalDisclosureReceiptView:
    kind: Literal["local_disclosure"]
    receipt: LocalDisclosureReceipt

    def __post_init__(self) -> None:
        if self.kind != "local_disclosure" or type(self.receipt) is not LocalDisclosureReceipt:
            raise _invalid()


type PrivacyReceiptView = NetworkEgressReceiptView | LocalDisclosureReceiptView


@dataclass(frozen=True, slots=True)
class PrivacyReceiptPage:
    snapshot_generation: int
    receipts: tuple[PrivacyReceiptView, ...]
    next_cursor: str | None

    def __post_init__(self) -> None:
        _positive(self.snapshot_generation)
        if type(self.receipts) is not tuple or len(self.receipts) > 100:
            raise _invalid()
        if any(
            type(receipt) not in {NetworkEgressReceiptView, LocalDisclosureReceiptView}
            for receipt in self.receipts
        ):
            raise _invalid()
        if len(set(self.receipts)) != len(self.receipts):
            raise _invalid()
        expected = tuple(
            sorted(
                self.receipts,
                key=lambda view: (view.receipt.finished_at, view.receipt.receipt_id),
                reverse=True,
            )
        )
        if self.receipts != expected:
            raise _invalid()
        if self.next_cursor is not None and (
            type(self.next_cursor) is not str
            or not 1 <= len(self.next_cursor) <= 1024
            or _CURSOR.fullmatch(self.next_cursor) is None
        ):
            raise _invalid()


@dataclass(frozen=True, slots=True)
class ProviderReconciliation:
    policy_generation: int
    activated_count: int
    deactivated_count: int
    unavailable_bindings: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        _positive(self.policy_generation)
        _nonnegative(self.activated_count)
        _nonnegative(self.deactivated_count)
        if type(self.unavailable_bindings) is not tuple:
            raise _invalid()
        if self.unavailable_bindings != tuple(
            sorted(set(self.unavailable_bindings), key=lambda value: value[0].encode())
        ):
            raise _invalid()
        for digest, reason in self.unavailable_bindings:
            validate_sha256_digest(digest)
            if type(reason) is not str or not reason:
                raise _invalid()


@dataclass(frozen=True, slots=True)
class HumanAuthorityCapability:
    source: Literal["os_user_presence", "established_passphrase", "unavailable"]
    capability_digest: str
    service_generation: int
    vault_mode: str
    vault_generation: int
    external_activation_allowed: bool

    def __post_init__(self) -> None:
        if self.source not in {"os_user_presence", "established_passphrase", "unavailable"}:
            raise _invalid()
        validate_sha256_digest(self.capability_digest)
        _positive(self.service_generation)
        if type(self.vault_mode) is not str or not self.vault_mode:
            raise _invalid()
        _positive(self.vault_generation)
        if type(self.external_activation_allowed) is not bool:
            raise _invalid()
        if (self.source == "unavailable") == self.external_activation_allowed:
            raise _invalid()


class PrivacyPolicyStorePort(Protocol):
    async def seed_if_absent(self, policy: PrivacyPolicy) -> PrivacyPolicy: ...

    async def effective_policy(self, scope: AuthorizationScope) -> EffectivePrivacyPolicy: ...

    async def prepare_transition(
        self, proposal: PolicyTransitionProposal
    ) -> PreparedPolicyTransition: ...

    async def load_pending_transition(self, proposal_id: str) -> PreparedPolicyTransition: ...

    async def commit_transition(
        self, prepared: PreparedPolicyTransition, decision: HumanPolicyDecision
    ) -> PolicyCommitResult: ...

    async def tighten(
        self,
        scope: AuthorizationScope,
        overlay: PolicyOverlay,
        expected_policy_digest: str,
    ) -> PolicyCommitResult: ...

    async def watch_generation(self) -> int: ...


class PrivacyAuditPort(Protocol):
    async def complete_agent_projection(
        self, request: AgentProjectionRequest, receipt: LocalDisclosureReceipt
    ) -> CompletedAgentProjection: ...

    async def prepare_disclosure_proposal(
        self, request: DisclosureProposalRequest
    ) -> PreparedDisclosureReservation: ...

    async def reserve(self, subject: PrivacyAuditSubject) -> PrivacyAuditReservation: ...

    async def load(self, request_id: str, subject_digest: str) -> PrivacyAuditState | None: ...

    async def load_disclosure_proposal(self, proposal_id: str) -> DisclosureProposal | None: ...

    async def load_authorization(self, authorization_id: str) -> EgressAuthorization | None: ...

    async def mark_awaiting_human(self, reservation_id: str) -> PrivacyAuditState: ...

    async def record_human_decision(
        self, reservation_id: str, decision: HumanPrivacyDecision
    ) -> PrivacyAuditState: ...

    async def authorize(
        self, reservation_id: str, approved_case_digest: str, now: datetime
    ) -> EgressAuthorization: ...

    async def consume(
        self, authorization_id: str, dispatch_id: str, now: datetime
    ) -> ConsumedAuthorization: ...

    async def consume_local(
        self, reservation_id: str, approved_case_digest: str, now: datetime
    ) -> ConsumedLocalDisclosure: ...

    async def complete_decision(
        self, reservation_id: str, receipt: EgressReceipt | LocalDisclosureReceipt
    ) -> None: ...

    async def complete_egress(self, dispatch_id: str, receipt: EgressReceipt) -> None: ...

    async def complete_local_disclosure(
        self, reservation_id: str, receipt: LocalDisclosureReceipt
    ) -> None: ...

    async def get_receipt(
        self, receipt_id: str, audience: PrivacyReceiptAudience
    ) -> PrivacyReceiptView | None: ...

    async def list_receipts(
        self, query: PrivacyReceiptQuery, audience: PrivacyReceiptAudience
    ) -> PrivacyReceiptPage: ...

    async def revoke_policy_generation(self, generation: int, reason: str) -> int: ...

    async def live_object_roots(
        self, task_id: str, route_identity_digest: str
    ) -> PrivacyAuditObjectRoots: ...


class PrivacyClassifierPort(Protocol):
    def classify(
        self, candidate: CandidateContext, policy: EffectivePrivacyPolicy
    ) -> ClassifiedContext: ...

    def minimize_and_scan(
        self, classified: ClassifiedContext, decision: PrivacyDecision
    ) -> MinimizedDisclosure: ...

    def scan_exact_bytes(self, data: bytes) -> tuple[ForbiddenDataKind, ...]: ...


class HumanPrivacyControlPort(Protocol):
    async def request_disclosure_decision(
        self, proposal: DisclosureProposal
    ) -> HumanPrivacyDecision | PendingHumanDecision: ...

    async def request_policy_decision(
        self, proposal: PolicyTransitionProposal
    ) -> HumanPolicyDecision | PendingHumanDecision: ...


class OutboundGatewayPort(Protocol):
    async def reconcile_policy(
        self, policy: EffectivePrivacyPolicy, human_authority: HumanAuthorityCapability
    ) -> ProviderReconciliation: ...

    async def dispatch_external_semantic(
        self,
        case: ApprovedOutboundCase,
        authorization: EgressAuthorization,
        deadline: Deadline,
    ) -> SemanticResult: ...

    async def dispatch_local_semantic(
        self, case: ApprovedLocalDisclosureCase, deadline: Deadline
    ) -> SemanticResult: ...

    async def close_revoked(self, policy_generation: int) -> None: ...

    async def close(self) -> None: ...
