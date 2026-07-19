"""Privacy policy, audit, local-human-control, and outbound-gateway ports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Literal, Protocol

from yoetz.domain.privacy import (
    ApprovedLocalDisclosureCase,
    ApprovedOutboundCase,
    AuthorizationScope,
    AuthorizationScopeKind,
    CandidateContext,
    ClassifiedContext,
    DisclosureProposal,
    EgressAuthorization,
    EgressChannel,
    EgressReceipt,
    ForbiddenDataKind,
    HumanPrivacyDecision,
    LocalDisclosureReceipt,
    LocalDisclosureSink,
    PolicyOverlay,
    PrivacyAuditSubject,
    PrivacyOutcome,
    PrivacyPolicy,
)
from yoetz.domain.values import format_rfc3339_millis, validate_commitment, validate_sha256_digest
from yoetz.ports.objects import ObjectKind, ObjectRef
from yoetz.ports.semantic import Deadline, SemanticResult
from yoetz.protocol.ids import IdKind, validate_id

__all__ = [
    "ConsumedAuthorization",
    "ConsumedLocalDisclosure",
    "EffectivePrivacyPolicy",
    "HumanAuthorityCapability",
    "HumanPolicyDecision",
    "HumanPrivacyControlPort",
    "OutboundGatewayPort",
    "PendingHumanDecision",
    "PolicyTransitionProposal",
    "PreparedOutboundCase",
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
        if self.cursor is not None and (type(self.cursor) is not str or not self.cursor):
            raise _invalid()


type PrivacyReceiptView = EgressReceipt | LocalDisclosureReceipt


@dataclass(frozen=True, slots=True)
class PrivacyReceiptPage:
    receipts: tuple[PrivacyReceiptView, ...]
    next_cursor: str | None

    def __post_init__(self) -> None:
        if type(self.receipts) is not tuple or len(self.receipts) > 100:
            raise _invalid()
        if any(
            type(receipt) not in {EgressReceipt, LocalDisclosureReceipt}
            for receipt in self.receipts
        ):
            raise _invalid()
        if self.next_cursor is not None and (
            type(self.next_cursor) is not str or not self.next_cursor
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
    async def effective_policy(self, scope: AuthorizationScope) -> EffectivePrivacyPolicy: ...

    async def prepare_transition(
        self, proposal: PolicyTransitionProposal
    ) -> PreparedPolicyTransition: ...

    async def commit_transition(
        self, prepared: PreparedPolicyTransition, decision: HumanPolicyDecision
    ) -> PrivacyPolicy: ...

    async def tighten(self, scope: AuthorizationScope, overlay: PolicyOverlay) -> PrivacyPolicy: ...

    async def watch_generation(self) -> int: ...


class PrivacyAuditPort(Protocol):
    async def reserve(self, subject: PrivacyAuditSubject) -> PrivacyAuditReservation: ...

    async def load(self, request_id: str, subject_digest: str) -> PrivacyAuditState | None: ...

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
        self, classified: ClassifiedContext, decision: object
    ) -> PreparedOutboundCase: ...

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
