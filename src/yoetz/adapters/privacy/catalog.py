"""Catalog-backed, provider-free privacy policy and audit persistence."""

from __future__ import annotations

import asyncio
import base64
import hmac
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import fields, is_dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Final, Literal, cast

import apsw

from yoetz.domain.privacy import (
    AgentProjectionAuditSubject,
    AuthorizationScope,
    AuthorizationScopeKind,
    ChannelPolicy,
    ConsentSource,
    DataCategory,
    DataClass,
    DisclosureProposal,
    EgressAuthorization,
    EgressChannel,
    EgressReceipt,
    ForbiddenDataKind,
    HumanPrivacyDecision,
    LocalDisclosureReceipt,
    LocalDisclosureSink,
    PreDispatchAuditDecision,
    PrivacyAuditSubject,
    PrivacyOutcome,
    PrivacyPolicy,
    PrivacyProfile,
    PrivacyReason,
    ProviderBinding,
    ReceiptCounts,
    ReceiptPolicyBinding,
    ReceiptSecretScan,
    ReceiptTransformations,
    ReviewContextProfile,
    ReviewSelectionPolicy,
)
from yoetz.domain.values import format_rfc3339_millis, parse_rfc3339_millis
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
from yoetz.protocol.canonical import (
    JsonValue,
    canonical_digest,
    canonical_encode,
    strict_json_parse,
)
from yoetz.protocol.ids import IdKind, new_id

__all__ = [
    "CatalogPrivacyAudit",
    "CatalogPrivacyPolicyStore",
    "decode_privacy_policy_canonical",
    "encode_privacy_policy_json",
]

_LOOKUP_DOMAIN = b"yoetz/privacy-audit/lookup/v1\x00"
_PROPOSAL_DOMAIN = b"yoetz/privacy-audit/proposal/v1\x00"
_CONTROL_DOMAIN = b"yoetz/privacy-audit/control-request/v1\x00"
_INTERNAL_RESULT_DOMAIN = b"yoetz/privacy-audit/internal-result/v1\x00"
_PROJECTION_DOMAIN = b"yoetz/privacy-audit/projection/v1\x00"
_APPROVAL_DOMAIN = b"yoetz/privacy-audit/local-approval/v1\x00"
_AUTHORIZATION_DOMAIN = b"yoetz/privacy-audit/authorization/v1\x00"
_CURSOR_DOMAIN = b"yoetz/privacy-audit/receipt-cursor/v1\x00"

_PRIVACY_POLICY_WIRE_SCHEMA_VERSION: Final = "1.0.0"
_WIRE_CHANNEL_ORDER: Final = (
    EgressChannel.CAPABILITY_TESTING,
    EgressChannel.CRASH_DIAGNOSTICS,
    EgressChannel.LLM_INFERENCE,
    EgressChannel.PRODUCT_TELEMETRY,
    EgressChannel.UPDATE_CHECKS,
)
_NEVER_SEND_WIRE: Final = tuple(sorted(item.value for item in ForbiddenDataKind))


def _json(value: object) -> JsonValue:
    if value is None or type(value) in {bool, int, str}:
        return cast(JsonValue, value)
    if isinstance(value, Enum):
        return value.value
    if type(value) is datetime:
        return format_rfc3339_millis(value)
    if type(value) is bytes:
        return base64.b64encode(value).decode("ascii")
    if type(value) is tuple:
        return [_json(item) for item in cast(tuple[object, ...], value)]
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _json(getattr(value, field.name)) for field in fields(value)}
    if type(value) is dict:
        return {str(key): _json(item) for key, item in cast(dict[object, object], value).items()}
    raise TypeError("privacy_catalog_canonical_value_invalid")


def _scope_json(scope: AuthorizationScope) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], _json(scope))


def _scope_digest(scope: AuthorizationScope) -> str:
    return canonical_digest(_scope_json(scope))


def _mac(key: MacKeyHandle, domain: bytes, data: bytes) -> str:
    result = key.mac(domain, data)
    if type(result) is not str:
        raise ValueError("privacy_audit_mac_invalid")
    return result


def _receipt_bytes(receipt: LocalDisclosureReceipt | EgressReceipt) -> bytes:
    return canonical_encode(_json(receipt))


def _mapping(value: JsonValue) -> dict[str, JsonValue]:
    if type(value) is not dict:
        raise ValueError("privacy_audit_row_corrupt")
    return cast(dict[str, JsonValue], value)


def _strings(value: JsonValue) -> tuple[str, ...]:
    if type(value) is not list or any(type(item) is not str for item in value):
        raise ValueError("privacy_audit_row_corrupt")
    return tuple(cast(list[str], value))


def _integer(value: JsonValue) -> int:
    if type(value) is not int:
        raise ValueError("privacy_audit_row_corrupt")
    return value


def _uint_from_json(value: JsonValue) -> int:
    """Accept catalog ints or wire decimal-string counters (``"0"``, ``"300"``)."""

    if type(value) is int:
        if value < 0:
            raise ValueError("privacy_audit_row_corrupt")
        return value
    if type(value) is str and value.isdecimal():
        if value != "0" and value.startswith("0"):
            raise ValueError("privacy_audit_row_corrupt")
        return int(value)
    raise ValueError("privacy_audit_row_corrupt")


def _enum_values(items: tuple[Enum, ...]) -> list[JsonValue]:
    encoded: list[JsonValue] = [item.value for item in items]
    return encoded


def _string_values(items: tuple[str, ...]) -> list[JsonValue]:
    encoded: list[JsonValue] = list(items)
    return encoded


def _local_receipt_from_bytes(data: bytes) -> LocalDisclosureReceipt:
    source = _mapping(strict_json_parse(data))
    scope = _mapping(source["scope"])
    policy = _mapping(source["policy"])
    counts = _mapping(source["counts"])
    transforms = _mapping(source["transformations"])
    scan = _mapping(source["secret_scan"])
    return LocalDisclosureReceipt(
        "1.0.0",
        cast(str, source["receipt_id"]),
        cast(str, source["request_id"]),
        cast(str, source["privacy_proposal_id"]),
        LocalDisclosureSink(cast(str, source["sink"])),
        PrivacyOutcome(cast(str, source["outcome"])),
        parse_rfc3339_millis(source["finished_at"]),
        AuthorizationScope(
            kind=__import__(
                "yoetz.domain.privacy", fromlist=["AuthorizationScopeKind"]
            ).AuthorizationScopeKind(cast(str, scope["kind"])),
            installation_id=cast(str, scope["installation_id"]),
            workspace_ref_commitment=cast(str | None, scope.get("workspace_ref_commitment")),
            task_id=cast(str | None, scope.get("task_id")),
            request_id=cast(str | None, scope.get("request_id")),
        ),
        cast(str, source["purpose"]),
        ReceiptPolicyBinding(
            cast(str, policy["policy_id"]),
            _integer(policy["version"]),
            cast(str, policy["policy_digest"]),
            cast(str, policy["authorization_scope_digest"]),
        ),
        ConsentSource(cast(str, source["consent_source"])),
        tuple(DataCategory(value) for value in _strings(source["approved_categories"])),
        tuple(DataCategory(value) for value in _strings(source["blocked_categories"])),
        ReceiptCounts(
            _integer(counts["candidate_items"]),
            _integer(counts["included_items"]),
            _integer(counts["removed_items"]),
            _integer(counts["approved_items"]),
            _integer(counts["blocked_items"]),
            _integer(counts["candidate_bytes"]),
            _integer(counts["final_bytes"]),
            cast(int | None, counts["estimated_input_tokens"]),
            cast(int | None, counts["request_body_bytes"]),
        ),
        ReceiptTransformations(
            _integer(transforms["minimized_items"]),
            _integer(transforms["redacted_spans"]),
            _integer(transforms["blocked_items"]),
        ),
        ReceiptSecretScan(
            cast(str, scan["registry_version"]),
            cast(str, scan["scanner_profile_digest"]),
            _integer(scan["match_count"]),
            cast(bool, scan["passed"]),
        ),
        None
        if source["safe_failure_reason"] is None
        else PrivacyReason(cast(str, source["safe_failure_reason"])),
        1,
    )


def _scope_from_json(value: JsonValue) -> AuthorizationScope:
    source = _mapping(value)
    return AuthorizationScope(
        AuthorizationScopeKind(cast(str, source["kind"])),
        cast(str, source["installation_id"]),
        cast(str | None, source.get("workspace_ref_commitment")),
        cast(str | None, source.get("task_id")),
        cast(str | None, source.get("request_id")),
    )


def _binding_from_json(value: JsonValue) -> ProviderBinding | None:
    if value is None:
        return None
    source = _mapping(value)
    return ProviderBinding(
        cast(str, source["provider_id"]),
        cast(str, source["model_id"]),
        cast(str, source["endpoint_profile_id"]),
        cast(str, source["endpoint_profile_version"]),
        cast(Literal["external", "local_af_unix"], source["transport"]),
    )


def _review_from_json(value: JsonValue) -> ReviewSelectionPolicy:
    source = _mapping(value)
    return ReviewSelectionPolicy(
        _strings(source["sections"]),
        _strings(source["excerpt_kinds"]),
        cast(Literal["linked_subjects_only", "linked_then_in_scope"], source["relevance"]),
        cast(bool, source["include_finding_prose"]),
        cast(bool, source["include_exact_command_text"]),
        _integer(source["max_timeline_items"]),
        _integer(source["max_assessments"]),
        _integer(source["max_change_observations"]),
        _integer(source["max_excerpts"]),
        _integer(source["max_omissions"]),
        _integer(source["max_excerpt_bytes"]),
        _integer(source["max_total_excerpt_bytes"]),
    )


