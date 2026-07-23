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
    evidence_id,
    event_id,
    result_id,
)
from yoetz.protocol.canonical import canonical_digest, request_digest
from yoetz.protocol.coverage import (
    ArtifactObservation,
    AuthorshipAssurance,
    Coverage,
    PublicationChannel,
    coverage_for_channel,
)
from yoetz.protocol.ids import PREFIX_BY_KIND, IdKind

__all__ = [
    "MATERIALIZATION_MAPPING_VERSION",
    "MaterializedObservationBatch",
    "MaterializedObservationDraft",
    "materialize_observation_envelope",
    "stable_observation_id",
]

MATERIALIZATION_MAPPING_VERSION: Final = "obs-ledger/1.0.0"
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
    for key in ("tool_call_id", "correlation_id", "parent_tool_call_id"):
        value = payload.get(key)
        if type(value) is str and value:
            return value
    return None


def _exit_status(payload: Mapping[str, JsonValue]) -> int | None:
    value = payload.get("exit_status")
    if type(value) is int and not isinstance(value, bool):
        return value
    return None


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

    schema = EventSchema(schema_name, "1.0.0")
    draft = EventDraft(
        event_id(event),
        schema,
        occurred_at,
        tuple(event_id(parent) for parent in parents),
        cast(object, payload),  # pyright: ignore[reportArgumentType]
        (),
        (),
    )
    encoded = encode_payload(cast(object, payload))  # pyright: ignore[reportArgumentType]
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
        return MaterializedObservationBatch((), coverage_for_channel(PublicationChannel.HOOK_OBSERVED), PublicationChannel.HOOK_OBSERVED, (), "invalid_envelope")

    structural = cast(Mapping[str, JsonValue], envelope.structural_payload)
    gaps = tuple(envelope.gap_codes)
    coverage = _coverage_for(envelope)
    channel = PublicationChannel.HOOK_OBSERVED
    mapping = envelope.cursor.mapping_version or MATERIALIZATION_MAPPING_VERSION
    kind = envelope.event_kind
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

    if kind == "PreToolUse":
        if correlation is None:
            return MaterializedObservationBatch((), coverage, channel, gaps, "missing_tool_identity")
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
            return MaterializedObservationBatch((), coverage, channel, gaps, "missing_subagent_identity")
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

    if kind in _COMPLETION_KINDS or kind.endswith("Complete") or "claim" in kind.lower():
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
                    ClaimKind.COMPLETION if kind == "Stop" else ClaimKind.MATERIAL,
                    "Observation-derived claim; not automatic completion proof",
                    (),
                ),
                role="claim",
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


def observation_operation_digest(
    *,
    task_id: str,
    session_id: str,
    writer_id: str,
    source_identity: str,
    draft_roles: tuple[str, ...],
) -> str:
    """Stable request digest for idempotent observation appends."""

    return request_digest(
        JsonObject(
            {
                "protocol": "yoetz",
                "kind": "observation_materialize",
                "task_id": task_id,
                "session_id": session_id,
                "writer_id": writer_id,
                "source_identity": source_identity,
                "roles": draft_roles,
                "mapping_version": MATERIALIZATION_MAPPING_VERSION,
            }
        )
    )


def observation_author() -> Actor:
    return Actor(
        actor_id("yoetz:observation-coordinator"),
        ActorType.HARNESS,
        AuthorshipAssurance.HARNESS_OBSERVED,
    )


# Re-export for callers that stage objects.
media_type_for_schema = media_type_for
