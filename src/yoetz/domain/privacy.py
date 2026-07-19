"""Closed privacy-policy, disclosure-authority, and audit value types."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Final, Literal, cast

from yoetz.domain.values import (
    format_rfc3339_millis,
    validate_commitment,
    validate_sha256_digest,
)
from yoetz.protocol.ids import IdKind, validate_id
from yoetz.protocol.models import DataCategory

__all__ = [
    "MAX_EGRESS_CASE_BYTES",
    "MAX_EGRESS_ITEM_BYTES",
    "NEVER_SEND_KINDS",
    "AgentProjectionAuditSubject",
    "ApprovedLocalDisclosureCase",
    "ApprovedLocalItem",
    "ApprovedOutboundCase",
    "ApprovedProviderCase",
    "AuthorizationScope",
    "AuthorizationScopeKind",
    "CandidateContext",
    "CandidateContextItem",
    "ChannelPolicy",
    "ClassifiedContext",
    "ClassifiedContextItem",
    "ConsentSource",
    "DataCategory",
    "DataClass",
    "DisclosureProposal",
    "DisclosureProvenance",
    "EgressAuthorization",
    "EgressChannel",
    "EgressReceipt",
    "ForbiddenDataKind",
    "HumanPrivacyDecision",
    "LocalDisclosureApproved",
    "LocalDisclosureBlocked",
    "LocalDisclosureOmission",
    "LocalDisclosureReceipt",
    "LocalDisclosureSink",
    "LocalDisclosureUnavailable",
    "NonLlmDestination",
    "PolicyOverlay",
    "PreDispatchAuditDecision",
    "PrivacyAuditSubject",
    "PrivacyDecision",
    "PrivacyOutcome",
    "PrivacyPolicy",
    "PrivacyProfile",
    "PrivacyReason",
    "ProviderBinding",
    "ProviderDataUseProfile",
    "ReceiptCounts",
    "ReceiptPolicyBinding",
    "ReceiptSecretScan",
    "ReceiptTransformations",
    "RequestCommitment",
    "ReviewContextProfile",
    "ReviewSelectionPolicy",
    "outcome_reason_is_valid",
]

MAX_EGRESS_ITEM_BYTES: Final = 16 * 1024
MAX_EGRESS_CASE_BYTES: Final = 256 * 1024
AUDIT_STORE_VERSION: Final = 1
PRIVACY_REQUEST_COMMITMENT_ALGORITHM: Final = "hmac-sha256/yoetz-privacy-egress-request-v1"

_IDENTITY = re.compile(r"^[a-z0-9][a-z0-9._-]*$", re.ASCII)
_MODEL_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]*$", re.ASCII)
_VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]*$", re.ASCII)
_PURPOSE = re.compile(r"^[a-z][a-z0-9-]{0,127}$", re.ASCII)
_OPAQUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
_POINTER = re.compile(r"^(?:/(?:[^~/]|~0|~1)*)*$")
_MAX_SAFE_INTEGER = 2**53 - 1


def _invalid() -> ValueError:
    return ValueError("invalid_privacy_value")


def _enum(value: object, enum_type: type[Enum]) -> None:
    if type(value) is not enum_type:
        raise _invalid()


def _text(value: object, pattern: re.Pattern[str], *, maximum: int = 128) -> str:
    if type(value) is not str or not value or len(value.encode("utf-8")) > maximum:
        raise _invalid()
    if pattern.fullmatch(value) is None:
        raise _invalid()
    return value


def _nonnegative(value: object, *, maximum: int = _MAX_SAFE_INTEGER) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise _invalid()
    return value


def _positive(value: object, *, maximum: int = _MAX_SAFE_INTEGER) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
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


def _sorted_enums[TEnum: Enum](values: object, enum_type: type[TEnum]) -> tuple[TEnum, ...]:
    if type(values) not in {tuple, list, set, frozenset}:
        raise _invalid()
    raw = cast(tuple[object, ...] | list[object] | set[object] | frozenset[object], values)
    if any(type(value) is not enum_type for value in raw):
        raise _invalid()
    typed = cast(tuple[TEnum, ...], tuple(raw))
    if len(set(typed)) != len(typed):
        raise _invalid()
    return tuple(sorted(typed, key=lambda value: str(value.value).encode("ascii")))


def _sorted_text(values: object, *, pattern: re.Pattern[str] = _OPAQUE) -> tuple[str, ...]:
    if type(values) not in {tuple, list, set, frozenset}:
        raise _invalid()
    raw = cast(tuple[object, ...] | list[object] | set[object] | frozenset[object], values)
    typed = tuple(_text(value, pattern) for value in raw)
    if len(set(typed)) != len(typed):
        raise _invalid()
    return tuple(sorted(typed, key=str.encode))


class PrivacyProfile(str, Enum):  # noqa: UP042 - durable enum
    LOCAL_ONLY = "local_only"
    CONFIRM_EVERY_REQUEST = "confirm_every_request"
    MINIMAL_EXTERNAL = "minimal_external"
    TRUSTED_PROVIDER = "trusted_provider"


class ReviewContextProfile(str, Enum):  # noqa: UP042 - durable enum
    STRUCTURAL = "structural"
    GOAL_AWARE = "goal_aware"
    ASSISTED = "assisted"
    EXPANDED = "expanded"
    CUSTOM = "custom"


class EgressChannel(str, Enum):  # noqa: UP042 - durable enum
    LLM_INFERENCE = "llm_inference"
    PRODUCT_TELEMETRY = "product_telemetry"
    CRASH_DIAGNOSTICS = "crash_diagnostics"
    UPDATE_CHECKS = "update_checks"
    CAPABILITY_TESTING = "capability_testing"


class LocalDisclosureSink(str, Enum):  # noqa: UP042 - durable enum
    LOCAL_MODEL = "local_model"
    AGENT_CONTEXT = "agent_context"
    LOCAL_HUMAN_VIEW = "local_human_view"
    TRUSTED_HUMAN_CONTROL = "trusted_human_control"


class DisclosureProvenance(str, Enum):  # noqa: UP042 - durable enum
    SELF_AUTHORED = "self_authored"
    ENGINE_DERIVED_FROM_SELF_AUTHORED = "engine_derived_from_self_authored"
    OTHER_WRITER = "other_writer"
    IMPORTED = "imported"


class DataClass(str, Enum):  # noqa: UP042 - durable enum
    PUBLIC_STRUCTURAL = "public_structural"
    ORDINARY_USER_CONTENT = "ordinary_user_content"
    SENSITIVE_CONFIDENTIAL = "sensitive_confidential"
    SECRET_OR_CRYPTOGRAPHIC = "secret_or_cryptographic"


class ForbiddenDataKind(str, Enum):  # noqa: UP042 - durable enum
    ENCRYPTION_KEY = "encryption_key"
    RECOVERY_OR_UNLOCK_SECRET = "recovery_or_unlock_secret"
    PASSWORD = "password"
    API_CREDENTIAL = "api_credential"
    AUTHENTICATION_TOKEN = "authentication_token"
    COOKIE = "cookie"
    PRIVATE_CERTIFICATE = "private_certificate"
    KEYRING_CONTENT = "keyring_content"
    UNRELATED_ENVIRONMENT = "unrelated_environment"
    CREDENTIAL_FILE = "credential_file"
    HIDDEN_AUTH_CONFIGURATION = "hidden_auth_configuration"
    RAW_DATABASE = "raw_database"
    UNRESTRICTED_LOG = "unrestricted_log"
    RAW_STDERR = "raw_stderr"
    COMPLETE_TRANSCRIPT = "complete_transcript"
    OUT_OF_SCOPE_FILE = "out_of_scope_file"


NEVER_SEND_KINDS: Final = frozenset(ForbiddenDataKind)


class AuthorizationScopeKind(str, Enum):  # noqa: UP042 - durable enum
    MACHINE = "machine"
    WORKSPACE = "workspace"
    TASK = "task"
    REQUEST = "request"


class ConsentSource(str, Enum):  # noqa: UP042 - durable enum
    NONE = "none"
    BASELINE_POLICY = "baseline_policy"
    SCOPED_LOCAL_HUMAN = "scoped_local_human"
    PER_REQUEST_LOCAL_HUMAN = "per_request_local_human"


class PrivacyOutcome(str, Enum):  # noqa: UP042 - durable enum
    BLOCKED_BY_POLICY = "blocked_by_policy"
    BLOCKED_FORBIDDEN_DATA = "blocked_forbidden_data"
    CLASSIFICATION_UNCERTAIN = "classification_uncertain"
    HUMAN_DENIED = "human_denied"
    APPROVAL_EXPIRED = "approval_expired"
    CHANNEL_UNAVAILABLE = "channel_unavailable"
    PROVIDER_REFUSED = "provider_refused"
    TIMEOUT = "timeout"
    INVALID_RESPONSE = "invalid_response"
    TRANSPORT_FAILED = "transport_failed"
    LATE = "late"
    STALE = "stale"
    AUDIT_FAILED = "audit_failed"
    COMPLETED = "completed"


class PrivacyReason(str, Enum):  # noqa: UP042 - durable enum
    POLICY_DENIED = "policy_denied"
    NEVER_SEND_DETECTED = "never_send_detected"
    CLASSIFICATION_UNCERTAIN = "classification_uncertain"
    SCOPE_MISMATCH = "scope_mismatch"
    PURPOSE_NOT_ALLOWED = "purpose_not_allowed"
    DESTINATION_NOT_ALLOWED = "destination_not_allowed"
    CATEGORY_NOT_ALLOWED = "category_not_allowed"
    CHANNEL_UNAVAILABLE = "channel_unavailable"
    HUMAN_DENIED = "human_denied"
    AUTHORIZATION_EXPIRED = "authorization_expired"
    AUTHORIZATION_STALE = "authorization_stale"
    AUTHORIZATION_REUSED = "authorization_reused"
    INSUFFICIENT_APPROVED_CONTEXT = "insufficient_approved_context"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_REFUSED = "provider_refused"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_INVALID_RESPONSE = "provider_invalid_response"
    TRANSPORT_FAILED = "transport_failed"
    AUDIT_FAILED = "audit_failed"
    DEADLINE_EXPIRED = "deadline_expired"
    LATE = "late"
    STALE = "stale"
    OUTCOME_UNKNOWN = "outcome_unknown"


_OUTCOME_REASONS: Final[dict[PrivacyOutcome, frozenset[PrivacyReason]]] = {
    PrivacyOutcome.BLOCKED_BY_POLICY: frozenset(
        {
            PrivacyReason.POLICY_DENIED,
            PrivacyReason.SCOPE_MISMATCH,
            PrivacyReason.PURPOSE_NOT_ALLOWED,
            PrivacyReason.DESTINATION_NOT_ALLOWED,
            PrivacyReason.CATEGORY_NOT_ALLOWED,
            PrivacyReason.INSUFFICIENT_APPROVED_CONTEXT,
        }
    ),
    PrivacyOutcome.BLOCKED_FORBIDDEN_DATA: frozenset({PrivacyReason.NEVER_SEND_DETECTED}),
    PrivacyOutcome.CLASSIFICATION_UNCERTAIN: frozenset({PrivacyReason.CLASSIFICATION_UNCERTAIN}),
    PrivacyOutcome.HUMAN_DENIED: frozenset({PrivacyReason.HUMAN_DENIED}),
    PrivacyOutcome.APPROVAL_EXPIRED: frozenset(
        {
            PrivacyReason.AUTHORIZATION_EXPIRED,
            PrivacyReason.AUTHORIZATION_STALE,
            PrivacyReason.AUTHORIZATION_REUSED,
        }
    ),
    PrivacyOutcome.CHANNEL_UNAVAILABLE: frozenset({PrivacyReason.CHANNEL_UNAVAILABLE}),
    PrivacyOutcome.PROVIDER_REFUSED: frozenset({PrivacyReason.PROVIDER_REFUSED}),
    PrivacyOutcome.TIMEOUT: frozenset(
        {PrivacyReason.PROVIDER_TIMEOUT, PrivacyReason.DEADLINE_EXPIRED}
    ),
    PrivacyOutcome.INVALID_RESPONSE: frozenset({PrivacyReason.PROVIDER_INVALID_RESPONSE}),
    PrivacyOutcome.TRANSPORT_FAILED: frozenset(
        {
            PrivacyReason.TRANSPORT_FAILED,
            PrivacyReason.PROVIDER_UNAVAILABLE,
            PrivacyReason.OUTCOME_UNKNOWN,
        }
    ),
    PrivacyOutcome.LATE: frozenset({PrivacyReason.LATE}),
    PrivacyOutcome.STALE: frozenset({PrivacyReason.STALE}),
    PrivacyOutcome.AUDIT_FAILED: frozenset({PrivacyReason.AUDIT_FAILED}),
    PrivacyOutcome.COMPLETED: frozenset(),
}


def outcome_reason_is_valid(outcome: PrivacyOutcome, reason: PrivacyReason | None) -> bool:
    if type(outcome) is not PrivacyOutcome:
        return False
    allowed = _OUTCOME_REASONS[outcome]
    return reason is None if not allowed else type(reason) is PrivacyReason and reason in allowed


@dataclass(frozen=True, slots=True)
class ProviderBinding:
    provider_id: str
    model_id: str
    endpoint_profile_id: str
    endpoint_profile_version: str
    transport: Literal["external", "local_af_unix"]

    def __post_init__(self) -> None:
        _text(self.provider_id, _IDENTITY)
        _text(self.model_id, _MODEL_IDENTITY)
        _text(self.endpoint_profile_id, _IDENTITY)
        _text(self.endpoint_profile_version, _VERSION)
        if self.transport not in {"external", "local_af_unix"}:
            raise _invalid()


@dataclass(frozen=True, slots=True)
class ProviderDataUseProfile:
    data_use_profile_id: str
    data_use_profile_version: str
    customer_content_training: Literal["prohibited", "permitted", "unknown"]
    retention: Literal["none", "bounded", "unbounded", "unknown"]
    retention_days_ceiling: int | None
    provider_human_access: Literal["prohibited", "restricted", "permitted", "unknown"]
    reviewed_at: datetime
    expires_at: datetime
    evidence_digest: str

    def __post_init__(self) -> None:
        _text(self.data_use_profile_id, _IDENTITY)
        _text(self.data_use_profile_version, _VERSION)
        if self.customer_content_training not in {"prohibited", "permitted", "unknown"}:
            raise _invalid()
        if self.retention not in {"none", "bounded", "unbounded", "unknown"}:
            raise _invalid()
        if (self.retention == "bounded") != (self.retention_days_ceiling is not None):
            raise _invalid()
        if self.retention_days_ceiling is not None:
            _nonnegative(self.retention_days_ceiling)
        if self.provider_human_access not in {
            "prohibited",
            "restricted",
            "permitted",
            "unknown",
        }:
            raise _invalid()
        _time(self.reviewed_at)
        _time(self.expires_at)
        if self.reviewed_at >= self.expires_at:
            raise _invalid()
        validate_sha256_digest(self.evidence_digest)

    def recommendation_eligible(self, now: datetime) -> bool:
        _time(now)
        return (
            now < self.expires_at
            and self.customer_content_training == "prohibited"
            and self.retention in {"none", "bounded"}
            and self.provider_human_access in {"prohibited", "restricted"}
        )


_SECTIONS: Final = frozenset(
    {
        "goal",
        "obligations",
        "claims",
        "decisions",
        "timeline",
        "deterministic_assessments",
        "change_observations",
        "coverage",
        "targeted_excerpts",
        "omissions",
    }
)
_EXCERPT_KINDS: Final = frozenset({"evidence", "test", "failure", "diff", "command", "repository"})


@dataclass(frozen=True, slots=True)
class ReviewSelectionPolicy:
    sections: tuple[str, ...]
    excerpt_kinds: tuple[str, ...]
    relevance: Literal["linked_subjects_only", "linked_then_in_scope"]
    include_finding_prose: bool
    include_exact_command_text: bool
    max_timeline_items: int
    max_assessments: int
    max_change_observations: int
    max_excerpts: int
    max_omissions: int
    max_excerpt_bytes: int
    max_total_excerpt_bytes: int

    def __post_init__(self) -> None:
        sections = _sorted_text(self.sections, pattern=_IDENTITY)
        kinds = _sorted_text(self.excerpt_kinds, pattern=_IDENTITY)
        if not set(sections) <= _SECTIONS or not set(kinds) <= _EXCERPT_KINDS:
            raise _invalid()
        object.__setattr__(self, "sections", sections)
        object.__setattr__(self, "excerpt_kinds", kinds)
        if self.relevance not in {"linked_subjects_only", "linked_then_in_scope"}:
            raise _invalid()
        if (
            type(self.include_finding_prose) is not bool
            or type(self.include_exact_command_text) is not bool
        ):
            raise _invalid()
        _nonnegative(self.max_timeline_items, maximum=64)
        _nonnegative(self.max_assessments, maximum=64)
        _nonnegative(self.max_change_observations, maximum=32)
        _nonnegative(self.max_excerpts, maximum=16)
        _nonnegative(self.max_omissions, maximum=64)
        _nonnegative(self.max_excerpt_bytes, maximum=16_384)
        _nonnegative(self.max_total_excerpt_bytes, maximum=131_072)
        if self.max_excerpts == 0:
            if self.max_excerpt_bytes != 0 or self.max_total_excerpt_bytes != 0 or kinds:
                raise _invalid()
        elif self.max_excerpt_bytes == 0 or self.max_total_excerpt_bytes == 0:
            raise _invalid()

    @classmethod
    def for_profile(cls, profile: ReviewContextProfile) -> ReviewSelectionPolicy:
        _enum(profile, ReviewContextProfile)
        if profile is ReviewContextProfile.CUSTOM:
            raise _invalid()
        sections = {
            "timeline",
            "deterministic_assessments",
            "change_observations",
            "coverage",
            "omissions",
        }
        finding = False
        kinds: set[str] = set()
        relevance: Literal["linked_subjects_only", "linked_then_in_scope"] = "linked_subjects_only"
        exact_commands = False
        max_excerpts = max_excerpt_bytes = max_total_excerpt_bytes = 0
        if profile is not ReviewContextProfile.STRUCTURAL:
            sections.update({"goal", "obligations", "claims", "decisions"})
            finding = True
        if profile in {ReviewContextProfile.ASSISTED, ReviewContextProfile.EXPANDED}:
            sections.add("targeted_excerpts")
            kinds = set(_EXCERPT_KINDS)
            max_excerpts = 16
            max_excerpt_bytes = 16_384
            max_total_excerpt_bytes = 131_072
        if profile is ReviewContextProfile.EXPANDED:
            relevance = "linked_then_in_scope"
            exact_commands = True
        return cls(
            sections=tuple(sections),
            excerpt_kinds=tuple(kinds),
            relevance=relevance,
            include_finding_prose=finding,
            include_exact_command_text=exact_commands,
            max_timeline_items=64,
            max_assessments=64,
            max_change_observations=32,
            max_excerpts=max_excerpts,
            max_omissions=64,
            max_excerpt_bytes=max_excerpt_bytes,
            max_total_excerpt_bytes=max_total_excerpt_bytes,
        )

    def meet(self, other: ReviewSelectionPolicy) -> ReviewSelectionPolicy:
        if type(other) is not ReviewSelectionPolicy:
            raise _invalid()
        return ReviewSelectionPolicy(
            sections=tuple(set(self.sections) & set(other.sections)),
            excerpt_kinds=tuple(set(self.excerpt_kinds) & set(other.excerpt_kinds)),
            relevance=(
                "linked_subjects_only"
                if "linked_subjects_only" in {self.relevance, other.relevance}
                else "linked_then_in_scope"
            ),
            include_finding_prose=self.include_finding_prose and other.include_finding_prose,
            include_exact_command_text=(
                self.include_exact_command_text and other.include_exact_command_text
            ),
            max_timeline_items=min(self.max_timeline_items, other.max_timeline_items),
            max_assessments=min(self.max_assessments, other.max_assessments),
            max_change_observations=min(
                self.max_change_observations, other.max_change_observations
            ),
            max_excerpts=min(self.max_excerpts, other.max_excerpts),
            max_omissions=min(self.max_omissions, other.max_omissions),
            max_excerpt_bytes=min(self.max_excerpt_bytes, other.max_excerpt_bytes),
            max_total_excerpt_bytes=min(
                self.max_total_excerpt_bytes, other.max_total_excerpt_bytes
            ),
        )


@dataclass(frozen=True, slots=True)
class AuthorizationScope:
    kind: AuthorizationScopeKind
    installation_id: str
    workspace_ref_commitment: str | None = None
    task_id: str | None = None
    request_id: str | None = None

    def __post_init__(self) -> None:
        _enum(self.kind, AuthorizationScopeKind)
        validate_id(IdKind.INSTALLATION, self.installation_id)
        needs_workspace = self.kind is not AuthorizationScopeKind.MACHINE
        needs_task = self.kind in {AuthorizationScopeKind.TASK, AuthorizationScopeKind.REQUEST}
        needs_request = self.kind is AuthorizationScopeKind.REQUEST
        if (self.workspace_ref_commitment is not None) != needs_workspace:
            raise _invalid()
        if self.workspace_ref_commitment is not None:
            validate_commitment(self.workspace_ref_commitment)
        if (self.task_id is not None) != needs_task:
            raise _invalid()
        if self.task_id is not None:
            validate_id(IdKind.TASK, self.task_id)
        if (self.request_id is not None) != needs_request:
            raise _invalid()
        if self.request_id is not None:
            validate_id(IdKind.REQUEST, self.request_id)

    def contains(self, child: AuthorizationScope) -> bool:
        if type(child) is not AuthorizationScope or self.installation_id != child.installation_id:
            return False
        if self.kind is AuthorizationScopeKind.MACHINE:
            return True
        if self.workspace_ref_commitment != child.workspace_ref_commitment:
            return False
        if self.kind is AuthorizationScopeKind.WORKSPACE:
            return True
        if self.task_id != child.task_id:
            return False
        if self.kind is AuthorizationScopeKind.TASK:
            return True
        return self.request_id == child.request_id


@dataclass(frozen=True, slots=True)
class ChannelPolicy:
    channel: EgressChannel
    enabled: bool
    allowed_categories: tuple[DataCategory, ...]
    allowed_data_classes: tuple[DataClass, ...]
    provider_binding: ProviderBinding | None
    allowed_purposes: tuple[str, ...]
    scope_ceiling: AuthorizationScopeKind
    preview_required: bool
    max_bytes: int
    max_tokens: int
    authorization_ttl_seconds: int

    def __post_init__(self) -> None:
        _enum(self.channel, EgressChannel)
        if type(self.enabled) is not bool or type(self.preview_required) is not bool:
            raise _invalid()
        object.__setattr__(
            self, "allowed_categories", _sorted_enums(self.allowed_categories, DataCategory)
        )
        object.__setattr__(
            self, "allowed_data_classes", _sorted_enums(self.allowed_data_classes, DataClass)
        )
        object.__setattr__(
            self, "allowed_purposes", _sorted_text(self.allowed_purposes, pattern=_PURPOSE)
        )
        _enum(self.scope_ceiling, AuthorizationScopeKind)
        _nonnegative(self.max_bytes, maximum=MAX_EGRESS_CASE_BYTES)
        _nonnegative(self.max_tokens)
        _nonnegative(self.authorization_ttl_seconds, maximum=86_400)
        if not self.enabled:
            if (
                self.allowed_categories
                or self.allowed_data_classes
                or self.provider_binding is not None
                or self.allowed_purposes
                or self.preview_required
                or self.max_bytes
                or self.max_tokens
                or self.authorization_ttl_seconds
            ):
                raise _invalid()
        if self.provider_binding is not None and type(self.provider_binding) is not ProviderBinding:
            raise _invalid()
        if self.channel is not EgressChannel.LLM_INFERENCE:
            if self.provider_binding is not None:
                raise _invalid()
            if set(self.allowed_data_classes) - {DataClass.PUBLIC_STRUCTURAL}:
                raise _invalid()


@dataclass(frozen=True, slots=True)
class PrivacyPolicy:
    policy_id: str
    version: int
    policy_digest: str
    profile: PrivacyProfile
    review_context_profile: ReviewContextProfile
    review_selection: ReviewSelectionPolicy
    require_current_provider_data_use_evidence: bool
    network_egress_permitted: bool
    effective_scope: AuthorizationScope
    channel_policies: tuple[ChannelPolicy, ...]
    local_model_enabled: bool
    local_model_binding: ProviderBinding | None
    local_model_categories: tuple[DataCategory, ...]
    local_model_data_classes: tuple[DataClass, ...]
    agent_context_categories: tuple[DataCategory, ...]
    agent_context_data_classes: tuple[DataClass, ...]
    trusted_human_control_categories: tuple[DataCategory, ...]
    trusted_human_control_data_classes: tuple[DataClass, ...]
    created_at: datetime
    supersedes_policy_digest: str | None = None

    def __post_init__(self) -> None:
        validate_id(IdKind.PRIVACY_POLICY, self.policy_id)
        _positive(self.version)
        validate_sha256_digest(self.policy_digest)
        _enum(self.profile, PrivacyProfile)
        _enum(self.review_context_profile, ReviewContextProfile)
        if type(self.review_selection) is not ReviewSelectionPolicy:
            raise _invalid()
        if self.review_context_profile is not ReviewContextProfile.CUSTOM:
            if self.review_selection != ReviewSelectionPolicy.for_profile(
                self.review_context_profile
            ):
                raise _invalid()
        if type(self.require_current_provider_data_use_evidence) is not bool:
            raise _invalid()
        if type(self.network_egress_permitted) is not bool:
            raise _invalid()
        if type(self.effective_scope) is not AuthorizationScope:
            raise _invalid()
        if type(self.channel_policies) is not tuple or len(self.channel_policies) != 5:
            raise _invalid()
        if any(type(policy) is not ChannelPolicy for policy in self.channel_policies):
            raise _invalid()
        ordered = tuple(
            sorted(self.channel_policies, key=lambda policy: policy.channel.value.encode())
        )
        if self.channel_policies != ordered or {policy.channel for policy in ordered} != set(
            EgressChannel
        ):
            raise _invalid()
        llm = next(policy for policy in ordered if policy.channel is EgressChannel.LLM_INFERENCE)
        if not self.network_egress_permitted and any(policy.enabled for policy in ordered):
            raise _invalid()
        if self.profile is PrivacyProfile.LOCAL_ONLY:
            if llm.enabled or llm.provider_binding is not None:
                raise _invalid()
        elif (
            not self.network_egress_permitted
            or not llm.enabled
            or llm.provider_binding is None
            or llm.provider_binding.transport != "external"
        ):
            raise _invalid()
        if self.profile is PrivacyProfile.CONFIRM_EVERY_REQUEST and not llm.preview_required:
            raise _invalid()
        if (
            self.profile is PrivacyProfile.MINIMAL_EXTERNAL
            and DataClass.SENSITIVE_CONFIDENTIAL in llm.allowed_data_classes
        ):
            raise _invalid()
        if self.require_current_provider_data_use_evidence and llm.provider_binding is None:
            raise _invalid()
        if type(self.local_model_enabled) is not bool:
            raise _invalid()
        if self.local_model_enabled != (self.local_model_binding is not None):
            raise _invalid()
        if (
            self.local_model_binding is not None
            and self.local_model_binding.transport != "local_af_unix"
        ):
            raise _invalid()
        for name, enum_type in (
            ("local_model_categories", DataCategory),
            ("local_model_data_classes", DataClass),
            ("agent_context_categories", DataCategory),
            ("agent_context_data_classes", DataClass),
            ("trusted_human_control_categories", DataCategory),
            ("trusted_human_control_data_classes", DataClass),
        ):
            object.__setattr__(self, name, _sorted_enums(getattr(self, name), enum_type))
        if any(
            DataClass.SECRET_OR_CRYPTOGRAPHIC in values
            for values in (
                self.local_model_data_classes,
                self.agent_context_data_classes,
                self.trusted_human_control_data_classes,
            )
        ):
            raise _invalid()
        _time(self.created_at)
        if self.supersedes_policy_digest is not None:
            validate_sha256_digest(self.supersedes_policy_digest)

    @property
    def unsupported_enabled_channels(self) -> tuple[EgressChannel, ...]:
        return tuple(
            policy.channel
            for policy in self.channel_policies
            if policy.enabled and policy.channel is not EgressChannel.LLM_INFERENCE
        )


@dataclass(frozen=True, slots=True)
class PolicyOverlay:
    scope: AuthorizationScope
    review_selection: ReviewSelectionPolicy
    require_current_provider_data_use_evidence: bool
    channel_policies: tuple[ChannelPolicy, ...]
    local_model_categories: tuple[DataCategory, ...]
    local_model_data_classes: tuple[DataClass, ...]
    agent_context_categories: tuple[DataCategory, ...]
    agent_context_data_classes: tuple[DataClass, ...]

    def __post_init__(self) -> None:
        if (
            type(self.scope) is not AuthorizationScope
            or type(self.review_selection) is not ReviewSelectionPolicy
        ):
            raise _invalid()
        if type(self.require_current_provider_data_use_evidence) is not bool:
            raise _invalid()
        if type(self.channel_policies) is not tuple or any(
            type(value) is not ChannelPolicy for value in self.channel_policies
        ):
            raise _invalid()
        for name, enum_type in (
            ("local_model_categories", DataCategory),
            ("local_model_data_classes", DataClass),
            ("agent_context_categories", DataCategory),
            ("agent_context_data_classes", DataClass),
        ):
            object.__setattr__(self, name, _sorted_enums(getattr(self, name), enum_type))


@dataclass(frozen=True, slots=True)
class CandidateContextItem:
    item_id: str
    category: DataCategory
    source_scope: AuthorizationScope
    origin_ref: str
    plaintext: bytes

    def __post_init__(self) -> None:
        _text(self.item_id, _OPAQUE)
        _enum(self.category, DataCategory)
        if type(self.source_scope) is not AuthorizationScope:
            raise _invalid()
        _text(self.origin_ref, _OPAQUE)
        if type(self.plaintext) is not bytes or len(self.plaintext) > MAX_EGRESS_ITEM_BYTES:
            raise _invalid()


@dataclass(frozen=True, slots=True)
class CandidateContext:
    request_id: str
    channel: EgressChannel | None
    local_sink: LocalDisclosureSink | None
    purpose: str
    scope: AuthorizationScope
    subject_digest: str | None
    provider_binding: ProviderBinding | None
    items: tuple[CandidateContextItem, ...]

    def __post_init__(self) -> None:
        validate_id(IdKind.REQUEST, self.request_id)
        if (self.channel is None) == (self.local_sink is None):
            raise _invalid()
        if self.channel is not None:
            _enum(self.channel, EgressChannel)
        if self.local_sink is not None:
            _enum(self.local_sink, LocalDisclosureSink)
        _text(self.purpose, _PURPOSE)
        if type(self.scope) is not AuthorizationScope:
            raise _invalid()
        if self.subject_digest is not None:
            validate_sha256_digest(self.subject_digest)
        if self.provider_binding is not None and type(self.provider_binding) is not ProviderBinding:
            raise _invalid()
        if type(self.items) is not tuple or any(
            type(item) is not CandidateContextItem for item in self.items
        ):
            raise _invalid()
        if len({item.item_id for item in self.items}) != len(self.items):
            raise _invalid()


@dataclass(frozen=True, slots=True)
class ClassifiedContextItem:
    candidate: CandidateContextItem
    data_class: DataClass
    forbidden_findings: tuple[ForbiddenDataKind, ...]
    scope_valid: bool
    classifier_ruleset_version: str

    def __post_init__(self) -> None:
        if type(self.candidate) is not CandidateContextItem:
            raise _invalid()
        _enum(self.data_class, DataClass)
        object.__setattr__(
            self, "forbidden_findings", _sorted_enums(self.forbidden_findings, ForbiddenDataKind)
        )
        if type(self.scope_valid) is not bool:
            raise _invalid()
        _text(self.classifier_ruleset_version, _VERSION)


@dataclass(frozen=True, slots=True)
class ClassifiedContext:
    candidate: CandidateContext
    items: tuple[ClassifiedContextItem, ...]

    def __post_init__(self) -> None:
        if type(self.candidate) is not CandidateContext or type(self.items) is not tuple:
            raise _invalid()
        if any(type(item) is not ClassifiedContextItem for item in self.items):
            raise _invalid()
        if tuple(item.candidate for item in self.items) != self.candidate.items:
            raise _invalid()


@dataclass(frozen=True, slots=True)
class PrivacyDecision:
    approved_item_ids: tuple[str, ...]
    blocked_categories: tuple[DataCategory, ...]
    outcome: PrivacyOutcome
    reason: PrivacyReason | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "approved_item_ids", _sorted_text(self.approved_item_ids))
        object.__setattr__(
            self, "blocked_categories", _sorted_enums(self.blocked_categories, DataCategory)
        )
        if not outcome_reason_is_valid(self.outcome, self.reason):
            raise _invalid()


@dataclass(frozen=True, slots=True)
class PreDispatchAuditDecision:
    privacy_proposal_id: str
    request_id: str
    channel: EgressChannel | None
    local_sink: LocalDisclosureSink | None
    purpose: str
    scope: AuthorizationScope
    policy_id: str
    policy_version: int
    policy_digest: str
    destination_digest: str | None
    categories: tuple[DataCategory, ...]
    candidate_count: int
    blocked_count: int
    forbidden_kind_counts: tuple[tuple[ForbiddenDataKind, int], ...]
    finished_at: datetime
    audit_subject_digest: str
    outcome: PrivacyOutcome
    reason: PrivacyReason

    def __post_init__(self) -> None:
        validate_id(IdKind.PRIVACY_PROPOSAL, self.privacy_proposal_id)
        validate_id(IdKind.REQUEST, self.request_id)
        if (self.channel is None) == (self.local_sink is None):
            raise _invalid()
        _text(self.purpose, _PURPOSE)
        if type(self.scope) is not AuthorizationScope:
            raise _invalid()
        validate_id(IdKind.PRIVACY_POLICY, self.policy_id)
        _positive(self.policy_version)
        validate_sha256_digest(self.policy_digest)
        if self.destination_digest is not None:
            validate_sha256_digest(self.destination_digest)
        object.__setattr__(self, "categories", _sorted_enums(self.categories, DataCategory))
        _nonnegative(self.candidate_count)
        _nonnegative(self.blocked_count)
        if type(self.forbidden_kind_counts) is not tuple:
            raise _invalid()
        expected = tuple(
            sorted(self.forbidden_kind_counts, key=lambda item: item[0].value.encode())
        )
        if self.forbidden_kind_counts != expected or len({kind for kind, _ in expected}) != len(
            expected
        ):
            raise _invalid()
        for kind, count in expected:
            _enum(kind, ForbiddenDataKind)
            _positive(count)
        _time(self.finished_at)
        validate_sha256_digest(self.audit_subject_digest)
        if self.outcome not in {
            PrivacyOutcome.BLOCKED_BY_POLICY,
            PrivacyOutcome.BLOCKED_FORBIDDEN_DATA,
            PrivacyOutcome.CLASSIFICATION_UNCERTAIN,
            PrivacyOutcome.CHANNEL_UNAVAILABLE,
        } or not outcome_reason_is_valid(self.outcome, self.reason):
            raise _invalid()


@dataclass(frozen=True, slots=True)
class AgentProjectionAuditSubject:
    privacy_proposal_id: str
    projection_request_id: str
    rpc_id: str
    method: str
    service_instance_id: str
    service_generation: int
    original_request_id: str | None
    scope: AuthorizationScope
    task_id: str | None
    route_identity_digest: str | None
    policy_id: str
    policy_version: int
    policy_digest: str
    sink: LocalDisclosureSink
    provenance: tuple[DisclosureProvenance, ...]
    internal_result_commitment: str
    projection_commitment: str
    field_decisions: tuple[tuple[str, DataCategory, bool, str | None], ...]
    candidate_count: int
    approved_count: int
    omitted_count: int
    finished_at: datetime

    def __post_init__(self) -> None:
        validate_id(IdKind.PRIVACY_PROPOSAL, self.privacy_proposal_id)
        validate_id(IdKind.REQUEST, self.projection_request_id)
        validate_id(IdKind.CONTROL_RPC, self.rpc_id)
        _text(self.method, _IDENTITY)
        validate_id(IdKind.SERVICE_INSTANCE, self.service_instance_id)
        _positive(self.service_generation)
        if self.original_request_id is not None:
            validate_id(IdKind.REQUEST, self.original_request_id)
        if self.sink not in {
            LocalDisclosureSink.AGENT_CONTEXT,
            LocalDisclosureSink.LOCAL_HUMAN_VIEW,
        }:
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
        validate_sha256_digest(self.policy_digest)
        object.__setattr__(self, "provenance", _sorted_enums(self.provenance, DisclosureProvenance))
        validate_commitment(self.internal_result_commitment)
        validate_commitment(self.projection_commitment)
        if type(self.field_decisions) is not tuple:
            raise _invalid()
        expected = tuple(sorted(self.field_decisions, key=lambda item: item[0].encode()))
        if expected != self.field_decisions:
            raise _invalid()
        for pointer, category, allowed, reason in expected:
            _text(pointer, _POINTER, maximum=256)
            _enum(category, DataCategory)
            if (
                type(allowed) is not bool
                or (allowed and reason is not None)
                or (not allowed and not reason)
            ):
                raise _invalid()
        _nonnegative(self.candidate_count)
        _nonnegative(self.approved_count)
        _nonnegative(self.omitted_count)
        if self.approved_count + self.omitted_count != self.candidate_count:
            raise _invalid()
        _time(self.finished_at)


@dataclass(frozen=True, slots=True)
class DisclosureProposal:
    privacy_proposal_id: str
    request_id: str
    task_id: str
    source_item_digests: tuple[str, ...]
    prepared_bytes: bytes
    approved_categories: tuple[DataCategory, ...]
    blocked_categories: tuple[DataCategory, ...]
    transformation_summary: tuple[tuple[str, int], ...]
    prepared_case_digest: str
    provider_binding: ProviderBinding | None
    local_sink: LocalDisclosureSink | None
    purpose: str
    scope: AuthorizationScope
    policy_version: int
    policy_digest: str
    max_bytes: int
    max_tokens: int
    expires_at: datetime
    proposal_commitment: str

    def __post_init__(self) -> None:
        validate_id(IdKind.PRIVACY_PROPOSAL, self.privacy_proposal_id)
        validate_id(IdKind.REQUEST, self.request_id)
        validate_id(IdKind.TASK, self.task_id)
        if (self.provider_binding is None) == (self.local_sink is None):
            raise _invalid()
        if (
            type(self.prepared_bytes) is not bytes
            or len(self.prepared_bytes) > MAX_EGRESS_CASE_BYTES
        ):
            raise _invalid()
        for digest in self.source_item_digests:
            validate_sha256_digest(digest)
        if tuple(sorted(set(self.source_item_digests), key=str.encode)) != self.source_item_digests:
            raise _invalid()
        object.__setattr__(
            self, "approved_categories", _sorted_enums(self.approved_categories, DataCategory)
        )
        object.__setattr__(
            self, "blocked_categories", _sorted_enums(self.blocked_categories, DataCategory)
        )
        validate_sha256_digest(self.prepared_case_digest)
        _text(self.purpose, _PURPOSE)
        _positive(self.policy_version)
        validate_sha256_digest(self.policy_digest)
        _nonnegative(self.max_bytes, maximum=MAX_EGRESS_CASE_BYTES)
        _nonnegative(self.max_tokens)
        _time(self.expires_at)
        validate_commitment(self.proposal_commitment)


type PrivacyAuditSubject = (
    PreDispatchAuditDecision | AgentProjectionAuditSubject | DisclosureProposal
)


@dataclass(frozen=True, slots=True)
class HumanPrivacyDecision:
    proposal_commitment: str
    approved: bool
    consent_source: ConsentSource
    decided_at: datetime
    expires_at: datetime | None
    accepted_diff_digest: str
    authority_commitment: str

    def __post_init__(self) -> None:
        validate_commitment(self.proposal_commitment)
        if type(self.approved) is not bool:
            raise _invalid()
        if self.consent_source not in {
            ConsentSource.SCOPED_LOCAL_HUMAN,
            ConsentSource.PER_REQUEST_LOCAL_HUMAN,
        }:
            raise _invalid()
        _time(self.decided_at)
        if self.expires_at is not None and _time(self.expires_at) <= self.decided_at:
            raise _invalid()
        validate_sha256_digest(self.accepted_diff_digest)
        validate_commitment(self.authority_commitment)


@dataclass(frozen=True, slots=True)
class EgressAuthorization:
    authorization_id: str
    privacy_proposal_id: str
    case_digest: str
    channel: EgressChannel
    provider_binding: ProviderBinding
    purpose: str
    scope: AuthorizationScope
    policy_version: int
    policy_digest: str
    max_bytes: int
    max_tokens: int
    consent_source: ConsentSource
    issued_at: datetime
    expires_at: datetime
    service_generation: int

    def __post_init__(self) -> None:
        validate_id(IdKind.EGRESS_AUTHORIZATION, self.authorization_id)
        validate_id(IdKind.PRIVACY_PROPOSAL, self.privacy_proposal_id)
        validate_sha256_digest(self.case_digest)
        _enum(self.channel, EgressChannel)
        if (
            type(self.provider_binding) is not ProviderBinding
            or self.provider_binding.transport != "external"
        ):
            raise _invalid()
        _text(self.purpose, _PURPOSE)
        _positive(self.policy_version)
        validate_sha256_digest(self.policy_digest)
        _nonnegative(self.max_bytes, maximum=MAX_EGRESS_CASE_BYTES)
        _nonnegative(self.max_tokens)
        _enum(self.consent_source, ConsentSource)
        _time(self.issued_at)
        if _time(self.expires_at) <= self.issued_at:
            raise _invalid()
        _positive(self.service_generation)


@dataclass(frozen=True, slots=True)
class ApprovedOutboundCase:
    case_id: str
    request_id: str
    payload: bytes
    media_type: str
    schema_id: str
    included_item_ids: tuple[str, ...]
    approved_categories: tuple[DataCategory, ...]
    blocked_categories: tuple[DataCategory, ...]
    byte_count: int
    token_count: int
    provider_binding: ProviderBinding
    purpose: str
    authorization_id: str
    policy_digest: str
    case_digest: str

    def __post_init__(self) -> None:
        validate_id(IdKind.OUTBOUND_CASE, self.case_id)
        validate_id(IdKind.REQUEST, self.request_id)
        if type(self.payload) is not bytes or len(self.payload) > MAX_EGRESS_CASE_BYTES:
            raise _invalid()
        _text(self.media_type, re.compile(r"^[a-z][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$", re.ASCII))
        _text(self.schema_id, _OPAQUE)
        object.__setattr__(self, "included_item_ids", _sorted_text(self.included_item_ids))
        object.__setattr__(
            self, "approved_categories", _sorted_enums(self.approved_categories, DataCategory)
        )
        object.__setattr__(
            self, "blocked_categories", _sorted_enums(self.blocked_categories, DataCategory)
        )
        if _nonnegative(self.byte_count, maximum=MAX_EGRESS_CASE_BYTES) != len(self.payload):
            raise _invalid()
        _nonnegative(self.token_count)
        if (
            type(self.provider_binding) is not ProviderBinding
            or self.provider_binding.transport != "external"
        ):
            raise _invalid()
        _text(self.purpose, _PURPOSE)
        validate_id(IdKind.EGRESS_AUTHORIZATION, self.authorization_id)
        validate_sha256_digest(self.policy_digest)
        validate_sha256_digest(self.case_digest)


@dataclass(frozen=True, slots=True)
class ApprovedLocalDisclosureCase:
    case_id: str
    request_id: str
    privacy_proposal_id: str
    payload: bytes
    media_type: str
    included_item_ids: tuple[str, ...]
    approved_categories: tuple[DataCategory, ...]
    blocked_categories: tuple[DataCategory, ...]
    byte_count: int
    token_count: int
    sink: LocalDisclosureSink
    binding: ProviderBinding | None
    purpose: str
    policy_digest: str
    case_digest: str

    def __post_init__(self) -> None:
        validate_id(IdKind.OUTBOUND_CASE, self.case_id)
        validate_id(IdKind.REQUEST, self.request_id)
        validate_id(IdKind.PRIVACY_PROPOSAL, self.privacy_proposal_id)
        if type(self.payload) is not bytes or len(self.payload) > MAX_EGRESS_CASE_BYTES:
            raise _invalid()
        _text(self.media_type, re.compile(r"^[a-z][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$", re.ASCII))
        object.__setattr__(self, "included_item_ids", _sorted_text(self.included_item_ids))
        object.__setattr__(
            self, "approved_categories", _sorted_enums(self.approved_categories, DataCategory)
        )
        object.__setattr__(
            self, "blocked_categories", _sorted_enums(self.blocked_categories, DataCategory)
        )
        if self.byte_count != len(self.payload):
            raise _invalid()
        _nonnegative(self.token_count)
        _enum(self.sink, LocalDisclosureSink)
        if self.sink is LocalDisclosureSink.LOCAL_MODEL:
            if self.binding is None or self.binding.transport != "local_af_unix":
                raise _invalid()
        elif self.binding is not None:
            raise _invalid()
        _text(self.purpose, _PURPOSE)
        validate_sha256_digest(self.policy_digest)
        validate_sha256_digest(self.case_digest)


type ApprovedProviderCase = ApprovedOutboundCase | ApprovedLocalDisclosureCase


@dataclass(frozen=True, slots=True)
class RequestCommitment:
    algorithm: Literal["hmac-sha256/yoetz-privacy-egress-request-v1"]
    commitment: str

    def __post_init__(self) -> None:
        if self.algorithm != PRIVACY_REQUEST_COMMITMENT_ALGORITHM:
            raise _invalid()
        validate_commitment(self.commitment)


@dataclass(frozen=True, slots=True)
class NonLlmDestination:
    kind: EgressChannel
    profile_id: str
    profile_version: str

    def __post_init__(self) -> None:
        if self.kind not in {
            EgressChannel.CAPABILITY_TESTING,
            EgressChannel.CRASH_DIAGNOSTICS,
            EgressChannel.PRODUCT_TELEMETRY,
            EgressChannel.UPDATE_CHECKS,
        }:
            raise _invalid()
        _text(self.profile_id, _OPAQUE)
        _text(self.profile_version, _OPAQUE)


@dataclass(frozen=True, slots=True)
class ReceiptPolicyBinding:
    policy_id: str
    version: int
    policy_digest: str
    authorization_scope_digest: str

    def __post_init__(self) -> None:
        validate_id(IdKind.PRIVACY_POLICY, self.policy_id)
        _positive(self.version)
        validate_sha256_digest(self.policy_digest)
        validate_sha256_digest(self.authorization_scope_digest)


@dataclass(frozen=True, slots=True)
class ReceiptCounts:
    candidate_items: int
    included_items: int
    removed_items: int
    approved_items: int
    blocked_items: int
    candidate_bytes: int
    final_bytes: int
    estimated_input_tokens: int | None = None
    request_body_bytes: int | None = None

    def __post_init__(self) -> None:
        for value in (
            self.candidate_items,
            self.included_items,
            self.removed_items,
            self.approved_items,
            self.blocked_items,
            self.candidate_bytes,
        ):
            _nonnegative(value)
        _nonnegative(self.final_bytes, maximum=MAX_EGRESS_CASE_BYTES)
        if self.estimated_input_tokens is not None:
            _nonnegative(self.estimated_input_tokens)
        if self.request_body_bytes is not None:
            _nonnegative(self.request_body_bytes)


@dataclass(frozen=True, slots=True)
class ReceiptTransformations:
    minimized_items: int
    redacted_spans: int
    blocked_items: int

    def __post_init__(self) -> None:
        _nonnegative(self.minimized_items)
        _nonnegative(self.redacted_spans)
        _nonnegative(self.blocked_items)


@dataclass(frozen=True, slots=True)
class ReceiptSecretScan:
    registry_version: str
    scanner_profile_digest: str
    match_count: int
    passed: bool

    def __post_init__(self) -> None:
        _text(self.registry_version, _VERSION)
        validate_sha256_digest(self.scanner_profile_digest)
        _nonnegative(self.match_count)
        if type(self.passed) is not bool:
            raise _invalid()


@dataclass(frozen=True, slots=True)
class EgressReceipt:
    schema_version: Literal["1.0.0"]
    receipt_id: str
    request_id: str
    privacy_proposal_id: str
    channel: EgressChannel
    outcome: PrivacyOutcome
    finished_at: datetime
    scope: AuthorizationScope
    purpose: str
    destination: ProviderBinding | NonLlmDestination
    policy: ReceiptPolicyBinding
    consent_source: ConsentSource
    approved_categories: tuple[DataCategory, ...]
    blocked_categories: tuple[DataCategory, ...]
    counts: ReceiptCounts
    transformations: ReceiptTransformations
    secret_scan: ReceiptSecretScan
    safe_failure_reason: PrivacyReason | None
    audit_store_version: Literal[1]
    authorization_id: str | None = None
    dispatch_id: str | None = None
    dispatch_started_at: datetime | None = None
    request_commitment: RequestCommitment | None = None

    def __post_init__(self) -> None:
        if type(self.schema_version) is not str or self.schema_version != "1.0.0":
            raise _invalid()
        validate_id(IdKind.EGRESS_RECEIPT, self.receipt_id)
        validate_id(IdKind.REQUEST, self.request_id)
        validate_id(IdKind.PRIVACY_PROPOSAL, self.privacy_proposal_id)
        _enum(self.channel, EgressChannel)
        _enum(self.outcome, PrivacyOutcome)
        _time(self.finished_at)
        if type(self.scope) is not AuthorizationScope:
            raise _invalid()
        _text(self.purpose, _PURPOSE)
        if self.channel is EgressChannel.LLM_INFERENCE:
            if (
                type(self.destination) is not ProviderBinding
                or self.destination.transport != "external"
            ):
                raise _invalid()
        elif (
            type(self.destination) is not NonLlmDestination
            or self.destination.kind is not self.channel
        ):
            raise _invalid()
        if type(self.policy) is not ReceiptPolicyBinding:
            raise _invalid()
        _enum(self.consent_source, ConsentSource)
        object.__setattr__(
            self, "approved_categories", _sorted_enums(self.approved_categories, DataCategory)
        )
        object.__setattr__(
            self, "blocked_categories", _sorted_enums(self.blocked_categories, DataCategory)
        )
        if (
            type(self.counts) is not ReceiptCounts
            or type(self.transformations) is not ReceiptTransformations
            or type(self.secret_scan) is not ReceiptSecretScan
            or not outcome_reason_is_valid(self.outcome, self.safe_failure_reason)
        ):
            raise _invalid()
        attempted = self.dispatch_id is not None
        if self.authorization_id is not None:
            validate_id(IdKind.EGRESS_AUTHORIZATION, self.authorization_id)
        if attempted:
            if (
                self.authorization_id is None
                or self.dispatch_started_at is None
                or self.request_commitment is None
                or self.counts.request_body_bytes is None
            ):
                raise _invalid()
            validate_id(IdKind.EGRESS_DISPATCH, self.dispatch_id)
            if _time(self.dispatch_started_at) > self.finished_at:
                raise _invalid()
            if type(self.request_commitment) is not RequestCommitment:
                raise _invalid()
        elif any(
            value is not None
            for value in (
                self.dispatch_started_at,
                self.request_commitment,
                self.counts.request_body_bytes,
            )
        ):
            raise _invalid()
        if self.outcome in {
            PrivacyOutcome.BLOCKED_BY_POLICY,
            PrivacyOutcome.BLOCKED_FORBIDDEN_DATA,
            PrivacyOutcome.CLASSIFICATION_UNCERTAIN,
            PrivacyOutcome.HUMAN_DENIED,
            PrivacyOutcome.CHANNEL_UNAVAILABLE,
        } and (attempted or self.authorization_id is not None):
            raise _invalid()
        if self.outcome is PrivacyOutcome.COMPLETED and not attempted:
            raise _invalid()
        if (
            self.outcome
            in {
                PrivacyOutcome.BLOCKED_BY_POLICY,
                PrivacyOutcome.BLOCKED_FORBIDDEN_DATA,
                PrivacyOutcome.CLASSIFICATION_UNCERTAIN,
                PrivacyOutcome.HUMAN_DENIED,
                PrivacyOutcome.CHANNEL_UNAVAILABLE,
            }
            and self.consent_source is not ConsentSource.NONE
        ):
            raise _invalid()
        if (
            type(self.audit_store_version) is not int
            or self.audit_store_version != AUDIT_STORE_VERSION
        ):
            raise _invalid()


@dataclass(frozen=True, slots=True)
class LocalDisclosureReceipt:
    schema_version: Literal["1.0.0"]
    receipt_id: str
    request_id: str
    privacy_proposal_id: str
    sink: LocalDisclosureSink
    outcome: PrivacyOutcome
    finished_at: datetime
    scope: AuthorizationScope
    purpose: str
    policy: ReceiptPolicyBinding
    consent_source: ConsentSource
    approved_categories: tuple[DataCategory, ...]
    blocked_categories: tuple[DataCategory, ...]
    counts: ReceiptCounts
    transformations: ReceiptTransformations
    secret_scan: ReceiptSecretScan
    safe_failure_reason: PrivacyReason | None
    audit_store_version: Literal[1]

    def __post_init__(self) -> None:
        if type(self.schema_version) is not str or self.schema_version != "1.0.0":
            raise _invalid()
        validate_id(IdKind.EGRESS_RECEIPT, self.receipt_id)
        validate_id(IdKind.REQUEST, self.request_id)
        validate_id(IdKind.PRIVACY_PROPOSAL, self.privacy_proposal_id)
        _enum(self.sink, LocalDisclosureSink)
        _enum(self.outcome, PrivacyOutcome)
        _time(self.finished_at)
        if (
            type(self.scope) is not AuthorizationScope
            or type(self.policy) is not ReceiptPolicyBinding
        ):
            raise _invalid()
        _text(self.purpose, _PURPOSE)
        _enum(self.consent_source, ConsentSource)
        object.__setattr__(
            self, "approved_categories", _sorted_enums(self.approved_categories, DataCategory)
        )
        object.__setattr__(
            self, "blocked_categories", _sorted_enums(self.blocked_categories, DataCategory)
        )
        if (
            type(self.counts) is not ReceiptCounts
            or self.counts.request_body_bytes is not None
            or type(self.transformations) is not ReceiptTransformations
            or type(self.secret_scan) is not ReceiptSecretScan
            or not outcome_reason_is_valid(self.outcome, self.safe_failure_reason)
            or type(self.audit_store_version) is not int
            or self.audit_store_version != 1
        ):
            raise _invalid()


@dataclass(frozen=True, slots=True)
class ApprovedLocalItem:
    json_pointer: str
    category: DataCategory
    bounded_bytes: bytes

    def __post_init__(self) -> None:
        _text(self.json_pointer, _POINTER, maximum=256)
        _enum(self.category, DataCategory)
        if type(self.bounded_bytes) is not bytes or len(self.bounded_bytes) > MAX_EGRESS_ITEM_BYTES:
            raise _invalid()


@dataclass(frozen=True, slots=True)
class LocalDisclosureOmission:
    json_pointer: str
    category: DataCategory
    reason: Literal["local_disclosure_not_authorized", "never_send_redacted"]

    def __post_init__(self) -> None:
        _text(self.json_pointer, _POINTER, maximum=256)
        _enum(self.category, DataCategory)
        if self.reason not in {"local_disclosure_not_authorized", "never_send_redacted"}:
            raise _invalid()


@dataclass(frozen=True, slots=True)
class LocalDisclosureApproved:
    privacy_proposal_id: str
    request_id: str
    sink: LocalDisclosureSink
    purpose: str
    scope: AuthorizationScope
    policy_digest: str
    case_or_projection_commitment: str
    approved_items: tuple[ApprovedLocalItem, ...]
    omissions: tuple[LocalDisclosureOmission, ...]
    receipt: LocalDisclosureReceipt

    def __post_init__(self) -> None:
        _validate_local_disclosure_result(
            privacy_proposal_id=self.privacy_proposal_id,
            request_id=self.request_id,
            sink=self.sink,
            purpose=self.purpose,
            scope=self.scope,
            policy_digest=self.policy_digest,
            case_or_projection_commitment=self.case_or_projection_commitment,
            omissions=self.omissions,
            receipt=self.receipt,
        )
        if type(self.approved_items) is not tuple or any(
            type(item) is not ApprovedLocalItem for item in self.approved_items
        ):
            raise _invalid()
        expected = tuple(sorted(self.approved_items, key=lambda item: item.json_pointer.encode()))
        if self.approved_items != expected or len({item.json_pointer for item in expected}) != len(
            expected
        ):
            raise _invalid()


@dataclass(frozen=True, slots=True)
class LocalDisclosureBlocked:
    privacy_proposal_id: str
    request_id: str
    sink: LocalDisclosureSink
    purpose: str
    scope: AuthorizationScope
    policy_digest: str
    case_or_projection_commitment: str
    omissions: tuple[LocalDisclosureOmission, ...]
    receipt: LocalDisclosureReceipt

    def __post_init__(self) -> None:
        _validate_local_disclosure_result(
            privacy_proposal_id=self.privacy_proposal_id,
            request_id=self.request_id,
            sink=self.sink,
            purpose=self.purpose,
            scope=self.scope,
            policy_digest=self.policy_digest,
            case_or_projection_commitment=self.case_or_projection_commitment,
            omissions=self.omissions,
            receipt=self.receipt,
        )
        if not self.omissions:
            raise _invalid()


@dataclass(frozen=True, slots=True)
class LocalDisclosureUnavailable:
    request_id: str
    sink: LocalDisclosureSink
    reason: Literal["audit_failed"] = "audit_failed"

    def __post_init__(self) -> None:
        validate_id(IdKind.REQUEST, self.request_id)
        _enum(self.sink, LocalDisclosureSink)
        if self.reason != "audit_failed":
            raise _invalid()


def _validate_local_disclosure_result(
    *,
    privacy_proposal_id: str,
    request_id: str,
    sink: LocalDisclosureSink,
    purpose: str,
    scope: AuthorizationScope,
    policy_digest: str,
    case_or_projection_commitment: str,
    omissions: tuple[LocalDisclosureOmission, ...],
    receipt: LocalDisclosureReceipt,
) -> None:
    validate_id(IdKind.PRIVACY_PROPOSAL, privacy_proposal_id)
    validate_id(IdKind.REQUEST, request_id)
    _enum(sink, LocalDisclosureSink)
    _text(purpose, _PURPOSE)
    if type(scope) is not AuthorizationScope:
        raise _invalid()
    validate_sha256_digest(policy_digest)
    if case_or_projection_commitment.startswith("hmac-sha256:"):
        validate_commitment(case_or_projection_commitment)
    else:
        validate_sha256_digest(case_or_projection_commitment)
    if type(omissions) is not tuple or any(
        type(omission) is not LocalDisclosureOmission for omission in omissions
    ):
        raise _invalid()
    expected = tuple(sorted(omissions, key=lambda item: item.json_pointer.encode()))
    if omissions != expected or len({item.json_pointer for item in expected}) != len(expected):
        raise _invalid()
    if (
        type(receipt) is not LocalDisclosureReceipt
        or receipt.privacy_proposal_id != privacy_proposal_id
        or receipt.request_id != request_id
        or receipt.sink is not sink
        or receipt.purpose != purpose
        or receipt.scope != scope
        or receipt.policy.policy_digest != policy_digest
    ):
        raise _invalid()
