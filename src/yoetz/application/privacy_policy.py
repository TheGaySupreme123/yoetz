"""Provider-free privacy setup, transition, and receipt-inspection use cases."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Literal, cast

from yoetz.domain.privacy import (
    AuthorizationScope,
    ChannelPolicy,
    EgressChannel,
    PolicyOverlay,
    PrivacyPolicy,
    PrivacyProfile,
    ProviderDataUseProfile,
    ReviewContextProfile,
    ReviewSelectionPolicy,
)
from yoetz.domain.values import validate_sha256_digest
from yoetz.ports.clock import ClockPort
from yoetz.ports.ids import IdPort
from yoetz.ports.privacy import (
    EffectivePrivacyPolicy,
    HumanAuthorityCapability,
    HumanPolicyDecision,
    OutboundGatewayPort,
    PolicyCommitResult,
    PolicyTransitionProposal,
    PreparedPolicyTransition,
    PrivacyAuditPort,
    PrivacyPolicyStorePort,
    PrivacyReceiptAudience,
    PrivacyReceiptPage,
    PrivacyReceiptQuery,
    PrivacyReceiptView,
    ProviderReconciliation,
)
from yoetz.protocol.canonical import JsonValue, canonical_digest
from yoetz.protocol.ids import IdKind, validate_id
from yoetz.protocol.models import DataCategory

__all__ = [
    "AllowedBlockedExample",
    "ChannelSetupChoice",
    "DecidePrivacyPolicyRequest",
    "GetPrivacyEffectiveRequest",
    "GetPrivacyReceiptRequest",
    "GetPrivacySetupRequest",
    "ListPrivacyReceiptsRequest",
    "PolicyDecisionRequired",
    "PolicyProposalResult",
    "PrivacyPolicyApplication",
    "PrivacyPolicyResult",
    "PrivacySetupView",
    "ProposePrivacyPolicyRequest",
    "ProviderDataUseSummary",
    "ReviewRecipeView",
    "TightenPrivacyPolicyRequest",
    "decide_privacy_policy",
    "is_privacy_tightening",
    "privacy_widening_summary",
    "privacy_get_effective",
    "privacy_get_setup",
    "privacy_propose_policy",
    "privacy_receipts_get",
    "privacy_receipts_list",
    "privacy_tighten_policy",
]

_SETUP_MESSAGES = frozenset({"begin", "answer", "review", "cancel"})
type PrivacyRecipe = Literal[
    "private", "metadata_only", "assisted_review", "expanded_review", "custom"
]


@dataclass(frozen=True, slots=True)
class GetPrivacySetupRequest:
    session_id: str
    message_type: Literal["begin", "answer", "review", "cancel"]
    sequence: int
    expires_at: datetime
    first_run: bool = False
    current_policy_digest: str | None = None
    current_policy_version: int | None = None
    recipe_hint: PrivacyRecipe | None = None

    def __post_init__(self) -> None:
        if type(self.session_id) is not str or not self.session_id:
            raise ValueError("privacy_setup_session_invalid")
        if self.message_type not in _SETUP_MESSAGES:
            raise ValueError("privacy_setup_message_invalid")
        if type(self.sequence) is not int or self.sequence < 0:
            raise ValueError("privacy_setup_sequence_invalid")
        if type(self.expires_at) is not datetime or self.expires_at.tzinfo is None:
            raise ValueError("privacy_setup_expiry_invalid")
        if type(self.first_run) is not bool:
            raise ValueError("privacy_setup_first_run_invalid")
        if self.first_run == (self.current_policy_digest is not None):
            raise ValueError("privacy_setup_policy_binding_invalid")
        if (self.current_policy_digest is None) != (self.current_policy_version is None):
            raise ValueError("privacy_setup_policy_binding_invalid")
        if self.current_policy_digest is not None:
            validate_sha256_digest(self.current_policy_digest)
        if self.current_policy_version is not None and (
            type(self.current_policy_version) is not int or self.current_policy_version <= 0
        ):
            raise ValueError("privacy_setup_policy_version_invalid")


@dataclass(frozen=True, slots=True)
class GetPrivacyEffectiveRequest:
    scope: AuthorizationScope

    def __post_init__(self) -> None:
        if type(self.scope) is not AuthorizationScope:
            raise TypeError("privacy_scope_invalid")


@dataclass(frozen=True, slots=True)
class ProposePrivacyPolicyRequest:
    expected_policy_digest: str
    candidate_policy: PrivacyPolicy

    def __post_init__(self) -> None:
        validate_sha256_digest(self.expected_policy_digest)
        if type(self.candidate_policy) is not PrivacyPolicy:
            raise TypeError("candidate_policy_invalid")


@dataclass(frozen=True, slots=True)
class TightenPrivacyPolicyRequest(ProposePrivacyPolicyRequest):
    pass


@dataclass(frozen=True, slots=True)
class DecidePrivacyPolicyRequest:
    prepared: PreparedPolicyTransition
    decision: HumanPolicyDecision
    human_authority: HumanAuthorityCapability

    def __post_init__(self) -> None:
        if (
            type(self.prepared) is not PreparedPolicyTransition
            or type(self.decision) is not HumanPolicyDecision
            or type(self.human_authority) is not HumanAuthorityCapability
        ):
            raise TypeError("privacy_policy_decision_invalid")


@dataclass(frozen=True, slots=True)
class ListPrivacyReceiptsRequest:
    query: PrivacyReceiptQuery = PrivacyReceiptQuery()

    def __post_init__(self) -> None:
        if type(self.query) is not PrivacyReceiptQuery:
            raise TypeError("privacy_receipt_query_invalid")


@dataclass(frozen=True, slots=True)
class GetPrivacyReceiptRequest:
    receipt_id: str

    def __post_init__(self) -> None:
        validate_id(IdKind.EGRESS_RECEIPT, self.receipt_id)


@dataclass(frozen=True, slots=True)
class ChannelSetupChoice:
    channel: EgressChannel
    enabled: bool
    capability_state: Literal["available", "unsupported"]


@dataclass(frozen=True, slots=True)
class AllowedBlockedExample:
    code: str
    allowed: bool


@dataclass(frozen=True, slots=True)
class ProviderDataUseSummary:
    profile: ProviderDataUseProfile
    recommendation_eligible: bool


@dataclass(frozen=True, slots=True)
class ReviewRecipeView:
    recipe: PrivacyRecipe
    privacy_profile: PrivacyProfile
    review_context_profile: ReviewContextProfile
    review_selection: ReviewSelectionPolicy


@dataclass(frozen=True, slots=True)
class PrivacySetupView:
    effective: EffectivePrivacyPolicy
    channel_choices: tuple[ChannelSetupChoice, ...]
    allowed_blocked_examples: tuple[AllowedBlockedExample, ...]
    recipes: tuple[ReviewRecipeView, ...]
    never_send_editable: Literal[False] = False


@dataclass(frozen=True, slots=True)
class PolicyDecisionRequired:
    prepared: PreparedPolicyTransition
    privacy_proposal_id: str

    def __post_init__(self) -> None:
        if type(self.prepared) is not PreparedPolicyTransition:
            raise TypeError("prepared_policy_transition_invalid")
        validate_id(IdKind.PRIVACY_PROPOSAL, self.privacy_proposal_id)


@dataclass(frozen=True, slots=True)
class PrivacyPolicyResult:
    policy: PrivacyPolicy
    generation: int
    revoked_authorization_count: int
    closed_session_count: int
    provider_reconciliation: ProviderReconciliation


type PolicyProposalResult = PolicyDecisionRequired | PrivacyPolicyResult


@dataclass(frozen=True, slots=True)
class PrivacyPolicyApplication:
    policy_store: PrivacyPolicyStorePort
    audit: PrivacyAuditPort
    gateway: OutboundGatewayPort
    clock: ClockPort
    ids: IdPort
    setup_scope: AuthorizationScope
    proposal_ttl_seconds: int = 300

    def __post_init__(self) -> None:
        if type(self.setup_scope) is not AuthorizationScope:
            raise TypeError("privacy_setup_scope_invalid")
        if type(self.proposal_ttl_seconds) is not int or not 1 <= self.proposal_ttl_seconds <= 3600:
            raise ValueError("privacy_proposal_ttl_invalid")


async def privacy_get_effective(
    app: PrivacyPolicyApplication, request: GetPrivacyEffectiveRequest
) -> EffectivePrivacyPolicy:
    return await app.policy_store.effective_policy(request.scope)


async def privacy_get_setup(
    app: PrivacyPolicyApplication, request: GetPrivacySetupRequest
) -> PrivacySetupView:
    effective = await app.policy_store.effective_policy(app.setup_scope)
    choices = tuple(
        ChannelSetupChoice(
            channel,
            next(p.enabled for p in effective.policy.channel_policies if p.channel is channel),
            "available" if channel is EgressChannel.LLM_INFERENCE else "unsupported",
        )
        for channel in sorted(EgressChannel, key=lambda value: value.value)
    )
    examples = tuple(
        AllowedBlockedExample(code, allowed)
        for code, allowed in (
            ("bounded_structural_metadata", True),
            ("declared_file_type", True),
            ("selected_evidence_excerpt", True),
            ("selected_task_description", True),
            ("complete_transcript", False),
            ("credentials", False),
            ("encryption_material", False),
            ("environment_variables", False),
            ("out_of_scope_content", False),
            ("unrelated_files", False),
        )
    )
    recipes = tuple(
        ReviewRecipeView(
            cast(PrivacyRecipe, name),
            profile,
            context,
            ReviewSelectionPolicy.for_profile(context),
        )
        for name, profile, context in (
            ("private", PrivacyProfile.LOCAL_ONLY, ReviewContextProfile.STRUCTURAL),
            (
                "metadata_only",
                PrivacyProfile.CONFIRM_EVERY_REQUEST,
                ReviewContextProfile.STRUCTURAL,
            ),
            ("assisted_review", PrivacyProfile.MINIMAL_EXTERNAL, ReviewContextProfile.ASSISTED),
            ("expanded_review", PrivacyProfile.TRUSTED_PROVIDER, ReviewContextProfile.EXPANDED),
        )
    )
    return PrivacySetupView(effective, choices, examples, recipes)


async def privacy_propose_policy(
    app: PrivacyPolicyApplication, request: ProposePrivacyPolicyRequest
) -> PolicyProposalResult:
    current = await app.policy_store.effective_policy(request.candidate_policy.effective_scope)
    if current.effective_digest != request.expected_policy_digest:
        raise ValueError("privacy_policy_stale")
    if _is_tightening(current.policy, request.candidate_policy):
        return await privacy_tighten_policy(
            app,
            TightenPrivacyPolicyRequest(request.expected_policy_digest, request.candidate_policy),
        )
    now = app.clock.now_utc()
    proposal_id = app.ids.new(IdKind.PRIVACY_PROPOSAL)
    proposal = PolicyTransitionProposal(
        scope=request.candidate_policy.effective_scope,
        expected_generation=current.generation,
        proposed_policy=request.candidate_policy,
        proposal_digest=canonical_digest(_policy_identity(request.candidate_policy)),
        created_at=now,
        expires_at=now + timedelta(seconds=app.proposal_ttl_seconds),
        privacy_proposal_id=proposal_id,
        expected_policy_digest=request.expected_policy_digest,
    )
    prepared = await app.policy_store.prepare_transition(proposal)
    return PolicyDecisionRequired(prepared, proposal_id)


async def privacy_tighten_policy(
    app: PrivacyPolicyApplication, request: TightenPrivacyPolicyRequest
) -> PrivacyPolicyResult:
    current = await app.policy_store.effective_policy(request.candidate_policy.effective_scope)
    if current.effective_digest != request.expected_policy_digest:
        raise ValueError("privacy_policy_stale")
    if not _is_tightening(current.policy, request.candidate_policy):
        raise ValueError("privacy_authority_required")
    commit = await app.policy_store.tighten(
        request.candidate_policy.effective_scope,
        _overlay(request.candidate_policy),
        request.expected_policy_digest,
    )
    await app.gateway.close_revoked(current.generation)
    unavailable = ProviderReconciliation(commit.generation, 0, 0, ())
    return _result(commit, unavailable)


async def decide_privacy_policy(
    app: PrivacyPolicyApplication, request: DecidePrivacyPolicyRequest
) -> PrivacyPolicyResult:
    commit = await app.policy_store.commit_transition(request.prepared, request.decision)
    effective = EffectivePrivacyPolicy(
        commit.policy, commit.generation, commit.policy.policy_digest
    )
    reconciliation = await app.gateway.reconcile_policy(effective, request.human_authority)
    return _result(commit, reconciliation)


async def privacy_receipts_list(
    app: PrivacyPolicyApplication, request: ListPrivacyReceiptsRequest
) -> PrivacyReceiptPage:
    return await app.audit.list_receipts(
        request.query, PrivacyReceiptAudience.TRUSTED_LOCAL_CONTROL
    )


async def privacy_receipts_get(
    app: PrivacyPolicyApplication, request: GetPrivacyReceiptRequest
) -> PrivacyReceiptView | None:
    return await app.audit.get_receipt(
        request.receipt_id, PrivacyReceiptAudience.TRUSTED_LOCAL_CONTROL
    )


def _result(
    commit: PolicyCommitResult, reconciliation: ProviderReconciliation
) -> PrivacyPolicyResult:
    return PrivacyPolicyResult(
        commit.policy,
        commit.generation,
        commit.revoked_authorization_count,
        commit.closed_session_count,
        reconciliation,
    )


def _overlay(policy: PrivacyPolicy) -> PolicyOverlay:
    return PolicyOverlay(
        policy.effective_scope,
        policy.review_selection,
        policy.require_current_provider_data_use_evidence,
        policy.channel_policies,
        policy.local_model_categories,
        policy.local_model_data_classes,
        policy.agent_context_categories,
        policy.agent_context_data_classes,
        policy,
    )


def _channel_subset(candidate: ChannelPolicy, current: ChannelPolicy) -> bool:
    if candidate.channel is not current.channel:
        return False
    if candidate.enabled and not current.enabled:
        return False
    if not candidate.enabled:
        return True
    return (
        set(candidate.allowed_categories) <= set(current.allowed_categories)
        and set(candidate.allowed_data_classes) <= set(current.allowed_data_classes)
        and set(candidate.allowed_purposes) <= set(current.allowed_purposes)
        and candidate.provider_binding == current.provider_binding
        and _scope_rank(candidate.scope_ceiling.value) >= _scope_rank(current.scope_ceiling.value)
        and (candidate.preview_required or not current.preview_required)
        and candidate.max_bytes <= current.max_bytes
        and candidate.max_tokens <= current.max_tokens
        and candidate.authorization_ttl_seconds <= current.authorization_ttl_seconds
    )


def _scope_rank(value: str) -> int:
    return {"machine": 0, "workspace": 1, "task": 2, "request": 3}[value]


def is_privacy_tightening(current: PrivacyPolicy, candidate: PrivacyPolicy) -> bool:
    """True when ``candidate`` is a non-widening subset/equivalent of ``current``."""

    return _is_tightening(current, candidate)


def privacy_widening_summary(
    current: PrivacyPolicy, candidate: PrivacyPolicy
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Every data category and authorization scope ``candidate`` newly permits.

    This is what a human is shown before approving a widen, so it has to cover the same
    surface :func:`_is_tightening` classifies on — egress channels *and* the local-sink
    ceilings — or a real widening can be approved against an empty summary. Returns sorted
    tuples, both empty when nothing widened.
    """

    categories: set[str] = set()
    scopes: set[str] = set()
    for new, old in zip(candidate.channel_policies, current.channel_policies, strict=True):
        if not new.enabled or _channel_subset(new, old):
            continue
        permitted: set[DataCategory] = set(old.allowed_categories) if old.enabled else set()
        categories.update(item.value for item in new.allowed_categories if item not in permitted)
        # A disabled channel grants no scope, so any enablement is itself a scope widening.
        if not old.enabled or _scope_rank(new.scope_ceiling.value) < _scope_rank(
            old.scope_ceiling.value
        ):
            scopes.add(new.scope_ceiling.value)
    for new_sink, old_sink in (
        (candidate.local_model_categories, current.local_model_categories),
        (candidate.agent_context_categories, current.agent_context_categories),
        (candidate.trusted_human_control_categories, current.trusted_human_control_categories),
    ):
        permitted_sink = set(old_sink)
        categories.update(item.value for item in new_sink if item not in permitted_sink)
    if candidate.effective_scope != current.effective_scope:
        scopes.add(candidate.effective_scope.kind.value)
    return tuple(sorted(categories)), tuple(sorted(scopes))


