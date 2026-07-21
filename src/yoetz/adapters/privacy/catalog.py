"""Catalog-backed, provider-free privacy policy and audit persistence."""

from __future__ import annotations

import asyncio
import base64
import hmac
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Literal, cast

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
    EgressReceipt,
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

__all__ = ["CatalogPrivacyAudit", "CatalogPrivacyPolicyStore", "decode_privacy_policy_canonical"]

_LOOKUP_DOMAIN = b"yoetz/privacy-audit/lookup/v1\x00"
_PROPOSAL_DOMAIN = b"yoetz/privacy-audit/proposal/v1\x00"
_CONTROL_DOMAIN = b"yoetz/privacy-audit/control-request/v1\x00"
_INTERNAL_RESULT_DOMAIN = b"yoetz/privacy-audit/internal-result/v1\x00"
_PROJECTION_DOMAIN = b"yoetz/privacy-audit/projection/v1\x00"
_APPROVAL_DOMAIN = b"yoetz/privacy-audit/local-approval/v1\x00"
_CURSOR_DOMAIN = b"yoetz/privacy-audit/receipt-cursor/v1\x00"


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
        __import__("yoetz.domain.privacy", fromlist=["EgressChannel"]).EgressChannel(
            cast(str, source["channel"])
        ),
        cast(bool, source["enabled"]),
        tuple(DataCategory(item) for item in _strings(source["allowed_categories"])),
        tuple(DataClass(item) for item in _strings(source["allowed_data_classes"])),
        _binding_from_json(source.get("provider_binding")),
        _strings(source["allowed_purposes"]),
        AuthorizationScopeKind(cast(str, source["scope_ceiling"])),
        cast(bool, source["preview_required"]),
        _integer(source["max_bytes"]),
        _integer(source["max_tokens"]),
        _integer(source["authorization_ttl_seconds"]),
    )


def _policy_from_bytes(data: bytes) -> PrivacyPolicy:
    source = _mapping(strict_json_parse(data))
    channels = source["channel_policies"]
    if type(channels) is not list:
        raise ValueError("privacy_policy_row_corrupt")
    return PrivacyPolicy(
        cast(str, source["policy_id"]),
        _integer(source["version"]),
        cast(str, source["policy_digest"]),
        PrivacyProfile(cast(str, source["profile"])),
        ReviewContextProfile(cast(str, source["review_context_profile"])),
        _review_from_json(source["review_selection"]),
        cast(bool, source["require_current_provider_data_use_evidence"]),
        cast(bool, source["network_egress_permitted"]),
        _scope_from_json(source["effective_scope"]),
        tuple(_channel_from_json(item) for item in channels),
        cast(bool, source["local_model_enabled"]),
        _binding_from_json(source["local_model_binding"]),
        tuple(DataCategory(item) for item in _strings(source["local_model_categories"])),
        tuple(DataClass(item) for item in _strings(source["local_model_data_classes"])),
        tuple(DataCategory(item) for item in _strings(source["agent_context_categories"])),
        tuple(DataClass(item) for item in _strings(source["agent_context_data_classes"])),
        tuple(DataCategory(item) for item in _strings(source["trusted_human_control_categories"])),
        tuple(DataClass(item) for item in _strings(source["trusted_human_control_data_classes"])),
        parse_rfc3339_millis(source["created_at"]),
        cast(str | None, source["supersedes_policy_digest"]),
    )


def decode_privacy_policy_canonical(data: bytes) -> PrivacyPolicy:
    """Decode a canonical privacy-policy JSON document (desired-state apply path)."""

    return _policy_from_bytes(data)


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
        policy, generation = max(
            eligible,
            key=lambda item: (
                {
                    AuthorizationScopeKind.MACHINE: 0,
                    AuthorizationScopeKind.WORKSPACE: 1,
                    AuthorizationScopeKind.TASK: 2,
                    AuthorizationScopeKind.REQUEST: 3,
                }[item[0].effective_scope.kind],
                item[1],
            ),
        )
        return EffectivePrivacyPolicy(policy, generation, policy.policy_digest)

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

    __slots__ = ("_clock", "_db", "_key", "_lock", "_objects")

    def __init__(
        self,
        db: apsw.Connection,
        objects: ObjectStorePort,
        audit_key: MacKeyHandle,
        clock: ClockPort,
    ) -> None:
        self._db = db
        self._objects = objects
        self._key = audit_key
        self._clock = clock
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
            _json(proposal)
            if False
            else {
                "policy_digest": request.policy_digest,
                "prepared_case_digest": request.minimized.case_digest,
                "proposal_commitment": proposal_commitment,
                "request_id": request.request_id,
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
        if type(receipt) is not LocalDisclosureReceipt:
            raise ValueError("network_audit_deferred_to_b8")
        await self._complete_local(
            reservation_id, receipt, "decision_receipt_pending", "decision_completed"
        )

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
