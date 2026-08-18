"""Conservative observation → task-ledger materialization.

Supported envelopes become service-authored ledger events with ``hook_observed``
(or an honest weaker coverage). Unknown/unmapped shapes stay observation-only
with explicit gaps and never invent success proof.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal, cast

from yoetz.domain.events import (
    EVIDENCE_SCHEMA_VERSION,
    OBSERVATION_COORDINATOR_ACTOR_ID,
    ActionKind,
    ActionRecordedPayload,
    ClaimKind,
    ClaimRecordedPayload,
    DecisionRecordedPayload,
    EventDraft,
    EventSchema,
    EvidenceImmutability,
    EvidenceKind,
    EvidenceRecordedPayload,
    ResultOutcome,
    ResultRecordedPayload,
    encode_payload,
    media_type_for,
)
from yoetz.domain.observation import ObservationEnvelope, ObservationGapCode, ObservationSource
from yoetz.domain.values import (
    Actor,
    ActorType,
    JsonObject,
    JsonValue,
    Timestamp,
    action_id,
    actor_id,
    claim_id,
    event_id,
    evidence_id,
    result_id,
)
from yoetz.protocol.canonical import request_digest
from yoetz.protocol.coverage import (
    ArtifactObservation,
    AuthorshipAssurance,
    Coverage,
    PublicationChannel,
    coverage_for_channel,
)
from yoetz.protocol.ids import PREFIX_BY_KIND, IdKind

__all__ = [
    "approved_check_author",
    "MATERIALIZATION_MAPPING_VERSION",
    "MaterializedObservationBatch",
    "MaterializedObservationDraft",
    "canonical_logical_identity",
    "materialize_observation_envelope",
    "observation_claim_identity",
    "observation_writer_id",
    "stable_observation_id",
]

MATERIALIZATION_MAPPING_VERSION: Final = "obs-ledger/1.2.0"
_LOGICAL_IDENTITY_DOMAIN: Final = "yoetz/observation-logical-identity/v1"
_ID_DOMAIN: Final = b"yoetz/observation-materialize-id/v1\x00"
_FILE_TOOLS: Final = frozenset(
    {
        "apply_patch",
        "edit",
        "write_file",
        "write",
        "create_file",
        "multi_edit",
        "str_replace",
    }
)
_COMMAND_TOOLS: Final = frozenset(
    {
        "shell",
        "bash",
        "exec",
        "command",
        "run_terminal_cmd",
        "local_shell",
    }
)
_COMPLETION_KINDS: Final = frozenset(
    {
        "Stop",
        "AgentMessage",
        "UserPromptSubmit",
    }
)
_PERMISSION_KINDS: Final = frozenset({"PermissionRequest", "PermissionDecision"})
_SUBAGENT_START: Final = frozenset({"SubagentStart"})
_SUBAGENT_STOP: Final = frozenset({"SubagentStop"})


@dataclass(frozen=True, slots=True)
class MaterializedObservationDraft:
    draft: EventDraft
    payload_bytes: bytes
    projection_status: Literal["projected", "unknown_unprojected"]
    role: str


@dataclass(frozen=True, slots=True)
class MaterializedObservationBatch:
    drafts: tuple[MaterializedObservationDraft, ...]
    coverage: Coverage
    channel: PublicationChannel
    gaps: tuple[str, ...]
    skip_reason: str | None = None


def stable_observation_id(
    *,
    kind: IdKind,
    task_id: str,
    source_identity: str,
    mapping_version: str,
    role: str,
) -> str:
    """Allocate a deterministic UUIDv4-shaped ID for observation materialization."""

    material = (
        _ID_DOMAIN
        + f"{kind.value}\0{task_id}\0{source_identity}\0{mapping_version}\0{role}".encode()
    )
    digest = hashlib.sha256(material).digest()
    raw = bytearray(digest[:16])
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    return PREFIX_BY_KIND[kind] + str(uuid.UUID(bytes=bytes(raw)))


def _tool_name(payload: Mapping[str, JsonValue]) -> str | None:
    value = payload.get("tool_name")
    return value if type(value) is str else None


def _correlation(payload: Mapping[str, JsonValue]) -> str | None:
    # Codex spells the host tool-call id ``tool_use_id``; ingress normalizes it
    # to ``tool_call_id``, but read the host spelling first too so a payload
    # that reaches this seam un-normalized still correlates (#274).
    for key in ("tool_use_id", "tool_call_id", "correlation_id", "parent_tool_call_id"):
        value = payload.get(key)
        if type(value) is str and value:
            return value
    return None


def _exit_status(payload: Mapping[str, JsonValue]) -> int | None:
    value = payload.get("exit_status")
    if type(value) is int and not isinstance(value, bool):
        return value
    return None


def _explicit_post_failure(payload: Mapping[str, JsonValue]) -> bool:
    """True when a post-event is an explicit failure or denial, not status-unknown."""

    if _exit_status(payload) not in {None, 0}:
        return True
    return payload.get("success") is False or payload.get("denied") is True


def _action_kind(tool: str | None) -> ActionKind:
    if tool is None:
        return ActionKind.OTHER
    lowered = tool.lower()
    if lowered in _FILE_TOOLS or "edit" in lowered or "patch" in lowered:
        return ActionKind.EDIT
    if lowered in _COMMAND_TOOLS or "shell" in lowered or "exec" in lowered:
        return ActionKind.COMMAND
    return ActionKind.OTHER


def _coverage_for(envelope: ObservationEnvelope) -> Coverage:
    if envelope.source is ObservationSource.CODEX_HOOK and not envelope.gap_codes:
        return coverage_for_channel(PublicationChannel.HOOK_OBSERVED)
    baseline = coverage_for_channel(PublicationChannel.HOOK_OBSERVED)
    gaps = tuple(sorted({*baseline.known_gaps, *envelope.gap_codes}, key=str.encode))
    # Weaker honest coverage when gaps or stream-only evidence.
    observation = (
        ArtifactObservation.HOOK_OBSERVED
        if envelope.source is ObservationSource.CODEX_HOOK
        and ObservationGapCode.UNSUPPORTED_EVENT.value not in envelope.gap_codes
        else ArtifactObservation.PUBLISHED_ONLY
    )
    authorship = (
        AuthorshipAssurance.HARNESS_OBSERVED
        if observation is ArtifactObservation.HOOK_OBSERVED
        else AuthorshipAssurance.SERVICE_AUTHENTICATED
    )
    return Coverage(
        publication_channels=(PublicationChannel.HOOK_OBSERVED,),
        authorship_assurance=authorship,
        artifact_observation=observation,
        evidence_immutability=baseline.evidence_immutability,
        ledger_freshness=baseline.ledger_freshness,
        check_types=baseline.check_types,
        known_gaps=gaps,
    )


def _draft(
    *,
    event: str,
    schema_name: str,
    occurred_at: Timestamp,
    payload: object,
    parents: tuple[str, ...] = (),
    role: str,
) -> MaterializedObservationDraft:
    from yoetz.protocol.canonical import canonical_encode

    schema = EventSchema(
        schema_name,
        EVIDENCE_SCHEMA_VERSION if schema_name == "evidence_recorded" else "1.0.0",
    )
    draft = EventDraft(
        event_id(event),
        schema,
        occurred_at,
        tuple(event_id(parent) for parent in parents),
        payload,  # pyright: ignore[reportArgumentType]
        (),
        (),
    )
    encoded = encode_payload(payload)  # pyright: ignore[reportArgumentType]
    return MaterializedObservationDraft(
        draft=draft,
        payload_bytes=canonical_encode(encoded),
        projection_status="projected",
        role=role,
    )


def materialize_observation_envelope(
    envelope: ObservationEnvelope,
    *,
    task_id: str,
) -> MaterializedObservationBatch:
    """Map one envelope to zero or more ledger drafts.

    Returns ``skip_reason`` when the envelope should remain observation-store-only.
    """

    if type(envelope) is not ObservationEnvelope:
        return MaterializedObservationBatch(
            (),
            coverage_for_channel(PublicationChannel.HOOK_OBSERVED),
            PublicationChannel.HOOK_OBSERVED,
            (),
            "invalid_envelope",
        )

    structural = cast(Mapping[str, JsonValue], envelope.structural_payload)
    gaps = tuple(envelope.gap_codes)
    coverage = _coverage_for(envelope)
    channel = PublicationChannel.HOOK_OBSERVED
    mapping = envelope.cursor.mapping_version or MATERIALIZATION_MAPPING_VERSION
    kind = envelope.event_kind
    # Codex hook ``PostToolUse`` and session-stream ``item.completed`` are two
    # observations of the same completed host call. Normalize the stream form
    # before choosing ledger roles so both sources produce the same
    # action/result batch and therefore the same operation digest.
    if (
        envelope.source is ObservationSource.CODEX_SESSION_STREAM
        and kind == "item.completed"
        and _correlation(structural) is not None
    ):
        kind = "PostToolUse"
    tool = _tool_name(structural)
    correlation = _correlation(structural)
    unpaired = ObservationGapCode.UNPAIRED_EVENT.value in gaps
    unsupported = (
        ObservationGapCode.UNSUPPORTED_EVENT.value in gaps
        or kind in {"unsupported_event", "observation_gap"}
        or kind.startswith("unsupported")
    )

    if unsupported or kind == "observation_gap":
        return MaterializedObservationBatch((), coverage, channel, gaps, "unsupported_or_gap")

    if kind in {"SessionStart", "SessionEnd", "PreCompact", "PostCompact"}:
        # Lifecycle bookkeeping stays in observation store / mapping; no ledger claim.
        return MaterializedObservationBatch((), coverage, channel, gaps, "lifecycle_only")

    drafts: list[MaterializedObservationDraft] = []
    routine_read = structural.get("action") == "routine_read"

    if kind == "PreToolUse":
        if routine_read:
            # The full envelope remains in the observation store. Successful read-only calls are
            # rate-limited at the task-ledger boundary rather than minting one pending action per
            # file lookup; a failed PostToolUse still materializes below.
            return MaterializedObservationBatch(
                (), coverage, channel, gaps, "routine_read_deferred"
            )
        if correlation is None:
            return MaterializedObservationBatch(
                (), coverage, channel, gaps, "missing_tool_identity"
            )
        action = stable_observation_id(
            kind=IdKind.ACTION,
            task_id=task_id,
            source_identity=envelope.source_identity,
            mapping_version=mapping,
            role="action",
        )
        event = stable_observation_id(
            kind=IdKind.EVENT,
            task_id=task_id,
            source_identity=envelope.source_identity,
            mapping_version=mapping,
            role="action_event",
        )
        action_kind = _action_kind(tool)
        description = f"Observed pending {action_kind.value} via Codex hook"
        command = None
        if action_kind is ActionKind.COMMAND:
            # Command text omitted/encrypted; placeholder digest-only description.
            digest = structural.get("command_digest") or structural.get("argv_digest")
            command = f"omitted:{digest}" if type(digest) is str else "omitted:structural"
        drafts.append(
            _draft(
                event=event,
                schema_name="action_recorded",
                occurred_at=envelope.receipt_time,
                payload=ActionRecordedPayload(
                    action_id(action),
                    action_kind,
                    description,
                    command=command,
                    attempted_items=(),
                ),
                role="action",
            )
        )
        return MaterializedObservationBatch(tuple(drafts), coverage, channel, gaps, None)

    if kind == "PostToolUse":
        if routine_read and not _explicit_post_failure(structural):
            return MaterializedObservationBatch(
                (), coverage, channel, gaps, "routine_read_coalesced"
            )
        if unpaired or correlation is None:
            # Standalone structural observation: evidence only; do not invent the action.
            evidence = stable_observation_id(
                kind=IdKind.EVIDENCE,
                task_id=task_id,
                source_identity=envelope.source_identity,
                mapping_version=mapping,
                role="unpaired_result",
            )
            event = stable_observation_id(
                kind=IdKind.EVENT,
                task_id=task_id,
                source_identity=envelope.source_identity,
                mapping_version=mapping,
                role="unpaired_result_event",
            )
            exit_status = _exit_status(structural)
            summary = (
                f"Unpaired observed tool result exit={exit_status}"
                if exit_status is not None
                else "Unpaired observed tool result"
            )
            drafts.append(
                _draft(
                    event=event,
                    schema_name="evidence_recorded",
                    occurred_at=envelope.receipt_time,
                    payload=EvidenceRecordedPayload(
                        evidence_id(evidence),
                        EvidenceKind.OTHER,
                        EvidenceImmutability.METADATA_ONLY,
                        envelope.receipt_time,
                        description=summary,
                    ),
                    role="unpaired_evidence",
                )
            )
            merged_gaps = tuple(
                sorted({*gaps, ObservationGapCode.UNPAIRED_EVENT.value}, key=str.encode)
            )
            return MaterializedObservationBatch(tuple(drafts), coverage, channel, merged_gaps, None)

        # Linked post: action (idempotent stable IDs from correlation) + result.
        action_source = f"pre:{correlation}:{tool or 'tool'}"
        action = stable_observation_id(
            kind=IdKind.ACTION,
            task_id=task_id,
            source_identity=action_source,
            mapping_version=mapping,
            role="action",
        )
        action_event = stable_observation_id(
            kind=IdKind.EVENT,
            task_id=task_id,
            source_identity=action_source,
            mapping_version=mapping,
            role="action_event",
        )
        result = stable_observation_id(
            kind=IdKind.RESULT,
            task_id=task_id,
            source_identity=envelope.source_identity,
            mapping_version=mapping,
            role="result",
        )
        result_event = stable_observation_id(
            kind=IdKind.EVENT,
            task_id=task_id,
            source_identity=envelope.source_identity,
            mapping_version=mapping,
            role="result_event",
        )
        action_kind = _action_kind(tool)
        command = None
        if action_kind is ActionKind.COMMAND:
            digest = structural.get("command_digest") or structural.get("argv_digest")
            command = f"omitted:{digest}" if type(digest) is str else "omitted:structural"
        drafts.append(
            _draft(
                event=action_event,
                schema_name="action_recorded",
                occurred_at=envelope.receipt_time,
                payload=ActionRecordedPayload(
                    action_id(action),
                    action_kind,
                    f"Observed {action_kind.value} via Codex hook",
                    command=command,
                ),
                role="action",
            )
        )
        exit_status = _exit_status(structural)
        if exit_status is None:
            outcome = ResultOutcome.UNKNOWN
        elif exit_status == 0:
            outcome = ResultOutcome.SUCCESS
        else:
            outcome = ResultOutcome.FAILURE
        drafts.append(
            _draft(
                event=result_event,
                schema_name="result_recorded",
                occurred_at=envelope.receipt_time,
                payload=ResultRecordedPayload(
                    result_id(result),
                    action_id(action),
                    outcome,
                    exit_status=exit_status,
                    summary=f"Observed result status={outcome.value}",
                ),
                parents=(action_event,),
                role="result",
            )
        )
        return MaterializedObservationBatch(tuple(drafts), coverage, channel, gaps, None)

    if kind in _PERMISSION_KINDS:
        event = stable_observation_id(
            kind=IdKind.EVENT,
            task_id=task_id,
            source_identity=envelope.source_identity,
            mapping_version=mapping,
            role="permission_decision",
        )
        decision = structural.get("permission_decision") or structural.get("decision_reason_code")
        statement = (
            f"Permission decision observed: {decision}"
            if type(decision) is str
            else "Permission decision observed"
        )
        drafts.append(
            _draft(
                event=event,
                schema_name="decision_recorded",
                occurred_at=envelope.receipt_time,
                payload=DecisionRecordedPayload(
                    statement=statement,
                    rationale="codex_hook_permission_evidence",
                    authority=actor_id("yoetz:observation-coordinator"),
                ),
                role="permission",
            )
        )
        return MaterializedObservationBatch(tuple(drafts), coverage, channel, gaps, None)

    if kind in _SUBAGENT_START or kind in _SUBAGENT_STOP:
        # Assignment requires obligations; record evidence-only until correlation is complete.
        if correlation is None and structural.get("subagent_id") is None:
            return MaterializedObservationBatch(
                (), coverage, channel, gaps, "missing_subagent_identity"
            )
        evidence = stable_observation_id(
            kind=IdKind.EVIDENCE,
            task_id=task_id,
            source_identity=envelope.source_identity,
            mapping_version=mapping,
            role="subagent",
        )
        event = stable_observation_id(
            kind=IdKind.EVENT,
            task_id=task_id,
            source_identity=envelope.source_identity,
            mapping_version=mapping,
            role="subagent_event",
        )
        phase = "start" if kind in _SUBAGENT_START else "stop"
        drafts.append(
            _draft(
                event=event,
                schema_name="evidence_recorded",
                occurred_at=envelope.receipt_time,
                payload=EvidenceRecordedPayload(
                    evidence_id(evidence),
                    EvidenceKind.OTHER,
                    EvidenceImmutability.METADATA_ONLY,
                    envelope.receipt_time,
                    description=f"Observed subagent {phase}",
                ),
                role="subagent",
            )
        )
        return MaterializedObservationBatch(tuple(drafts), coverage, channel, gaps, None)

    explicit_claim_kind = structural.get("claim_kind")
    if type(explicit_claim_kind) is str and explicit_claim_kind in {
        member.value for member in ClaimKind
    }:
        claim = stable_observation_id(
            kind=IdKind.CLAIM,
            task_id=task_id,
            source_identity=envelope.source_identity,
            mapping_version=mapping,
            role="claim",
        )
        event = stable_observation_id(
            kind=IdKind.EVENT,
            task_id=task_id,
            source_identity=envelope.source_identity,
            mapping_version=mapping,
            role="claim_event",
        )
        drafts.append(
            _draft(
                event=event,
                schema_name="claim_recorded",
                occurred_at=envelope.receipt_time,
                payload=ClaimRecordedPayload(
                    claim_id(claim),
                    ClaimKind(explicit_claim_kind),
                    "Explicit claim signal observed; not automatic completion proof",
                    (),
                ),
                role="claim",
            )
        )
        return MaterializedObservationBatch(tuple(drafts), coverage, channel, gaps, None)

    if kind in _COMPLETION_KINDS or kind.endswith("Complete") or "claim" in kind.lower():
        evidence = stable_observation_id(
            kind=IdKind.EVIDENCE,
            task_id=task_id,
            source_identity=envelope.source_identity,
            mapping_version=mapping,
            role="completion_signal",
        )
        event = stable_observation_id(
            kind=IdKind.EVENT,
            task_id=task_id,
            source_identity=envelope.source_identity,
            mapping_version=mapping,
            role="completion_signal_event",
        )
        drafts.append(
            _draft(
                event=event,
                schema_name="evidence_recorded",
                occurred_at=envelope.receipt_time,
                payload=EvidenceRecordedPayload(
                    evidence_id(evidence),
                    EvidenceKind.OTHER,
                    EvidenceImmutability.METADATA_ONLY,
                    envelope.receipt_time,
                    description=f"Observed completion signal kind={kind}",
                ),
                role="completion_signal",
            )
        )
        return MaterializedObservationBatch(tuple(drafts), coverage, channel, gaps, None)

    # Opaque structural observation for unrecognized but admitted kinds.
    evidence = stable_observation_id(
        kind=IdKind.EVIDENCE,
        task_id=task_id,
        source_identity=envelope.source_identity,
        mapping_version=mapping,
        role="opaque",
    )
    event = stable_observation_id(
        kind=IdKind.EVENT,
        task_id=task_id,
        source_identity=envelope.source_identity,
        mapping_version=mapping,
        role="opaque_event",
    )
    drafts.append(
        _draft(
            event=event,
            schema_name="evidence_recorded",
            occurred_at=envelope.receipt_time,
            payload=EvidenceRecordedPayload(
                evidence_id(evidence),
                EvidenceKind.OTHER,
                EvidenceImmutability.METADATA_ONLY,
                envelope.receipt_time,
                description=f"Opaque observation kind={kind}",
            ),
            role="opaque",
        )
    )
    return MaterializedObservationBatch(tuple(drafts), coverage, channel, gaps, None)


def canonical_logical_identity(envelope: ObservationEnvelope) -> str:
    """Return the canonical logical-observation identity for one envelope.

    Hook ``PostToolUse`` and stream ``item.completed`` copies of the same host
    call collapse to one identity (session + host call/correlation id + tool
    family), so cross-source duplicates materialize a single ledger action or
    result. Consecutive identical commands with *different* host ids stay
    distinct. Events without a host call id (lifecycle, unsupported, or gap
    envelopes) fall back to a source-specific opaque identity so unrelated
    look-alikes never collide.
    """

    if type(envelope) is not ObservationEnvelope:
        return _logical_identity_digest(("opaque", "invalid"))
    structural = cast(Mapping[str, JsonValue], envelope.structural_payload)
    host_call = _correlation(structural)
    if host_call is None:
        return _logical_identity_digest(("opaque", envelope.source.value, envelope.source_identity))
    family = _action_kind(_tool_name(structural)).value
    return _logical_identity_digest(("action", envelope.session_commitment, host_call, family))


def observation_claim_identity(envelope: ObservationEnvelope, draft_roles: tuple[str, ...]) -> str:
    """Return the role-scoped key for one durable logical-identity claim.

    One host call legitimately materializes several role-sets against the same
    canonical logical identity: hook ``PreToolUse`` (``action``), a paired
    ``PostToolUse`` (``action`` + ``result``), unpaired evidence, permission and
    subagent events carrying the call id, and opaque stream phases. Each has its
    own operation digest, so claims keyed on the bare logical identity conflict
    with their own siblings. Scoping the claim by roles (and by the mapping
    version, matching the digest) keeps each phase's claim independent while
    cross-source copies of the *same* phase still merge their source masks.
    """

    return _logical_identity_digest(
        (
            "claim",
            MATERIALIZATION_MAPPING_VERSION,
            canonical_logical_identity(envelope),
            *draft_roles,
        )
    )


def _logical_identity_digest(components: tuple[str, ...]) -> str:
    """Hash domain-separated components into a nul-free ``sha256:`` identity.

    The result is safe to embed in canonical request digests (which forbid nul
    bytes) while remaining a stable equality key across sources.
    """

    material = _LOGICAL_IDENTITY_DOMAIN.encode() + b"\x00"
    material += b"\x00".join(component.encode() for component in components)
    return "sha256:" + hashlib.sha256(material).hexdigest()


def observation_operation_digest(
    *,
    task_id: str,
    session_id: str,
    writer_id: str,
    logical_identity: str,
    draft_roles: tuple[str, ...],
) -> str:
    """Stable request digest for idempotent observation appends.

    Keyed on the canonical *logical* identity rather than the source-specific
    identity so matching hook/stream copies produce one ledger operation.
    """

    return request_digest(
        JsonObject(
            {
                "protocol": "yoetz",
                "kind": "observation_materialize",
                "task_id": task_id,
                "session_id": session_id,
                "writer_id": writer_id,
                "logical_identity": logical_identity,
                "roles": draft_roles,
                "mapping_version": MATERIALIZATION_MAPPING_VERSION,
            }
        )
    )


def observation_author() -> Actor:
    return Actor(
        actor_id(OBSERVATION_COORDINATOR_ACTOR_ID),
        ActorType.HARNESS,
        AuthorshipAssurance.HARNESS_OBSERVED,
    )


def observation_writer_id(task_id: str, session_id: str) -> str:
    """Derive the admitted harness writer for one task session."""

    return stable_observation_id(
        kind=IdKind.WRITER,
        task_id=task_id,
        source_identity=session_id,
        mapping_version="obs-writer/1.0.0",
        role="observation",
    )


def approved_check_author() -> Actor:
    return Actor(
        actor_id("yoetz:approved-check-service"),
        ActorType.YOETZ_ENGINE,
        AuthorshipAssurance.SERVICE_AUTHENTICATED,
    )


# Re-export for callers that stage objects.
media_type_for_schema = media_type_for