def _channel_from_json(value: JsonValue) -> ChannelPolicy:
    source = _mapping(value)
    return ChannelPolicy(
        EgressChannel(cast(str, source["channel"])),
        cast(bool, source["enabled"]),
        tuple(DataCategory(item) for item in _strings(source["allowed_categories"])),
        tuple(DataClass(item) for item in _strings(source["allowed_data_classes"])),
        _binding_from_json(source.get("provider_binding")),
        _strings(source["allowed_purposes"]),
        AuthorizationScopeKind(cast(str, source["scope_ceiling"])),
        cast(bool, source["preview_required"]),
        _uint_from_json(source["max_bytes"]),
        _uint_from_json(source["max_tokens"]),
        _uint_from_json(source["authorization_ttl_seconds"]),
    )


def _ceiling_from_wire(value: JsonValue) -> tuple[tuple[DataCategory, ...], tuple[DataClass, ...]]:
    source = _mapping(value)
    return (
        tuple(DataCategory(item) for item in _strings(source["categories"])),
        tuple(DataClass(item) for item in _strings(source["data_classes"])),
    )


def _ceiling_to_wire(
    categories: tuple[DataCategory, ...], data_classes: tuple[DataClass, ...]
) -> dict[str, JsonValue]:
    return {
        "categories": _enum_values(categories),
        "data_classes": _enum_values(data_classes),
    }


def _channel_to_wire(channel: ChannelPolicy) -> dict[str, JsonValue]:
    body: dict[str, JsonValue] = {
        "channel": channel.channel.value,
        "enabled": channel.enabled,
        "allowed_categories": _enum_values(channel.allowed_categories),
        "allowed_data_classes": _enum_values(channel.allowed_data_classes),
        "allowed_purposes": _string_values(channel.allowed_purposes),
        "scope_ceiling": channel.scope_ceiling.value,
        "preview_required": channel.preview_required,
        "max_bytes": str(channel.max_bytes),
        "max_tokens": str(channel.max_tokens),
        "authorization_ttl_seconds": str(channel.authorization_ttl_seconds),
    }
    if channel.provider_binding is not None:
        body["provider_binding"] = _json(channel.provider_binding)
    return body


def _policy_from_domain_mapping(source: dict[str, JsonValue]) -> PrivacyPolicy:
    channels = source["channel_policies"]
    if type(channels) is not list:
        raise ValueError("privacy_policy_row_corrupt")
    return PrivacyPolicy(
        cast(str, source["policy_id"]),
        _uint_from_json(source["version"]),
        cast(str, source["policy_digest"]),
        PrivacyProfile(cast(str, source["profile"])),
        ReviewContextProfile(cast(str, source["review_context_profile"])),
        _review_from_json(source["review_selection"]),
        cast(bool, source["require_current_provider_data_use_evidence"]),
        cast(bool, source["network_egress_permitted"]),
        _scope_from_json(source["effective_scope"]),
        tuple(_channel_from_json(item) for item in channels),
        cast(bool, source["local_model_enabled"]),
        _binding_from_json(source.get("local_model_binding")),
        tuple(DataCategory(item) for item in _strings(source["local_model_categories"])),
        tuple(DataClass(item) for item in _strings(source["local_model_data_classes"])),
        tuple(DataCategory(item) for item in _strings(source["agent_context_categories"])),
        tuple(DataClass(item) for item in _strings(source["agent_context_data_classes"])),
        tuple(DataCategory(item) for item in _strings(source["trusted_human_control_categories"])),
        tuple(DataClass(item) for item in _strings(source["trusted_human_control_data_classes"])),
        parse_rfc3339_millis(source["created_at"]),
        cast(str | None, source.get("supersedes_policy_digest")),
    )


def _policy_from_wire_mapping(source: dict[str, JsonValue]) -> PrivacyPolicy:
    # The frozen schema makes both of these required, and never_send is a const deny list.
    # Treating either as optional would decode an incomplete or future document as a valid
    # 1.0.0 policy, which on this boundary means silently accepting a weaker deny list.
    if source.get("schema_version") != _PRIVACY_POLICY_WIRE_SCHEMA_VERSION:
        raise ValueError("privacy_policy_row_corrupt")
    channels = source["channel_policies"]
    if type(channels) is not list:
        raise ValueError("privacy_policy_row_corrupt")
    never_send = source.get("never_send")
    if never_send is None or tuple(_strings(never_send)) != _NEVER_SEND_WIRE:
        raise ValueError("privacy_policy_row_corrupt")
    ceilings = _mapping(source["local_sink_category_ceilings"])
    local_categories, local_classes = _ceiling_from_wire(ceilings["local_model"])
    agent_categories, agent_classes = _ceiling_from_wire(ceilings["agent_context"])
    trusted_categories, trusted_classes = _ceiling_from_wire(ceilings["trusted_human_control"])
    return PrivacyPolicy(
        cast(str, source["policy_id"]),
        _uint_from_json(source["version"]),
        cast(str, source["policy_digest"]),
        PrivacyProfile(cast(str, source["profile"])),
        ReviewContextProfile(cast(str, source["review_context_profile"])),
        _review_from_json(source["review_selection"]),
        cast(bool, source["require_current_provider_data_use_evidence"]),
        cast(bool, source["network_egress_permitted"]),
        _scope_from_json(source["effective_scope"]),
        tuple(_channel_from_json(item) for item in channels),
        cast(bool, source["local_model_enabled"]),
        _binding_from_json(source.get("local_model_binding")),
        local_categories,
        local_classes,
        agent_categories,
        agent_classes,
        trusted_categories,
        trusted_classes,
        parse_rfc3339_millis(source["created_at"]),
        cast(str | None, source.get("supersedes_policy_digest")),
    )


def _policy_from_bytes(data: bytes) -> PrivacyPolicy:
    source = _mapping(strict_json_parse(data))
    if "local_sink_category_ceilings" in source:
        return _policy_from_wire_mapping(source)
    return _policy_from_domain_mapping(source)


def decode_privacy_policy_canonical(data: bytes) -> PrivacyPolicy:
    """Decode a canonical privacy-policy JSON document (desired-state or wire control)."""

    return _policy_from_bytes(data)


def encode_privacy_policy_json(policy: PrivacyPolicy) -> dict[str, JsonValue]:
    """Encode a privacy policy as the wire ``privacy-policy-1.0.0`` JSON object.

    Catalog rows stay domain-shaped; ordinary-control / CLI results must match the frozen
    schema (``local_sink_category_ceilings``, const ``never_send``, decimal counters).
    Omits JSON nulls so ``additionalProperties: false`` oneOf arms validate.
    """

    by_channel = {channel.channel: channel for channel in policy.channel_policies}
    if set(by_channel) != set(EgressChannel):
        raise ValueError("privacy_policy_channel_set_invalid")
    encoded: dict[str, JsonValue] = {
        "schema_version": _PRIVACY_POLICY_WIRE_SCHEMA_VERSION,
        "policy_id": policy.policy_id,
        "version": str(policy.version),
        "policy_digest": policy.policy_digest,
        "profile": policy.profile.value,
        "review_context_profile": policy.review_context_profile.value,
        "review_selection": _json(policy.review_selection),
        "require_current_provider_data_use_evidence": (
            policy.require_current_provider_data_use_evidence
        ),
        "network_egress_permitted": policy.network_egress_permitted,
        "effective_scope": _omit_json_nulls(_json(policy.effective_scope)),
        "channel_policies": [
            _channel_to_wire(by_channel[channel]) for channel in _WIRE_CHANNEL_ORDER
        ],
        "local_model_enabled": policy.local_model_enabled,
        "local_sink_category_ceilings": {
            "local_model": _ceiling_to_wire(
                policy.local_model_categories, policy.local_model_data_classes
            ),
            "agent_context": _ceiling_to_wire(
                policy.agent_context_categories, policy.agent_context_data_classes
            ),
            "trusted_human_control": _ceiling_to_wire(
                policy.trusted_human_control_categories,
                policy.trusted_human_control_data_classes,
            ),
        },
        "never_send": _string_values(_NEVER_SEND_WIRE),
        "created_at": format_rfc3339_millis(policy.created_at),
    }
    if policy.local_model_binding is not None:
        encoded["local_model_binding"] = _json(policy.local_model_binding)
    if policy.supersedes_policy_digest is not None:
        encoded["supersedes_policy_digest"] = policy.supersedes_policy_digest
    return cast(dict[str, JsonValue], _omit_json_nulls(encoded))


def _omit_json_nulls(value: JsonValue) -> JsonValue:
    if type(value) is dict:
        return {
            str(key): _omit_json_nulls(item)
            for key, item in cast(dict[str, JsonValue], value).items()
            if item is not None
        }
    if type(value) is list:
        return [_omit_json_nulls(item) for item in cast(list[JsonValue], value)]
    return value


@contextmanager
def _transaction(db: apsw.Connection) -> Generator[None]:
    db.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        db.execute("ROLLBACK")
        raise
    else:
        db.execute("COMMIT")