def _is_tightening(current: PrivacyPolicy, candidate: PrivacyPolicy) -> bool:
    return (
        current.effective_scope == candidate.effective_scope
        and (candidate.network_egress_permitted <= current.network_egress_permitted)
        and all(
            _channel_subset(new, old)
            for new, old in zip(candidate.channel_policies, current.channel_policies, strict=True)
        )
        and candidate.review_selection.meet(current.review_selection) == candidate.review_selection
        and (
            candidate.require_current_provider_data_use_evidence
            or not current.require_current_provider_data_use_evidence
        )
        and set(candidate.local_model_categories) <= set(current.local_model_categories)
        and set(candidate.local_model_data_classes) <= set(current.local_model_data_classes)
        and (not candidate.local_model_enabled or current.local_model_enabled)
        and (
            not candidate.local_model_enabled
            or candidate.local_model_binding == current.local_model_binding
        )
        and set(candidate.agent_context_categories) <= set(current.agent_context_categories)
        and set(candidate.agent_context_data_classes) <= set(current.agent_context_data_classes)
        and set(candidate.trusted_human_control_categories)
        <= set(current.trusted_human_control_categories)
        and set(candidate.trusted_human_control_data_classes)
        <= set(current.trusted_human_control_data_classes)
    )


def _policy_identity(policy: PrivacyPolicy) -> JsonValue:
    return cast(JsonValue, _to_json(policy))


def _to_json(value: object) -> object:
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is bytes:
        return value.hex()
    if isinstance(value, Enum):
        return value.value
    if type(value) is datetime:
        return value.isoformat()
    if is_dataclass(value):
        return {field.name: _to_json(getattr(value, field.name)) for field in fields(value)}
    if type(value) is tuple:
        return [_to_json(member) for member in cast(tuple[object, ...], value)]
    raise TypeError("privacy_policy_identity_invalid")