class CatalogPrivacyPolicyStore:
    """Generation-CAS catalog policy store with canonical private row codecs."""

    __slots__ = ("_clock", "_db", "_lock")

    def __init__(self, db: apsw.Connection, clock: ClockPort) -> None:
        self._db = db
        self._clock = clock
        self._lock = asyncio.Lock()

    async def seed_if_absent(self, policy: PrivacyPolicy) -> PrivacyPolicy:
        scope_digest = _scope_digest(policy.effective_scope)
        canonical = canonical_encode(_json(policy))
        async with self._lock:
            with _transaction(self._db):
                row = self._db.execute(
                    "SELECT policy_canonical FROM privacy_policy_versions WHERE scope_digest = ? AND state = 'current'",
                    (scope_digest,),
                ).fetchone()
                if row is not None:
                    existing = _policy_from_bytes(cast(bytes, row[0]))
                    if existing != policy:
                        raise ValueError("privacy_policy_seed_conflict")
                    return existing
                generation = self._next_generation()
                self._insert_policy(policy, generation, "seed", None, canonical)
        return policy

    async def effective_policy(self, scope: AuthorizationScope) -> EffectivePrivacyPolicy:
        rows = self._db.execute(
            "SELECT policy_canonical, policy_generation FROM privacy_policy_versions WHERE state = 'current'"
        ).fetchall()
        eligible: list[tuple[PrivacyPolicy, int]] = []
        for canonical, generation in rows:
            policy = _policy_from_bytes(cast(bytes, canonical))
            if policy.effective_scope.contains(scope):
                eligible.append((policy, cast(int, generation)))
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
            key=lambda item: (rank[item[0].effective_scope.kind], item[1]),
        )
        composed = ordered[0][0]
        for policy, _generation in ordered[1:]:
            composed = composed.meet(policy)
        # Generation stays the most-specific eligible row's own generation, because it is the
        # CAS token prepare_transition/commit_transition compare against _current_exact(scope).
        # Reporting a composed maximum here would make every transition at that scope fail
        # privacy_policy_stale whenever an ancestor row carried a higher generation. Staleness
        # against ancestor movement is still caught: effective_digest below is the meet digest,
        # and it is the other half of the same precondition.
        generation = ordered[-1][1]
        return EffectivePrivacyPolicy(composed, generation, composed.policy_digest)

    async def prepare_transition(
        self, proposal: PolicyTransitionProposal
    ) -> PreparedPolicyTransition:
        if proposal.privacy_proposal_id is None or proposal.expected_policy_digest is None:
            raise ValueError("privacy_policy_proposal_identity_missing")
        current = await self.effective_policy(proposal.scope)
        if (
            current.generation != proposal.expected_generation
            or current.effective_digest != proposal.expected_policy_digest
        ):
            raise ValueError("privacy_policy_stale")
        candidate = canonical_encode(_json(proposal.proposed_policy))
        diff = canonical_encode(
            {
                "base_policy_digest": current.effective_digest,
                "candidate_policy_digest": proposal.proposed_policy.policy_digest,
            }
        )
        exact_diff_digest = canonical_digest(strict_json_parse(diff))
        prepared_digest = canonical_digest(
            {
                "diff_digest": exact_diff_digest,
                "proposal_digest": proposal.proposal_digest,
                "proposal_id": proposal.privacy_proposal_id,
            }
        )
        now = self._clock.now_utc()
        async with self._lock:
            with _transaction(self._db):
                self._db.execute(
                    """INSERT INTO privacy_policy_transitions (
                           proposal_id, scope_digest, base_policy_id, base_policy_version,
                           base_policy_generation, proposal_digest, candidate_policy_digest,
                           candidate_policy_canonical, diff_canonical, state, expires_at,
                           created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
                    (
                        proposal.privacy_proposal_id,
                        _scope_digest(proposal.scope),
                        current.policy.policy_id,
                        current.policy.version,
                        current.generation,
                        proposal.proposal_digest,
                        proposal.proposed_policy.policy_digest,
                        candidate,
                        diff,
                        format_rfc3339_millis(proposal.expires_at),
                        format_rfc3339_millis(now),
                        format_rfc3339_millis(now),
                    ),
                )
        return PreparedPolicyTransition(proposal, prepared_digest, exact_diff_digest, True)

    async def load_pending_transition(self, proposal_id: str) -> PreparedPolicyTransition:
        row = self._db.execute(
            """SELECT base_policy_generation, proposal_digest, candidate_policy_canonical,
                      diff_canonical, expires_at, created_at
               FROM privacy_policy_transitions
               WHERE proposal_id = ? AND state = 'pending'""",
            (proposal_id,),
        ).fetchone()
        if row is None:
            raise ValueError("privacy_policy_transition_unavailable")
        candidate = _policy_from_bytes(cast(bytes, row[2]))
        # Rebuild the identity this proposal was prepared with. Deriving the base digest from
        # the *current* policy instead would silently re-key prepared_digest whenever the
        # effective policy moves, so the digest a human previewed would not be the digest the
        # commit is authorised against.
        diff = cast(bytes, row[3])
        base_policy_digest = cast(str, _mapping(strict_json_parse(diff))["base_policy_digest"])
        proposal = PolicyTransitionProposal(
            scope=candidate.effective_scope,
            expected_generation=cast(int, row[0]),
            proposed_policy=candidate,
            proposal_digest=cast(str, row[1]),
            created_at=parse_rfc3339_millis(row[5]),
            expires_at=parse_rfc3339_millis(row[4]),
            privacy_proposal_id=proposal_id,
            expected_policy_digest=base_policy_digest,
        )
        exact_diff_digest = canonical_digest(strict_json_parse(diff))
        prepared_digest = canonical_digest(
            {
                "diff_digest": exact_diff_digest,
                "proposal_digest": proposal.proposal_digest,
                "proposal_id": proposal_id,
            }
        )
        return PreparedPolicyTransition(proposal, prepared_digest, exact_diff_digest, True)

    async def commit_transition(
        self, prepared: PreparedPolicyTransition, decision: HumanPolicyDecision
    ) -> PolicyCommitResult:
        proposal = prepared.proposal
        proposal_id = proposal.privacy_proposal_id
        if proposal_id is None or decision.prepared_digest != prepared.prepared_digest:
            raise ValueError("privacy_policy_decision_mismatch")
        now = self._clock.now_utc()
        async with self._lock:
            with _transaction(self._db):
                row = self._db.execute(
                    "SELECT state, base_policy_generation, expires_at, candidate_policy_canonical FROM privacy_policy_transitions WHERE proposal_id = ?",
                    (proposal_id,),
                ).fetchone()
                if row is None or row[0] != "pending":
                    raise ValueError("privacy_policy_transition_unavailable")
                if cast(int, row[1]) != proposal.expected_generation:
                    raise ValueError("privacy_policy_stale")
                if parse_rfc3339_millis(row[2]) <= decision.decided_at:
                    self._db.execute(
                        "UPDATE privacy_policy_transitions SET state='expired', terminal_at=?, updated_at=? WHERE proposal_id=?",
                        (format_rfc3339_millis(now), format_rfc3339_millis(now), proposal_id),
                    )
                    raise ValueError("privacy_policy_decision_expired")
                if not decision.approved:
                    self._db.execute(
                        """UPDATE privacy_policy_transitions SET state='denied',
                               human_decision='denied', decision_digest=?, terminal_at=?, updated_at=?
                           WHERE proposal_id=?""",
                        (
                            prepared.prepared_digest,
                            format_rfc3339_millis(now),
                            format_rfc3339_millis(now),
                            proposal_id,
                        ),
                    )
                    current = self._current_exact(proposal.scope)
                    return PolicyCommitResult(current.policy, current.generation, 0, 0)
                policy = _policy_from_bytes(cast(bytes, row[3]))
                current = self._current_exact(proposal.scope)
                if current.generation != proposal.expected_generation:
                    raise ValueError("privacy_policy_stale")
                generation = self._next_generation()
                self._supersede(current.policy, now)
                self._insert_policy(policy, generation, "human_expansion", proposal_id)
                self._db.execute(
                    """UPDATE privacy_policy_transitions SET state='committed',
                           human_decision='approved', decision_digest=?, authority_commitment=?,
                           committed_policy_id=?, committed_policy_version=?, terminal_at=?, updated_at=?
                       WHERE proposal_id=?""",
                    (
                        prepared.prepared_digest,
                        decision.authority_commitment,
                        policy.policy_id,
                        policy.version,
                        format_rfc3339_millis(now),
                        format_rfc3339_millis(now),
                        proposal_id,
                    ),
                )
        return PolicyCommitResult(policy, generation, 0, 0)

    async def tighten(
        self,
        scope: AuthorizationScope,
        overlay: PolicyOverlay,
        expected_policy_digest: str,
    ) -> PolicyCommitResult:
        if overlay.scope != scope:
            raise ValueError("privacy_policy_scope_mismatch")
        candidate = overlay.candidate_policy
        now = self._clock.now_utc()
        async with self._lock:
            with _transaction(self._db):
                current = self._current_exact(scope)
                if current.effective_digest != expected_policy_digest:
                    raise ValueError("privacy_policy_stale")
                generation = self._next_generation()
                self._supersede(current.policy, now)
                self._insert_policy(candidate, generation, "tightening", None)
        return PolicyCommitResult(candidate, generation, 0, 0)

    async def reseed_untouched_bootstrap_default(
        self,
        scope: AuthorizationScope,
        *,
        expected_current: PrivacyPolicy,
        replacement: PrivacyPolicy,
    ) -> PrivacyPolicy:
        """Re-seed the shipped bootstrap default when the stored policy is still exactly it.

        An installation created before the shipped default changed would otherwise keep the older
        allowlist forever, so the same release behaves differently for new and existing users.

        Two independent conditions gate the swap, because policy contents alone cannot prove
        origin: the stored row must still carry first-run bootstrap provenance (`change_kind`
        `seed` with no source proposal), and its decoded policy must equal ``expected_current``
        exactly. A tightening or an approved expansion records a different `change_kind`, so an
        owner choice that happens to reproduce the old default's fields is never overwritten.
        """

        now = self._clock.now_utc()
        async with self._lock:
            with _transaction(self._db):
                current = self._current_exact(scope)
                if current.policy != expected_current:
                    return current.policy
                if not self._is_untouched_bootstrap_seed(scope):
                    return current.policy
                generation = self._next_generation()
                self._supersede(current.policy, now)
                self._insert_policy(replacement, generation, "seed", None)
        return replacement

    async def watch_generation(self) -> int:
        row = self._db.execute(
            "SELECT COALESCE(MAX(policy_generation), 0) FROM privacy_policy_versions"
        ).fetchone()
        if row is None or type(row[0]) is not int:
            raise ValueError("privacy_policy_generation_corrupt")
        return cast(int, row[0])

    def _current_exact(self, scope: AuthorizationScope) -> EffectivePrivacyPolicy:
        row = self._db.execute(
            """SELECT policy_canonical, policy_generation FROM privacy_policy_versions
               WHERE scope_digest = ? AND state = 'current'""",
            (_scope_digest(scope),),
        ).fetchone()
        if row is None:
            raise ValueError("privacy_policy_missing")
        policy = _policy_from_bytes(cast(bytes, row[0]))
        return EffectivePrivacyPolicy(policy, cast(int, row[1]), policy.policy_digest)

    def _is_untouched_bootstrap_seed(self, scope: AuthorizationScope) -> bool:
        """Report whether the current row for the scope is still the first-run bootstrap seed.

        Only `seed_if_absent` and this re-seed write `change_kind='seed'`, and neither carries a
        source proposal, so the pair is an immutable provenance marker that no owner-driven
        transition can reproduce.
        """

        row = self._db.execute(
            """SELECT change_kind, source_proposal_id FROM privacy_policy_versions
               WHERE scope_digest = ? AND state = 'current'""",
            (_scope_digest(scope),),
        ).fetchone()
        if row is None:
            return False
        return row[0] == "seed" and row[1] is None

    def _next_generation(self) -> int:
        row = self._db.execute(
            "SELECT COALESCE(MAX(policy_generation), 0) + 1 FROM privacy_policy_versions"
        ).fetchone()
        if row is None or type(row[0]) is not int:
            raise ValueError("privacy_policy_generation_corrupt")
        return cast(int, row[0])

    def _supersede(self, policy: PrivacyPolicy, now: datetime) -> None:
        changed = (
            self._db.execute(
                """UPDATE privacy_policy_versions SET state='superseded', superseded_at=?
               WHERE policy_id=? AND policy_version=? AND state='current'""",
                (format_rfc3339_millis(now), policy.policy_id, policy.version),
            )
            .getconnection()
            .changes()
        )
        if changed != 1:
            raise ValueError("privacy_policy_stale")

    def _insert_policy(
        self,
        policy: PrivacyPolicy,
        generation: int,
        change_kind: str,
        source_proposal_id: str | None,
        canonical: bytes | None = None,
    ) -> None:
        scope = policy.effective_scope
        self._db.execute(
            """INSERT INTO privacy_policy_versions (
                   policy_id, policy_version, scope_digest, scope_kind, installation_id,
                   workspace_ref_commitment, task_id, request_id, policy_digest,
                   policy_canonical, policy_generation, change_kind, source_proposal_id,
                   state, created_at, superseded_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'current', ?, NULL)""",
            (
                policy.policy_id,
                policy.version,
                _scope_digest(scope),
                scope.kind.value,
                scope.installation_id,
                scope.workspace_ref_commitment,
                scope.task_id,
                scope.request_id,
                policy.policy_digest,
                canonical if canonical is not None else canonical_encode(_json(policy)),
                generation,
                change_kind,
                source_proposal_id,
                format_rfc3339_millis(policy.created_at),
            ),
        )


class CatalogPrivacyAudit:
    """Durable privacy audit with objectless atomic client projections."""

    __slots__ = ("_clock", "_db", "_key", "_lock", "_objects", "_service_generation")

    def __init__(
        self,
        db: apsw.Connection,
        objects: ObjectStorePort,
        audit_key: MacKeyHandle,
        clock: ClockPort,
        *,
        service_generation: int = 1,
    ) -> None:
        if type(service_generation) is not int or service_generation <= 0:
            raise ValueError("privacy_service_generation_invalid")
        self._db = db
        self._objects = objects
        self._key = audit_key
        self._clock = clock
        self._service_generation = service_generation
        self._lock = asyncio.Lock()

    async def complete_agent_projection(
        self, request: AgentProjectionRequest, receipt: LocalDisclosureReceipt
    ) -> CompletedAgentProjection:
        if receipt.privacy_proposal_id != request.privacy_proposal_id:
            raise ValueError("privacy_projection_receipt_mismatch")
        control_commitment = _mac(self._key, _CONTROL_DOMAIN, request.control_request_canonical)
        internal_commitment = _mac(
            self._key, _INTERNAL_RESULT_DOMAIN, request.internal_result_canonical
        )
        projection_commitment = _mac(self._key, _PROJECTION_DOMAIN, request.projection_canonical)
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
            internal_commitment,
            projection_commitment,
            request.field_decisions,
            request.candidate_count,
            request.approved_count,
            request.omitted_count,
            request.finished_at,
        )
        subject_bytes = canonical_encode(_json(subject))
        lookup = _mac(self._key, _LOOKUP_DOMAIN, subject_bytes)
        receipt_canonical = _receipt_bytes(receipt)
        receipt_digest = canonical_digest(strict_json_parse(receipt_canonical))
        now = self._clock.now_utc()
        async with self._lock:
            with _transaction(self._db):
                existing = self._db.execute(
                    """SELECT request_id, subject_lookup_identity, state,
                              subject_structural_canonical, receipt_canonical,
                              control_request_commitment
                       FROM privacy_audit_records WHERE proposal_id = ?""",
                    (request.privacy_proposal_id,),
                ).fetchone()
                if existing is not None:
                    if existing != (
                        request.projection_request_id,
                        lookup,
                        "local_disclosure_completed",
                        subject_bytes,
                        receipt_canonical,
                        control_commitment,
                    ):
                        raise ValueError("privacy_projection_replay_conflict")
                else:
                    self._db.execute(
                        """
                    INSERT INTO privacy_audit_records (
                        proposal_id, request_id, originating_workflow_request_id,
                        control_rpc_id, control_method, service_instance_id, service_generation,
                        control_request_commitment, subject_lookup_identity, subject_kind,
                        destination_kind, local_sink, purpose, scope_kind, scope_digest,
                        policy_id, policy_version, policy_digest, subject_structural_canonical,
                        task_id, route_identity_digest, state, consent_source,
                        approval_binding_commitment, consumed_at,
                        attempt_result_structural_canonical, attempt_result_commitment,
                        receipt_id, receipt_outcome, receipt_reason, receipt_canonical,
                        receipt_digest, receipt_finished_at, expires_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'agent_projection', 'local', ?,
                        'client_result_projection', ?, ?, ?, ?, ?, ?, ?, ?,
                        'local_disclosure_completed', 'baseline_policy', ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, NULL, ?, ?)
                    """,
                        (
                            request.privacy_proposal_id,
                            request.projection_request_id,
                            request.original_request_id,
                            request.rpc_id,
                            request.method,
                            request.service_instance_id,
                            request.service_generation,
                            control_commitment,
                            lookup,
                            request.sink.value,
                            request.scope.kind.value,
                            _scope_digest(request.scope),
                            request.policy_id,
                            request.policy_version,
                            request.policy_digest,
                            subject_bytes,
                            request.task_id,
                            request.route_identity_digest,
                            projection_commitment,
                            format_rfc3339_millis(now),
                            receipt_canonical,
                            receipt_digest,
                            receipt.receipt_id,
                            receipt.outcome.value,
                            None
                            if receipt.safe_failure_reason is None
                            else receipt.safe_failure_reason.value,
                            receipt_canonical,
                            receipt_digest,
                            format_rfc3339_millis(receipt.finished_at),
                            format_rfc3339_millis(now),
                            format_rfc3339_millis(now),
                        ),
                    )
        reservation = PrivacyAuditReservation(
            request.privacy_proposal_id,
            request.projection_request_id,
            projection_commitment,
            "local_disclosure_completed",
            request.policy_generation,
            now,
        )
        return CompletedAgentProjection(subject, reservation)

    async def prepare_disclosure_proposal(
        self, request: DisclosureProposalRequest
    ) -> PreparedDisclosureReservation:
        now = self._clock.now_utc()
        proposal_value = {
            "approved_categories": [item.value for item in request.minimized.approved_categories],
            "blocked_categories": [item.value for item in request.minimized.blocked_categories],
            "expires_at": format_rfc3339_millis(request.expires_at),
            "policy_digest": request.policy_digest,
            "prepared_bytes_base64": base64.b64encode(request.minimized.prepared_bytes).decode(
                "ascii"
            ),
            "prepared_case_digest": request.minimized.case_digest,
            "privacy_proposal_id": request.privacy_proposal_id,
            "purpose": request.purpose,
            "request_id": request.request_id,
            "schema": "yoetz.disclosure-proposal/1",
            "scope": _scope_json(request.scope),
            "source_item_digests": list(request.minimized.source_item_digests),
            "transformation_summary": [
                list(item) for item in request.minimized.transformation_summary
            ],
        }
        proposal_bytes = canonical_encode(cast(JsonValue, proposal_value))
        proposal_commitment = _mac(self._key, _PROPOSAL_DOMAIN, proposal_bytes)
        metadata = ObjectMetadata(
            ObjectKind.PRIVACY_AUDIT,
            "application/vnd.yoetz.privacy-disclosure+json",
            request.task_id,
            now,
        )
        staged = await self._objects.stage(ObjectSource(data=proposal_bytes), metadata)
        ref = await self._objects.finalize(staged)
        route_row = self._db.execute(
            "SELECT active_route_identity_digest FROM task_routes WHERE task_id = ? AND state = 'active'",
            (request.task_id,),
        ).fetchone()
        if route_row is None or type(route_row[0]) is not str:
            raise ValueError("privacy_audit_route_unavailable")
        route_identity = cast(str, route_row[0])
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
            proposal_commitment,
        )
        structural = canonical_encode(
            {
                "max_bytes": request.max_bytes,
                "max_tokens": request.max_tokens,
                "policy_digest": request.policy_digest,
                "prepared_case_digest": request.minimized.case_digest,
                "proposal_commitment": proposal_commitment,
                "request_id": request.request_id,
                "scope": _scope_json(request.scope),
            }
        )
        lookup = _mac(self._key, _LOOKUP_DOMAIN, structural)
        async with self._lock:
            with _transaction(self._db):
                self._db.execute(
                    """
                    INSERT INTO privacy_audit_records (
                        proposal_id, request_id, subject_lookup_identity, subject_kind,
                        destination_kind, channel, local_sink, provider_id, model_id,
                        endpoint_profile_id, endpoint_profile_version, purpose, scope_kind,
                        scope_digest, policy_id, policy_version, policy_digest,
                        subject_structural_canonical, task_id, route_identity_digest,
                        content_object_id, content_object_kind, content_plaintext_size,
                        content_commitment, content_envelope_digest, content_encryption_format,
                        content_key_slot, content_media_type, content_created_at,
                        state, expires_at, created_at, updated_at
                    ) VALUES (?, ?, ?, 'disclosure', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, 'privacy_audit', ?, ?, ?, ?, ?, ?, ?, 'reserved', ?, ?, ?)
                    """,
                    (
                        request.privacy_proposal_id,
                        request.request_id,
                        lookup,
                        "network" if request.provider_binding is not None else "local",
                        None if request.provider_binding is None else "llm_inference",
                        None if request.local_sink is None else request.local_sink.value,
                        None
                        if request.provider_binding is None
                        else request.provider_binding.provider_id,
                        None
                        if request.provider_binding is None
                        else request.provider_binding.model_id,
                        None
                        if request.provider_binding is None
                        else request.provider_binding.endpoint_profile_id,
                        None
                        if request.provider_binding is None
                        else request.provider_binding.endpoint_profile_version,
                        request.purpose,
                        request.scope.kind.value,
                        _scope_digest(request.scope),
                        request.policy_id,
                        request.policy_version,
                        request.policy_digest,
                        structural,
                        request.task_id,
                        route_identity,
                        ref.object_id,
                        ref.plaintext_size,
                        ref.commitment,
                        ref.envelope_digest,
                        ref.encryption_format,
                        ref.key_slot,
                        ref.metadata.media_type,
                        format_rfc3339_millis(ref.metadata.created_at),
                        format_rfc3339_millis(request.expires_at),
                        format_rfc3339_millis(now),
                        format_rfc3339_millis(now),
                    ),
                )
                self._refresh_roots(request.task_id, route_identity, now)
        reservation = PrivacyAuditReservation(
            request.privacy_proposal_id,
            request.request_id,
            lookup,
            "reserved",
            request.policy_generation,
            now,
        )
        return PreparedDisclosureReservation(proposal, reservation)

    async def reserve(self, subject: PrivacyAuditSubject) -> PrivacyAuditReservation:
        if type(subject) is not PreDispatchAuditDecision:
            raise ValueError("privacy_audit_subject_requires_typed_prepare")
        canonical = canonical_encode(_json(subject))
        lookup = _mac(self._key, _LOOKUP_DOMAIN, canonical)
        now = self._clock.now_utc()
        generation = self._policy_generation(subject.policy_digest)
        async with self._lock:
            with _transaction(self._db):
                self._db.execute(
                    """
                    INSERT INTO privacy_audit_records (
                        proposal_id, request_id, subject_lookup_identity, subject_kind,
                        destination_kind, channel, local_sink, purpose, scope_kind, scope_digest,
                        policy_id, policy_version, policy_digest, subject_structural_canonical,
                        task_id, state, expires_at, created_at, updated_at
                    ) VALUES (?, ?, ?, 'pre_dispatch', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        'decision_receipt_pending', NULL, ?, ?)
                    """,
                    (
                        subject.privacy_proposal_id,
                        subject.request_id,
                        lookup,
                        "network" if subject.channel is not None else "local",
                        None if subject.channel is None else subject.channel.value,
                        None if subject.local_sink is None else subject.local_sink.value,
                        subject.purpose,
                        subject.scope.kind.value,
                        _scope_digest(subject.scope),
                        subject.policy_id,
                        subject.policy_version,
                        subject.policy_digest,
                        canonical,
                        subject.scope.task_id,
                        format_rfc3339_millis(now),
                        format_rfc3339_millis(now),
                    ),
                )
        return PrivacyAuditReservation(
            subject.privacy_proposal_id,
            subject.request_id,
            lookup,
            "decision_receipt_pending",
            generation,
            now,
        )

    async def load(self, request_id: str, subject_digest: str) -> PrivacyAuditState | None:
        row = self._db.execute(
            """SELECT proposal_id, state, policy_digest, created_at, authorization_id,
                      dispatch_id, receipt_id
               FROM privacy_audit_records
               WHERE request_id = ? AND subject_lookup_identity = ?""",
            (request_id, subject_digest),
        ).fetchone()
        if row is None:
            return None
        reservation = PrivacyAuditReservation(
            cast(str, row[0]),
            request_id,
            subject_digest,
            cast(str, row[1]),
            self._policy_generation(cast(str, row[2])),
            parse_rfc3339_millis(row[3]),
        )
        return PrivacyAuditState(
            reservation,
            cast(str, row[1]),
            cast(str | None, row[4]),
            cast(str | None, row[5]),
            cast(str | None, row[6]),
        )

    async def load_disclosure_proposal(self, proposal_id: str) -> DisclosureProposal | None:
        row = self._db.execute(
            """SELECT content_object_id, content_plaintext_size, content_commitment,
                      content_envelope_digest, content_encryption_format, content_key_slot,
                      content_media_type, content_created_at, task_id,
                      provider_id, model_id, endpoint_profile_id, endpoint_profile_version,
                      local_sink, destination_kind, policy_version,
                      subject_structural_canonical, expires_at
               FROM privacy_audit_records
               WHERE proposal_id = ? AND subject_kind = 'disclosure'""",
            (proposal_id,),
        ).fetchone()
        if row is None or type(row[0]) is not str or type(row[8]) is not str:
            return None
        structural = _mapping(strict_json_parse(cast(bytes, row[16])))
        task = cast(str, row[8])
        ref = ObjectRef(
            cast(str, row[0]),
            cast(int, row[1]),
            cast(str, row[2]),
            cast(str, row[3]),
            cast(Literal["yoetz-object/1"], row[4]),
            cast(str, row[5]),
            ObjectMetadata(
                ObjectKind.PRIVACY_AUDIT,
                cast(str, row[6]),
                task,
                parse_rfc3339_millis(row[7]),
            ),
        )
        try:
            body = b"".join([chunk async for chunk in self._objects.open_verified(ref)])
            parsed = _mapping(strict_json_parse(body))
        except Exception:
            return None
        if parsed.get("schema") != "yoetz.disclosure-proposal/1":
            return None
        prepared_b64 = parsed.get("prepared_bytes_base64")
        if type(prepared_b64) is not str:
            return None
        try:
            prepared_bytes = base64.b64decode(prepared_b64.encode("ascii"), validate=True)
        except Exception:
            return None
        scope_raw = parsed.get("scope")
        if scope_raw is None and "scope" in structural:
            scope_raw = structural["scope"]
        if scope_raw is None:
            return None
        binding = None
        local_sink = None
        if cast(str | None, row[14]) == "network":
            provider_id = cast(str | None, row[9])
            model_id = cast(str | None, row[10])
            endpoint_id = cast(str | None, row[11])
            endpoint_version = cast(str | None, row[12])
            if None in {provider_id, model_id, endpoint_id, endpoint_version}:
                return None
            binding = ProviderBinding(
                cast(str, provider_id),
                cast(str, model_id),
                cast(str, endpoint_id),
                cast(str, endpoint_version),
                "external",
            )
        else:
            sink_raw = cast(str | None, row[13])
            if sink_raw is None:
                return None
            local_sink = LocalDisclosureSink(sink_raw)
        transforms_raw = parsed.get("transformation_summary") or []
        if type(transforms_raw) is not list:
            return None
        transforms: list[tuple[str, int]] = []
        for item in transforms_raw:
            if type(item) is not list or len(item) != 2:
                return None
            if type(item[0]) is not str or type(item[1]) is not int:
                return None
            transforms.append((item[0], item[1]))
        max_bytes = int(cast(int | str, structural.get("max_bytes") or len(prepared_bytes)))
        max_tokens = int(cast(int | str, structural.get("max_tokens") or 0))
        commitment = structural.get("proposal_commitment")
        if type(commitment) is not str:
            return None
        try:
            return DisclosureProposal(
                cast(str, parsed["privacy_proposal_id"]),
                cast(str, parsed["request_id"]),
                task,
                _strings(parsed.get("source_item_digests") or []),
                prepared_bytes,
                tuple(
                    DataCategory(value)
                    for value in _strings(parsed.get("approved_categories") or [])
                ),
                tuple(
                    DataCategory(value)
                    for value in _strings(parsed.get("blocked_categories") or [])
                ),
                tuple(transforms),
                cast(str, parsed["prepared_case_digest"]),
                binding,
                local_sink,
                cast(str, parsed["purpose"]),
                _scope_from_json(scope_raw),
                int(cast(int | str, row[15])),
                cast(str, parsed["policy_digest"]),
                max_bytes,
                max_tokens,
                parse_rfc3339_millis(parsed["expires_at"]),
                commitment,
            )
        except Exception:
            return None

    async def load_authorization(self, authorization_id: str) -> EgressAuthorization | None:
        row = self._db.execute(
            """SELECT authorization_structural_canonical, state
               FROM privacy_audit_records WHERE authorization_id = ?""",
            (authorization_id,),
        ).fetchone()
        if row is None or row[1] != "authorized" or row[0] is None:
            return None
        try:
            source = _mapping(strict_json_parse(cast(bytes, row[0])))
            binding_raw = _mapping(source["provider_binding"])
            return EgressAuthorization(
                cast(str, source["authorization_id"]),
                cast(str, source["privacy_proposal_id"]),
                cast(str, source["case_digest"]),
                EgressChannel(cast(str, source["channel"])),
                ProviderBinding(
                    cast(str, binding_raw["provider_id"]),
                    cast(str, binding_raw["model_id"]),
                    cast(str, binding_raw["endpoint_profile_id"]),
                    cast(str, binding_raw["endpoint_profile_version"]),
                    "external",
                ),
                cast(str, source["purpose"]),
                _scope_from_json(source["scope"]),
                cast(int, source["policy_version"]),
                cast(str, source["policy_digest"]),
                cast(int, source["max_bytes"]),
                cast(int, source["max_tokens"]),
                ConsentSource(cast(str, source["consent_source"])),
                parse_rfc3339_millis(source["issued_at"]),
                parse_rfc3339_millis(source["expires_at"]),
                cast(int, source["service_generation"]),
            )
        except Exception:
            return None

    async def consume_local(
        self, reservation_id: str, approved_case_digest: str, now: datetime
    ) -> ConsumedLocalDisclosure:
        async with self._lock:
            with _transaction(self._db):
                row = self._db.execute(
                    "SELECT state, local_sink, subject_structural_canonical, expires_at FROM privacy_audit_records WHERE proposal_id = ?",
                    (reservation_id,),
                ).fetchone()
                if row is None or row[0] != "reserved":
                    raise ValueError("privacy_local_reservation_unavailable")
                if parse_rfc3339_millis(row[3]) <= now:
                    raise ValueError("privacy_local_reservation_expired")
                structural = _mapping(strict_json_parse(cast(bytes, row[2])))
                if structural.get("prepared_case_digest") != approved_case_digest:
                    raise ValueError("privacy_local_case_digest_mismatch")
                approval = _mac(self._key, _APPROVAL_DOMAIN, approved_case_digest.encode("ascii"))
                self._db.execute(
                    """UPDATE privacy_audit_records
                       SET state = 'local_disclosure_pending', consent_source = 'baseline_policy',
                           approval_binding_commitment = ?, consumed_at = ?, updated_at = ?
                       WHERE proposal_id = ? AND state = 'reserved'""",
                    (
                        approval,
                        format_rfc3339_millis(now),
                        format_rfc3339_millis(now),
                        reservation_id,
                    ),
                )
        return ConsumedLocalDisclosure(
            reservation_id,
            approved_case_digest,
            LocalDisclosureSink(cast(str, row[1])),
            now,
        )

    async def complete_local_disclosure(
        self, reservation_id: str, receipt: LocalDisclosureReceipt
    ) -> None:
        await self._complete_local(
            reservation_id, receipt, "local_disclosure_pending", "local_disclosure_completed"
        )

    async def complete_decision(
        self, reservation_id: str, receipt: EgressReceipt | LocalDisclosureReceipt
    ) -> None:
        if type(receipt) is LocalDisclosureReceipt:
            await self._complete_local(
                reservation_id, receipt, "decision_receipt_pending", "decision_completed"
            )
            return
        if type(receipt) is not EgressReceipt:
            raise TypeError("privacy_decision_receipt_invalid")
        canonical = _receipt_bytes(receipt)
        digest = canonical_digest(strict_json_parse(canonical))
        now = self._clock.now_utc()
        async with self._lock:
            with _transaction(self._db):
                changed = (
                    self._db.execute(
                        """UPDATE privacy_audit_records
                           SET state = 'decision_completed',
                               attempt_result_structural_canonical = ?,
                               attempt_result_commitment = ?,
                               receipt_id = ?, receipt_outcome = ?, receipt_reason = ?,
                               receipt_canonical = ?, receipt_digest = ?,
                               receipt_finished_at = ?, updated_at = ?
                           WHERE proposal_id = ? AND state = 'decision_receipt_pending'
                             AND receipt_id IS NULL""",
                        (
                            canonical,
                            digest,
                            receipt.receipt_id,
                            receipt.outcome.value,
                            None
                            if receipt.safe_failure_reason is None
                            else receipt.safe_failure_reason.value,
                            canonical,
                            digest,
                            format_rfc3339_millis(receipt.finished_at),
                            format_rfc3339_millis(now),
                            reservation_id,
                        ),
                    )
                    .getconnection()
                    .changes()
                )
                if changed != 1:
                    raise ValueError("privacy_audit_state_conflict")

    async def _complete_local(
        self,
        reservation_id: str,
        receipt: LocalDisclosureReceipt,
        expected: str,
        terminal: str,
    ) -> None:
        if receipt.privacy_proposal_id != reservation_id:
            raise ValueError("privacy_receipt_reservation_mismatch")
        canonical = _receipt_bytes(receipt)
        digest = canonical_digest(strict_json_parse(canonical))
        async with self._lock:
            with _transaction(self._db):
                changed = (
                    self._db.execute(
                        """UPDATE privacy_audit_records
                       SET state = ?, attempt_result_structural_canonical = ?,
                           attempt_result_commitment = ?, receipt_id = ?, receipt_outcome = ?,
                           receipt_reason = ?, receipt_canonical = ?, receipt_digest = ?,
                           receipt_finished_at = ?, updated_at = ?
                       WHERE proposal_id = ? AND state = ?""",
                        (
                            terminal,
                            canonical,
                            digest,
                            receipt.receipt_id,
                            receipt.outcome.value,
                            None
                            if receipt.safe_failure_reason is None
                            else receipt.safe_failure_reason.value,
                            canonical,
                            digest,
                            format_rfc3339_millis(receipt.finished_at),
                            format_rfc3339_millis(self._clock.now_utc()),
                            reservation_id,
                            expected,
                        ),
                    )
                    .getconnection()
                    .changes()
                )
                if changed != 1:
                    raise ValueError("privacy_audit_state_conflict")

    async def get_receipt(
        self, receipt_id: str, audience: PrivacyReceiptAudience
    ) -> PrivacyReceiptView | None:
        if audience is not PrivacyReceiptAudience.TRUSTED_LOCAL_CONTROL:
            raise ValueError("privacy_receipt_audience_invalid")
        row = self._db.execute(
            "SELECT destination_kind, receipt_canonical FROM privacy_audit_records WHERE receipt_id = ?",
            (receipt_id,),
        ).fetchone()
        if row is None:
            return None
        if row[0] != "local":
            raise ValueError("network_receipt_codec_deferred_to_b8")
        return LocalDisclosureReceiptView(
            "local_disclosure", _local_receipt_from_bytes(cast(bytes, row[1]))
        )

    async def list_receipts(
        self, query: PrivacyReceiptQuery, audience: PrivacyReceiptAudience
    ) -> PrivacyReceiptPage:
        if audience is not PrivacyReceiptAudience.TRUSTED_LOCAL_CONTROL:
            raise ValueError("privacy_receipt_audience_invalid")
        query_value: dict[str, JsonValue] = {
            "channel": None if query.channel is None else query.channel.value,
            "endpoint_profile_id": query.endpoint_profile_id,
            "finished_at_from": (
                None
                if query.finished_at_from is None
                else format_rfc3339_millis(query.finished_at_from)
            ),
            "finished_at_through": (
                None
                if query.finished_at_through is None
                else format_rfc3339_millis(query.finished_at_through)
            ),
            "local_sink": None if query.local_sink is None else query.local_sink.value,
            "outcome": None if query.outcome is None else query.outcome.value,
            "policy_version": query.policy_version,
            "provider_id": query.provider_id,
            "receipt_id": query.receipt_id,
            "scope_kind": None if query.scope_kind is None else query.scope_kind.value,
        }
        query_digest = canonical_digest(query_value)
        if query.cursor is None:
            snapshot_at = self._clock.now_utc()
            count_row = self._db.execute(
                "SELECT COUNT(*) + 1 FROM privacy_audit_records"
            ).fetchone()
            if count_row is None or type(count_row[0]) is not int:
                raise ValueError("privacy_audit_generation_unavailable")
            snapshot_generation = cast(int, count_row[0])
            after_at: datetime | None = None
            after_id: str | None = None
        else:
            cursor = self._decode_cursor(query.cursor)
            if cursor.get("query_digest") != query_digest:
                raise ValueError("privacy_receipt_cursor_query_mismatch")
            snapshot_at = parse_rfc3339_millis(cursor["snapshot_at"])
            snapshot_generation = _integer(cursor["snapshot_generation"])
            after_at = parse_rfc3339_millis(cursor["after_at"])
            after_id = cast(str, cursor["after_id"])
        clauses = ["receipt_id IS NOT NULL", "receipt_finished_at <= ?"]
        parameters: list[apsw.SQLiteValue] = [format_rfc3339_millis(snapshot_at)]
        fields = (
            ("receipt_id", query.receipt_id),
            ("receipt_outcome", None if query.outcome is None else query.outcome.value),
            ("channel", None if query.channel is None else query.channel.value),
            ("local_sink", None if query.local_sink is None else query.local_sink.value),
            ("provider_id", query.provider_id),
            ("endpoint_profile_id", query.endpoint_profile_id),
            ("policy_version", query.policy_version),
            ("scope_kind", None if query.scope_kind is None else query.scope_kind.value),
        )
        for column, value in fields:
            if value is not None:
                clauses.append(f"{column} = ?")
                parameters.append(value)
        if query.finished_at_from is not None:
            clauses.append("receipt_finished_at >= ?")
            parameters.append(format_rfc3339_millis(query.finished_at_from))
        if query.finished_at_through is not None:
            clauses.append("receipt_finished_at <= ?")
            parameters.append(format_rfc3339_millis(query.finished_at_through))
        if after_at is not None and after_id is not None:
            clauses.append(
                "(receipt_finished_at < ? OR (receipt_finished_at = ? AND receipt_id < ?))"
            )
            rendered = format_rfc3339_millis(after_at)
            parameters.extend((rendered, rendered, after_id))
        parameters.append(query.limit + 1)
        rows = self._db.execute(
            f"""SELECT destination_kind, receipt_canonical, receipt_finished_at, receipt_id
                FROM privacy_audit_records WHERE {" AND ".join(clauses)}
                ORDER BY receipt_finished_at DESC, receipt_id DESC LIMIT ?""",  # noqa: S608
            parameters,
        ).fetchall()
        selected = rows[: query.limit]
        receipts: list[PrivacyReceiptView] = []
        for kind, canonical, _, _ in selected:
            if kind != "local":
                continue
            receipts.append(
                LocalDisclosureReceiptView(
                    "local_disclosure", _local_receipt_from_bytes(cast(bytes, canonical))
                )
            )
        next_cursor = None
        if len(rows) > query.limit and selected:
            last = selected[-1]
            next_cursor = self._encode_cursor(
                {
                    "after_at": cast(str, last[2]),
                    "after_id": cast(str, last[3]),
                    "query_digest": query_digest,
                    "snapshot_at": format_rfc3339_millis(snapshot_at),
                    "snapshot_generation": snapshot_generation,
                    "version": 1,
                }
            )
        return PrivacyReceiptPage(snapshot_generation, tuple(receipts), next_cursor)

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
        try:
            padding = "=" * (-len(cursor) % 4)
            envelope = _mapping(strict_json_parse(base64.urlsafe_b64decode(cursor + padding)))
            body_text = cast(str, envelope["body"])
            body_padding = "=" * (-len(body_text) % 4)
            body = base64.urlsafe_b64decode(body_text + body_padding)
            expected = _mac(self._key, _CURSOR_DOMAIN, body)
            actual = cast(str, envelope["mac"])
            if not hmac.compare_digest(expected, actual):
                raise ValueError("privacy_receipt_cursor_invalid")
            payload = _mapping(strict_json_parse(body))
            if payload.get("version") != 1:
                raise ValueError("privacy_receipt_cursor_invalid")
            return payload
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("privacy_receipt_cursor_invalid") from exc

    async def live_object_roots(
        self, task_id: str, route_identity_digest: str
    ) -> PrivacyAuditObjectRoots:
        row = self._db.execute(
            "SELECT root_generation, root_digest FROM privacy_root_sets WHERE task_id = ? AND route_identity_digest = ?",
            (task_id, route_identity_digest),
        ).fetchone()
        refs = self._root_refs(task_id)
        generation = 0 if row is None else cast(int, row[0])
        digest = canonical_digest([_json(ref) for ref in refs])
        if row is not None and row[1] != digest:
            raise ValueError("privacy_root_set_corrupt")
        return PrivacyAuditObjectRoots(task_id, route_identity_digest, generation, refs, digest)

    async def revoke_policy_generation(self, generation: int, reason: str) -> int:
        del generation, reason
        return 0

    async def mark_awaiting_human(self, reservation_id: str) -> PrivacyAuditState:
        now = self._clock.now_utc()
        async with self._lock:
            with _transaction(self._db):
                row = self._db.execute(
                    """SELECT request_id, subject_lookup_identity, policy_digest, state, created_at
                       FROM privacy_audit_records WHERE proposal_id = ?""",
                    (reservation_id,),
                ).fetchone()
                if row is None or row[3] != "reserved":
                    raise ValueError("privacy_audit_state_conflict")
                changed = (
                    self._db.execute(
                        """UPDATE privacy_audit_records
                           SET state = 'awaiting_human', updated_at = ?
                           WHERE proposal_id = ? AND state = 'reserved'""",
                        (format_rfc3339_millis(now), reservation_id),
                    )
                    .getconnection()
                    .changes()
                )
                if changed != 1:
                    raise ValueError("privacy_audit_state_conflict")
        reservation = PrivacyAuditReservation(
            reservation_id,
            cast(str, row[0]),
            cast(str, row[1]),
            "awaiting_human",
            self._policy_generation(cast(str, row[2])),
            parse_rfc3339_millis(row[4]),
        )
        return PrivacyAuditState(reservation, "awaiting_human")

    async def record_human_decision(
        self, reservation_id: str, decision: HumanPrivacyDecision
    ) -> PrivacyAuditState:
        if type(decision) is not HumanPrivacyDecision:
            raise TypeError("privacy_human_decision_invalid")
        now = self._clock.now_utc()
        decision_bytes = canonical_encode(_json(decision))
        decision_commitment = _mac(self._key, _APPROVAL_DOMAIN, decision_bytes)
        async with self._lock:
            with _transaction(self._db):
                row = self._db.execute(
                    """SELECT request_id, subject_lookup_identity, policy_digest, state,
                              subject_structural_canonical, created_at
                       FROM privacy_audit_records WHERE proposal_id = ?""",
                    (reservation_id,),
                ).fetchone()
                if row is None or row[3] not in {"awaiting_human", "reserved"}:
                    raise ValueError("privacy_audit_state_conflict")
                structural = _mapping(strict_json_parse(cast(bytes, row[4])))
                if structural.get("proposal_commitment") != decision.proposal_commitment:
                    raise ValueError("privacy_decision_commitment_mismatch")
                if decision.approved:
                    next_state = "approved"
                    self._db.execute(
                        """UPDATE privacy_audit_records
                           SET state = ?, consent_source = ?, decision_structural_canonical = ?,
                               decision_commitment = ?, approval_binding_commitment = ?,
                               updated_at = ?
                           WHERE proposal_id = ? AND state IN ('awaiting_human', 'reserved')""",
                        (
                            next_state,
                            decision.consent_source.value,
                            decision_bytes,
                            decision_commitment,
                            _mac(
                                self._key,
                                _APPROVAL_DOMAIN,
                                cast(str, structural["prepared_case_digest"]).encode("ascii"),
                            ),
                            format_rfc3339_millis(now),
                            reservation_id,
                        ),
                    )
                else:
                    next_state = "denied"
                    # Denial receipt is completed by the coordinator via complete_decision.
                    self._db.execute(
                        """UPDATE privacy_audit_records
                           SET state = 'decision_receipt_pending', consent_source = ?,
                               decision_structural_canonical = ?, decision_commitment = ?,
                               updated_at = ?
                           WHERE proposal_id = ? AND state IN ('awaiting_human', 'reserved')""",
                        (
                            decision.consent_source.value,
                            decision_bytes,
                            decision_commitment,
                            format_rfc3339_millis(now),
                            reservation_id,
                        ),
                    )
                    next_state = "decision_receipt_pending"
                if self._db.execute("SELECT changes()").fetchone() is None:
                    raise ValueError("privacy_audit_state_conflict")
        reservation = PrivacyAuditReservation(
            reservation_id,
            cast(str, row[0]),
            cast(str, row[1]),
            next_state,
            self._policy_generation(cast(str, row[2])),
            parse_rfc3339_millis(row[5]),
        )
        return PrivacyAuditState(reservation, next_state)

    async def authorize(
        self, reservation_id: str, approved_case_digest: str, now: datetime
    ) -> EgressAuthorization:
        async with self._lock:
            with _transaction(self._db):
                row = self._db.execute(
                    """SELECT request_id, subject_lookup_identity, policy_digest, state,
                              subject_structural_canonical, provider_id, model_id,
                              endpoint_profile_id, endpoint_profile_version, purpose,
                              scope_kind, policy_version, expires_at, consent_source,
                              approval_binding_commitment, destination_kind
                       FROM privacy_audit_records WHERE proposal_id = ?""",
                    (reservation_id,),
                ).fetchone()
                if row is None or cast(str, row[15]) != "network":
                    raise ValueError("privacy_audit_authorization_unavailable")
                if cast(str, row[3]) not in {"reserved", "approved"}:
                    raise ValueError("privacy_audit_authorization_unavailable")
                if parse_rfc3339_millis(row[12]) <= now:
                    raise ValueError("privacy_authorization_expired")
                structural = _mapping(strict_json_parse(cast(bytes, row[4])))
                if structural.get("prepared_case_digest") != approved_case_digest:
                    raise ValueError("privacy_case_digest_mismatch")
                if "scope" not in structural:
                    raise ValueError("privacy_audit_authorization_unavailable")
                provider_id = cast(str | None, row[5])
                model_id = cast(str | None, row[6])
                endpoint_id = cast(str | None, row[7])
                endpoint_version = cast(str | None, row[8])
                if None in {provider_id, model_id, endpoint_id, endpoint_version}:
                    raise ValueError("privacy_audit_authorization_unavailable")
                binding = ProviderBinding(
                    cast(str, provider_id),
                    cast(str, model_id),
                    cast(str, endpoint_id),
                    cast(str, endpoint_version),
                    "external",
                )
                approval = cast(str | None, row[14]) or _mac(
                    self._key, _APPROVAL_DOMAIN, approved_case_digest.encode("ascii")
                )
                consent = cast(str | None, row[13]) or ConsentSource.BASELINE_POLICY.value
                authorization_id = new_id(IdKind.EGRESS_AUTHORIZATION)
                scope = _scope_from_json(structural["scope"])
                authorization = EgressAuthorization(
                    authorization_id,
                    reservation_id,
                    approved_case_digest,
                    EgressChannel.LLM_INFERENCE,
                    binding,
                    cast(str, row[9]),
                    scope,
                    cast(int, row[11]),
                    cast(str, row[2]),
                    int(cast(int | str, structural.get("max_bytes") or 0)),
                    int(cast(int | str, structural.get("max_tokens") or 0)),
                    ConsentSource(consent),
                    now,
                    now + timedelta(seconds=60),
                    self._service_generation,
                )
                auth_bytes = canonical_encode(_json(authorization))
                auth_commitment = _mac(self._key, _AUTHORIZATION_DOMAIN, auth_bytes)
                changed = (
                    self._db.execute(
                        """UPDATE privacy_audit_records
                           SET state = 'authorized', consent_source = ?,
                               approval_binding_commitment = ?,
                               authorization_id = ?,
                               authorization_structural_canonical = ?,
                               authorization_commitment = ?,
                               updated_at = ?
                           WHERE proposal_id = ? AND state IN ('reserved', 'approved')
                             AND authorization_id IS NULL""",
                        (
                            consent,
                            approval,
                            authorization_id,
                            auth_bytes,
                            auth_commitment,
                            format_rfc3339_millis(now),
                            reservation_id,
                        ),
                    )
                    .getconnection()
                    .changes()
                )
                if changed != 1:
                    raise ValueError("privacy_audit_authorization_unavailable")
        return authorization

    async def consume(
        self, authorization_id: str, dispatch_id: str, now: datetime
    ) -> ConsumedAuthorization:
        async with self._lock:
            with _transaction(self._db):
                row = self._db.execute(
                    """SELECT authorization_structural_canonical, state
                       FROM privacy_audit_records WHERE authorization_id = ?""",
                    (authorization_id,),
                ).fetchone()
                if row is None or row[1] != "authorized" or row[0] is None:
                    raise ValueError("privacy_audit_authorization_unavailable")
                source = _mapping(strict_json_parse(cast(bytes, row[0])))
                authorization = EgressAuthorization(
                    cast(str, source["authorization_id"]),
                    cast(str, source["privacy_proposal_id"]),
                    cast(str, source["case_digest"]),
                    EgressChannel(cast(str, source["channel"])),
                    ProviderBinding(
                        cast(
                            str, cast(dict[str, object], source["provider_binding"])["provider_id"]
                        ),
                        cast(str, cast(dict[str, object], source["provider_binding"])["model_id"]),
                        cast(
                            str,
                            cast(dict[str, object], source["provider_binding"])[
                                "endpoint_profile_id"
                            ],
                        ),
                        cast(
                            str,
                            cast(dict[str, object], source["provider_binding"])[
                                "endpoint_profile_version"
                            ],
                        ),
                        "external",
                    ),
                    cast(str, source["purpose"]),
                    _scope_from_json(source["scope"]),
                    cast(int, source["policy_version"]),
                    cast(str, source["policy_digest"]),
                    cast(int, source["max_bytes"]),
                    cast(int, source["max_tokens"]),
                    ConsentSource(cast(str, source["consent_source"])),
                    parse_rfc3339_millis(source["issued_at"]),
                    parse_rfc3339_millis(source["expires_at"]),
                    cast(int, source["service_generation"]),
                )
                changed = (
                    self._db.execute(
                        """UPDATE privacy_audit_records
                           SET state = 'receipt_pending', dispatch_id = ?,
                               dispatch_started_at = ?, consumed_at = ?, updated_at = ?
                           WHERE authorization_id = ? AND state = 'authorized'
                             AND dispatch_id IS NULL""",
                        (
                            dispatch_id,
                            format_rfc3339_millis(now),
                            format_rfc3339_millis(now),
                            format_rfc3339_millis(now),
                            authorization_id,
                        ),
                    )
                    .getconnection()
                    .changes()
                )
                if changed != 1:
                    raise ValueError("privacy_audit_authorization_unavailable")
        return ConsumedAuthorization(authorization, dispatch_id, now)

    async def complete_egress(self, dispatch_id: str, receipt: EgressReceipt) -> None:
        if type(receipt) is not EgressReceipt:
            raise TypeError("privacy_egress_receipt_invalid")
        canonical = _receipt_bytes(receipt)
        digest = canonical_digest(strict_json_parse(canonical))
        now = self._clock.now_utc()
        async with self._lock:
            with _transaction(self._db):
                changed = (
                    self._db.execute(
                        """UPDATE privacy_audit_records
                           SET state = 'attempt_completed',
                               attempt_result_structural_canonical = ?,
                               attempt_result_commitment = ?,
                               receipt_id = ?, receipt_outcome = ?, receipt_reason = ?,
                               receipt_canonical = ?, receipt_digest = ?,
                               receipt_finished_at = ?, updated_at = ?
                           WHERE dispatch_id = ? AND state = 'receipt_pending'
                             AND receipt_id IS NULL""",
                        (
                            canonical,
                            digest,
                            receipt.receipt_id,
                            receipt.outcome.value,
                            None
                            if receipt.safe_failure_reason is None
                            else receipt.safe_failure_reason.value,
                            canonical,
                            digest,
                            format_rfc3339_millis(receipt.finished_at),
                            format_rfc3339_millis(now),
                            dispatch_id,
                        ),
                    )
                    .getconnection()
                    .changes()
                )
                if changed != 1:
                    raise ValueError("privacy_egress_receipt_conflict")

    def _policy_generation(self, policy_digest: str) -> int:
        row = self._db.execute(
            "SELECT policy_generation FROM privacy_policy_versions WHERE policy_digest = ?",
            (policy_digest,),
        ).fetchone()
        return 1 if row is None else cast(int, row[0])

    def _root_refs(self, task_id: str) -> tuple[ObjectRef, ...]:
        rows = self._db.execute(
            """SELECT content_object_id, content_plaintext_size, content_commitment,
                      content_envelope_digest, content_encryption_format, content_key_slot,
                      content_media_type, content_created_at
               FROM privacy_audit_records
               WHERE task_id = ? AND content_object_id IS NOT NULL
               ORDER BY content_object_id""",
            (task_id,),
        ).fetchall()
        return tuple(
            ObjectRef(
                cast(str, row[0]),
                cast(int, row[1]),
                cast(str, row[2]),
                cast(str, row[3]),
                cast(Literal["yoetz-object/1"], row[4]),
                cast(str, row[5]),
                ObjectMetadata(
                    ObjectKind.PRIVACY_AUDIT,
                    cast(str, row[6]),
                    task_id,
                    parse_rfc3339_millis(row[7]),
                ),
            )
            for row in rows
        )

    def _refresh_roots(self, task_id: str, route: str, now: datetime) -> None:
        refs = self._root_refs(task_id)
        digest = canonical_digest([_json(ref) for ref in refs])
        current = self._db.execute(
            "SELECT root_generation FROM privacy_root_sets WHERE task_id = ?", (task_id,)
        ).fetchone()
        generation = 1 if current is None else cast(int, current[0]) + 1
        self._db.execute(
            """INSERT INTO privacy_root_sets (
                   task_id, route_identity_digest, root_generation, root_count, root_digest, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(task_id) DO UPDATE SET
                   route_identity_digest=excluded.route_identity_digest,
                   root_generation=excluded.root_generation, root_count=excluded.root_count,
                   root_digest=excluded.root_digest, updated_at=excluded.updated_at""",
            (task_id, route, generation, len(refs), digest, format_rfc3339_millis(now)),
        )
