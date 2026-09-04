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
    EvidenceContentAvailability,
    EvidenceDigestBinding,
    EvidenceDigestProvenance,
    EvidenceDigestSubject,
    EvidenceImmutability,
    EvidenceKind,
    EvidenceRecordedPayload,
    ResultOutcome,
    ResultRecordedPayload,
    encode_payload,
    media_type_for,
)
from yoetz.domain.observation import (
    ObservationContentKind,
    ObservationContentManifest,
    ObservationEnvelope,
    ObservationGapCode,
    ObservationInspectionSnapshot,
    ObservationSource,
)
from yoetz.domain.values import (
    Actor,
    ActorType,
    JsonObject,
    JsonValue,
    SubjectStateRef,
    Timestamp,
    action_id,
    actor_id,
    claim_id,
    event_id,
    evidence_id,
    object_id,
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
    "HOST_OUTCOME_UNAVAILABLE_GAP",
    "approved_check_author",
    "MATERIALIZATION_LEGACY_MAPPING_VERSIONS",
    "MATERIALIZATION_MAPPING_VERSION",
    "SESSION_BOUND_MAPPING_VERSIONS",
    "MaterializedObservationBatch",
    "MaterializedObservationDraft",
    "STREAM_COMPLETED_EVENT_KINDS",
    "canonical_logical_identity",
    "materialize_observation_envelope",
    "materialize_observation_inspection_snapshot",
    "materialize_observation_outcome_correction",
    "observation_claim_identity",
    "observation_content_identity",
    "observation_writer_id",
    "stable_observation_id",
    "stream_event_is_completed_tool",
]

MATERIALIZATION_MAPPING_VERSION: Final = "obs-ledger/1.5.0"
MATERIALIZATION_LEGACY_MAPPING_VERSIONS: Final = (
    "obs-ledger/1.4.0",
    "obs-ledger/1.3.0",
    "obs-ledger/1.2.0",
)
# Mapping versions whose operation digest was bound to the routed Yoetz session
# and its observation writer. A workflow reattach in the same host session
# rotates both, so their committed operations can only be found from the
# session that committed them (#560). ``obs-ledger/1.5.0`` keys the digest on
# the task, the canonical logical identity, and the role tuple only — exactly
# the facts the stable event ids are derived from — and is resolved task-wide.
SESSION_BOUND_MAPPING_VERSIONS: Final = frozenset(MATERIALIZATION_LEGACY_MAPPING_VERSIONS)
# One bounded coverage condition for "the host emitted a paired tool result with
# no outcome semantics at all" (#350). It rides the entry coverage of the
# affected action/result records, so any number of outcome-less observed calls
# fold into a single known-gap code in check coverage and the receipt instead of
# one material-limitation candidate per call. It is deliberately not an
# ObservationGapCode: envelopes are complete observations; the gap belongs to
# the materialized ledger records whose outcome the host did not state.
HOST_OUTCOME_UNAVAILABLE_GAP: Final = "host_outcome_unavailable"
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
STREAM_COMPLETED_EVENT_KINDS: Final = frozenset(
    {
        "custom_tool_call_output",
        "function_call_output",
        "item.completed",
        "item_completed",
    }
)
_CAPTURED_EVIDENCE_KINDS: Final = frozenset(
    {
        ObservationContentKind.TOOL_OUTPUT,
        ObservationContentKind.CHANGED_FILE,
        ObservationContentKind.WORKSPACE_DIFF,
    }
)


def stream_event_is_completed_tool(kind: str, structural: Mapping[str, JsonValue]) -> bool:
    """True when a session-stream envelope is a completed host tool call."""

    if kind in STREAM_COMPLETED_EVENT_KINDS:
        return True
    action = structural.get("action")
    return (
        kind == "response_item" and type(action) is str and action in STREAM_COMPLETED_EVENT_KINDS
    )


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


# Closed result_status vocabularies. Codex hosts spell tool outcomes several
# ways; only exact known spellings map to an outcome, and any other string
# stays UNKNOWN so an unrecognized status is never upgraded to success.
_RESULT_STATUS_SUCCESS: Final = frozenset({"success", "succeeded", "ok", "completed", "passed"})
_RESULT_STATUS_FAILURE: Final = frozenset(
    {
        "failure",
        "failed",
        "error",
        "errored",
        "denied",
        "aborted",
        "cancelled",
        "canceled",
        "timeout",
        "timed_out",
        "interrupted",
    }
)
_RESULT_STATUS_PARTIAL: Final = frozenset({"partial", "partially_completed"})


def _post_outcome(payload: Mapping[str, JsonValue]) -> ResultOutcome:
    """Map every host-provided outcome fact to a result outcome (#350).

    The real Codex ``PostToolUse`` payload may state its outcome as
    ``exit_status``, ``denied``, boolean ``success``, or a ``result_status``
    string; all of them are consumed rather than discarded. Any explicit
    failure signal wins over any success signal — conflicting facts must never
    launder a stated failure into success — and only a payload with no outcome
    fact at all is UNKNOWN: a missing outcome is never upgraded to success.
    """

    exit_status = _exit_status(payload)
    status = payload.get("result_status")
    lowered = status.lower() if type(status) is str else None
    if (
        (exit_status is not None and exit_status != 0)
        or payload.get("denied") is True
        or payload.get("success") is False
        or lowered in _RESULT_STATUS_FAILURE
    ):
        return ResultOutcome.FAILURE
    if lowered in _RESULT_STATUS_PARTIAL:
        return ResultOutcome.PARTIAL
    if exit_status == 0 or payload.get("success") is True or lowered in _RESULT_STATUS_SUCCESS:
        return ResultOutcome.SUCCESS
    return ResultOutcome.UNKNOWN


def _action_kind(tool: str | None) -> ActionKind:
    if tool is None:
        return ActionKind.OTHER
    lowered = tool.lower()
    if lowered in _FILE_TOOLS or "edit" in lowered or "patch" in lowered:
        return ActionKind.EDIT
    if lowered in _COMMAND_TOOLS or "shell" in lowered or "exec" in lowered:
        return ActionKind.COMMAND
    return ActionKind.OTHER


def _coverage_for(
    envelope: ObservationEnvelope,
    *,
    gaps: tuple[str, ...] | None = None,
    content_captured: bool = False,
) -> Coverage:
    effective_gaps = envelope.gap_codes if gaps is None else gaps
    if (
        envelope.source is ObservationSource.CODEX_HOOK
        and not effective_gaps
        and not content_captured
    ):
        return coverage_for_channel(PublicationChannel.HOOK_OBSERVED)
    baseline = coverage_for_channel(PublicationChannel.HOOK_OBSERVED)
    known_gaps = tuple(sorted({*baseline.known_gaps, *effective_gaps}, key=str.encode))
    # Weaker honest coverage when gaps or stream-only evidence.
    observation = (
        ArtifactObservation.CONTENT_CAPTURED
        if content_captured
        else ArtifactObservation.HOOK_OBSERVED
        if envelope.source is ObservationSource.CODEX_HOOK
        and ObservationGapCode.UNSUPPORTED_EVENT.value not in effective_gaps
        else ArtifactObservation.PUBLISHED_ONLY
    )
    authorship = (
        AuthorshipAssurance.HARNESS_OBSERVED
        if envelope.source is ObservationSource.CODEX_HOOK
        and observation in {ArtifactObservation.HOOK_OBSERVED, ArtifactObservation.CONTENT_CAPTURED}
        else AuthorshipAssurance.SERVICE_AUTHENTICATED
    )
    return Coverage(
        publication_channels=(PublicationChannel.HOOK_OBSERVED,),
        authorship_assurance=authorship,
        artifact_observation=observation,
        evidence_immutability=(
            EvidenceImmutability.IMMUTABLE_SNAPSHOT
            if content_captured
            else baseline.evidence_immutability
        ),
        ledger_freshness=baseline.ledger_freshness,
        check_types=baseline.check_types,
        known_gaps=known_gaps,
    )


def _draft(
    *,
    event: str,
    schema_name: str,
    occurred_at: Timestamp,
    payload: object,
    parents: tuple[str, ...] = (),
    artifact_refs: tuple[str, ...] = (),
    evidence_refs: tuple[str, ...] = (),
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
        tuple(object_id(value) for value in artifact_refs),
        tuple(evidence_id(value) for value in evidence_refs),
    )
    encoded = encode_payload(payload)  # pyright: ignore[reportArgumentType]
    return MaterializedObservationDraft(
        draft=draft,
        payload_bytes=canonical_encode(encoded),
        projection_status="projected",
        role=role,
    )


def _eligible_captured_content(
    envelope: ObservationEnvelope,
    manifests: tuple[ObservationContentManifest, ...],
) -> tuple[tuple[ObservationContentManifest, ...], tuple[str, ...]]:
    """Select only service-bound content and name every missing eligible object honestly."""

    by_object = {
        item.object_id: item for item in manifests if type(item) is ObservationContentManifest
    }
    gaps = set(envelope.gap_codes)
    eligible: list[ObservationContentManifest] = []
    for ref in envelope.content_object_refs:
        if not ref.startswith("obj_"):
            continue
        manifest = by_object.get(ref)
        if manifest is None:
            gaps.add(ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value)
            continue
        if manifest.content_kind not in _CAPTURED_EVIDENCE_KINDS:
            gaps.add(ObservationGapCode.CONTENT_UNSELECTED.value)
            continue
        if manifest.content_digest is None or manifest.content_bytes is None:
            gaps.add(ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value)
            continue
        if manifest.redacted:
            gaps.add(ObservationGapCode.CONTENT_REDACTED.value)
        eligible.append(manifest)
    return (
        tuple(sorted(eligible, key=lambda item: item.object_id.encode("ascii"))),
        tuple(sorted(gaps, key=str.encode)),
    )


def _captured_evidence_drafts(
    envelope: ObservationEnvelope,
    *,
    task_id: str,
    manifests: tuple[ObservationContentManifest, ...],
    parents: tuple[str, ...] = (),
) -> tuple[tuple[MaterializedObservationDraft, ...], tuple[str, ...]]:
    drafts: list[MaterializedObservationDraft] = []
    refs: list[str] = []
    for item in manifests:
        source = f"{envelope.source_identity}:captured:{item.object_id}"
        evidence = stable_observation_id(
            kind=IdKind.EVIDENCE,
            task_id=task_id,
            source_identity=source,
            mapping_version=MATERIALIZATION_MAPPING_VERSION,
            role="captured_evidence",
        )
        event = stable_observation_id(
            kind=IdKind.EVENT,
            task_id=task_id,
            source_identity=source,
            mapping_version=MATERIALIZATION_MAPPING_VERSION,
            role="captured_evidence_event",
        )
        refs.append(evidence)
        drafts.append(
            _draft(
                event=event,
                schema_name="evidence_recorded",
                occurred_at=envelope.receipt_time,
                payload=EvidenceRecordedPayload(
                    evidence_id(evidence),
                    EvidenceKind.OTHER,
                    EvidenceImmutability.IMMUTABLE_SNAPSHOT,
                    envelope.receipt_time,
                    captured_object_id=object_id(item.object_id),
                    content_digest=item.content_digest,
                    description=(
                        f"Observation-captured {item.content_kind.value} bytes "
                        f"part={item.part_index + 1}/{item.part_count}"
                    ),
                    digest_binding=EvidenceDigestBinding(
                        subject=EvidenceDigestSubject.BOUNDED_EXCERPT,
                        content_availability=EvidenceContentAvailability.CAPTURED,
                        byte_count=cast(int, item.content_bytes),
                        provenance=EvidenceDigestProvenance.OBSERVATION_CAPTURED,
                    ),
                ),
                parents=parents,
                artifact_refs=(item.object_id,),
                role=(
                    f"captured_{item.content_kind.value}_{item.part_index}_{item.content_digest}"
                ),
            )
        )
    return tuple(drafts), tuple(refs)


def materialize_observation_inspection_snapshot(
    snapshot: ObservationInspectionSnapshot,
    *,
    task_id: str,
) -> MaterializedObservationBatch:
    """Materialize trusted inspection objects without exposing their captured bytes.

    Inspection facts and excerpts are independently encrypted before this seam. The ledger binds
    their exact secret-scanned plaintext digests and object identities, while descriptions remain
    structural so observation intake does not silently become semantic-review egress.
    """

    baseline = coverage_for_channel(PublicationChannel.HOOK_OBSERVED)
    if type(snapshot) is not ObservationInspectionSnapshot:
        return MaterializedObservationBatch(
            (), baseline, PublicationChannel.HOOK_OBSERVED, (), "invalid_inspection_snapshot"
        )
    subject_state = SubjectStateRef(
        described_state=f"observation-inspection:{snapshot.subject_state_digest}"
    )
    candidates = (
        (
            "facts",
            snapshot.facts_object_id,
            snapshot.facts_content_digest,
            snapshot.facts_content_bytes,
            "Observation inspection facts snapshot",
        ),
        (
            "excerpt",
            snapshot.excerpt_object_id,
            snapshot.excerpt_content_digest,
            snapshot.excerpt_content_bytes,
            "Observation inspection bounded excerpt snapshot",
        ),
    )
    drafts: list[MaterializedObservationDraft] = []
    for role, captured_object, content_digest, content_bytes, description in candidates:
        if captured_object is None:
            continue
        source = f"inspection:{snapshot.snapshot_id}:{role}:{captured_object}"
        evidence = stable_observation_id(
            kind=IdKind.EVIDENCE,
            task_id=task_id,
            source_identity=source,
            mapping_version=MATERIALIZATION_MAPPING_VERSION,
            role=f"inspection_{role}",
        )
        event = stable_observation_id(
            kind=IdKind.EVENT,
            task_id=task_id,
            source_identity=source,
            mapping_version=MATERIALIZATION_MAPPING_VERSION,
            role=f"inspection_{role}_event",
        )
        drafts.append(
            _draft(
                event=event,
                schema_name="evidence_recorded",
                occurred_at=snapshot.recorded_at,
                payload=EvidenceRecordedPayload(
                    evidence_id=evidence_id(evidence),
                    evidence_kind=EvidenceKind.OTHER,
                    strength=EvidenceImmutability.IMMUTABLE_SNAPSHOT,
                    observed_at=snapshot.recorded_at,
                    captured_object_id=object_id(captured_object),
                    content_digest=cast(str, content_digest),
                    description=description,
                    subject_state=subject_state,
                    digest_binding=EvidenceDigestBinding(
                        subject=EvidenceDigestSubject.BOUNDED_EXCERPT,
                        content_availability=EvidenceContentAvailability.CAPTURED,
                        byte_count=cast(int, content_bytes),
                        provenance=EvidenceDigestProvenance.OBSERVATION_CAPTURED,
                    ),
                ),
                artifact_refs=(captured_object,),
                role=f"inspection_{role}",
            )
        )
    if not drafts:
        gap = ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value
        coverage = Coverage(
            publication_channels=baseline.publication_channels,
            authorship_assurance=baseline.authorship_assurance,
            artifact_observation=baseline.artifact_observation,
            evidence_immutability=baseline.evidence_immutability,
            ledger_freshness=baseline.ledger_freshness,
            check_types=baseline.check_types,
            known_gaps=tuple(sorted({*baseline.known_gaps, gap}, key=str.encode)),
        )
        return MaterializedObservationBatch(
            (), coverage, PublicationChannel.HOOK_OBSERVED, (gap,), "inspection_content_unavailable"
        )
    inspection_gaps = set(baseline.known_gaps)
    if snapshot.excerpt_redacted:
        inspection_gaps.add(ObservationGapCode.CONTENT_REDACTED.value)
    if snapshot.excerpt_truncated:
        inspection_gaps.add(ObservationGapCode.TRUNCATED_PAYLOAD.value)
    coverage = Coverage(
        publication_channels=(PublicationChannel.HOOK_OBSERVED,),
        authorship_assurance=AuthorshipAssurance.HARNESS_OBSERVED,
        artifact_observation=ArtifactObservation.CONTENT_CAPTURED,
        evidence_immutability=EvidenceImmutability.IMMUTABLE_SNAPSHOT,
        ledger_freshness=baseline.ledger_freshness,
        check_types=baseline.check_types,
        known_gaps=tuple(sorted(inspection_gaps, key=str.encode)),
    )
    return MaterializedObservationBatch(
        tuple(drafts),
        coverage,
        PublicationChannel.HOOK_OBSERVED,
        tuple(sorted(inspection_gaps, key=str.encode)),
        None,
    )


def materialize_observation_envelope(
    envelope: ObservationEnvelope,
    *,
    task_id: str,
    captured_content: tuple[ObservationContentManifest, ...] = (),
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
    captured, gaps = _eligible_captured_content(envelope, captured_content)
    coverage = _coverage_for(envelope, gaps=gaps, content_captured=bool(captured))
    channel = PublicationChannel.HOOK_OBSERVED
    mapping = envelope.cursor.mapping_version or MATERIALIZATION_MAPPING_VERSION
    kind = envelope.event_kind
    # Codex hook ``PostToolUse`` and a completed session-stream tool record
    # (rollout ``function_call_output``, historically exec ``item.completed``)
    # are two observations of the same host call. Normalize the stream form
    # before choosing ledger roles so both sources produce the same
    # action/result batch and therefore the same operation digest.
    completed_stream = stream_event_is_completed_tool(kind, structural)
    if (
        envelope.source is ObservationSource.CODEX_SESSION_STREAM
        and completed_stream
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
        if routine_read and not captured:
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
        captured_drafts, _captured_refs = _captured_evidence_drafts(
            envelope,
            task_id=task_id,
            manifests=captured,
            parents=(event,),
        )
        drafts.extend(captured_drafts)
        return MaterializedObservationBatch(tuple(drafts), coverage, channel, gaps, None)

    if kind == "PostToolUse":
        # A routine read is coalesced only after the host supplied an explicit
        # successful outcome. An outcome-less result must retain its durable
        # action/result identity and folded host_outcome_unavailable gap under
        # ADR-022 decision 12; treating UNKNOWN as a successful read would
        # discard that limitation before the materializer can record it.
        if routine_read and not captured and _post_outcome(structural) is ResultOutcome.SUCCESS:
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
            captured_drafts, _captured_refs = _captured_evidence_drafts(
                envelope,
                task_id=task_id,
                manifests=captured,
                parents=(event,),
            )
            drafts.extend(captured_drafts)
            merged_gaps = tuple(
                sorted({*gaps, ObservationGapCode.UNPAIRED_EVENT.value}, key=str.encode)
            )
            return MaterializedObservationBatch(tuple(drafts), coverage, channel, merged_gaps, None)

        # Linked post: action (idempotent stable IDs from correlation) + result.
        family = _action_kind(tool).value
        action_source = f"pre:{correlation}:{family}"
        action = stable_observation_id(
            kind=IdKind.ACTION,
            task_id=task_id,
            source_identity=action_source,
            mapping_version=MATERIALIZATION_MAPPING_VERSION,
            role="action",
        )
        action_event = stable_observation_id(
            kind=IdKind.EVENT,
            task_id=task_id,
            source_identity=action_source,
            mapping_version=MATERIALIZATION_MAPPING_VERSION,
            role="action_event",
        )
        result_source = f"post:{correlation}:{family}"
        result = stable_observation_id(
            kind=IdKind.RESULT,
            task_id=task_id,
            source_identity=result_source,
            mapping_version=MATERIALIZATION_MAPPING_VERSION,
            role="result",
        )
        result_event = stable_observation_id(
            kind=IdKind.EVENT,
            task_id=task_id,
            source_identity=result_source,
            mapping_version=MATERIALIZATION_MAPPING_VERSION,
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
        captured_drafts, captured_refs = _captured_evidence_drafts(
            envelope,
            task_id=task_id,
            manifests=captured,
            parents=(action_event,),
        )
        drafts.extend(captured_drafts)
        exit_status = _exit_status(structural)
        outcome = _post_outcome(structural)
        if outcome is ResultOutcome.UNKNOWN:
            # The host stated no outcome for this call. The per-call record stays
            # durable, but its coverage names the one standing capability
            # condition so check/receipt fold every such call into a single
            # bounded known gap instead of per-result limitation candidates.
            coverage = Coverage(
                publication_channels=coverage.publication_channels,
                authorship_assurance=coverage.authorship_assurance,
                artifact_observation=coverage.artifact_observation,
                evidence_immutability=coverage.evidence_immutability,
                ledger_freshness=coverage.ledger_freshness,
                check_types=coverage.check_types,
                known_gaps=tuple(
                    sorted({*coverage.known_gaps, HOST_OUTCOME_UNAVAILABLE_GAP}, key=str.encode)
                ),
            )
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
                    evidence_refs=tuple(evidence_id(value) for value in captured_refs),
                ),
                parents=tuple(
                    sorted(
                        (action_event, *(item.draft.event_id for item in captured_drafts)),
                        key=str.encode,
                    )
                ),
                evidence_refs=captured_refs,
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
        captured_drafts, _captured_refs = _captured_evidence_drafts(
            envelope, task_id=task_id, manifests=captured, parents=(event,)
        )
        drafts.extend(captured_drafts)
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
        captured_drafts, _captured_refs = _captured_evidence_drafts(
            envelope, task_id=task_id, manifests=captured, parents=(event,)
        )
        drafts.extend(captured_drafts)
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
        captured_drafts, _captured_refs = _captured_evidence_drafts(
            envelope, task_id=task_id, manifests=captured, parents=(event,)
        )
        drafts.extend(captured_drafts)
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
        captured_drafts, _captured_refs = _captured_evidence_drafts(
            envelope, task_id=task_id, manifests=captured, parents=(event,)
        )
        drafts.extend(captured_drafts)
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
    captured_drafts, _captured_refs = _captured_evidence_drafts(
        envelope, task_id=task_id, manifests=captured, parents=(event,)
    )
    drafts.extend(captured_drafts)
    return MaterializedObservationBatch(tuple(drafts), coverage, channel, gaps, None)


def materialize_observation_outcome_correction(
    envelope: ObservationEnvelope,
    *,
    task_id: str,
    conflict: bool = False,
    target_action_id: str | None = None,
    target_action_event_id: str | None = None,
) -> MaterializedObservationBatch:
    """Append an explicit stream outcome that enriches an earlier UNKNOWN hook result.

    Historical action/result rows stay immutable. The correction is a second,
    source-independent result linked to the same canonical action, so retries are
    idempotent and explicit host failure/success facts remain visible. A replayed
    legacy-mapping operation committed its graph under pre-upgrade record
    identities; ``target_action_id`` (with an optional committed action event as
    causal parent) binds the correction to that exact committed action instead of
    the current canonical one.
    """

    structural = cast(Mapping[str, JsonValue], envelope.structural_payload)
    correlation = _correlation(structural)
    tool = _tool_name(structural)
    coverage = _coverage_for(envelope)
    channel = PublicationChannel.HOOK_OBSERVED
    gaps = tuple(envelope.gap_codes)
    if (
        envelope.source is not ObservationSource.CODEX_SESSION_STREAM
        or not stream_event_is_completed_tool(envelope.event_kind, structural)
        or correlation is None
        or ObservationGapCode.UNPAIRED_EVENT.value in gaps
    ):
        return MaterializedObservationBatch((), coverage, channel, gaps, "no_outcome_correction")
    outcome = _post_outcome(structural)
    if outcome is ResultOutcome.UNKNOWN:
        return MaterializedObservationBatch((), coverage, channel, gaps, "outcome_unknown")

    family = _action_kind(tool).value
    action_source = f"pre:{correlation}:{family}"
    if target_action_id is not None:
        action = action_id(target_action_id)
        action_parents = (
            (event_id(target_action_event_id),) if target_action_event_id is not None else ()
        )
    else:
        action = action_id(
            stable_observation_id(
                kind=IdKind.ACTION,
                task_id=task_id,
                source_identity=action_source,
                mapping_version=MATERIALIZATION_MAPPING_VERSION,
                role="action",
            )
        )
        action_parents = (
            event_id(
                stable_observation_id(
                    kind=IdKind.EVENT,
                    task_id=task_id,
                    source_identity=action_source,
                    mapping_version=MATERIALIZATION_MAPPING_VERSION,
                    role="action_event",
                )
            ),
        )
    exit_status = _exit_status(structural)
    correction_source = (
        f"outcome-correction:{correlation}:{family}:{outcome.value}:"
        f"{exit_status if exit_status is not None else 'none'}"
    )
    correction_role = (
        f"result_correction_{outcome.value}_{exit_status if exit_status is not None else 'none'}"
    )
    result = stable_observation_id(
        kind=IdKind.RESULT,
        task_id=task_id,
        source_identity=correction_source,
        mapping_version=MATERIALIZATION_MAPPING_VERSION,
        role=correction_role,
    )
    result_event = stable_observation_id(
        kind=IdKind.EVENT,
        task_id=task_id,
        source_identity=correction_source,
        mapping_version=MATERIALIZATION_MAPPING_VERSION,
        role=f"{correction_role}_event",
    )
    if conflict:
        coverage = Coverage(
            publication_channels=coverage.publication_channels,
            authorship_assurance=coverage.authorship_assurance,
            artifact_observation=coverage.artifact_observation,
            evidence_immutability=coverage.evidence_immutability,
            ledger_freshness=coverage.ledger_freshness,
            check_types=coverage.check_types,
            known_gaps=tuple(
                sorted(
                    {*coverage.known_gaps, ObservationGapCode.DEDUP_CONFLICT.value},
                    key=str.encode,
                )
            ),
        )
        gaps = tuple(sorted({*gaps, ObservationGapCode.DEDUP_CONFLICT.value}, key=str.encode))
    draft = _draft(
        event=result_event,
        schema_name="result_recorded",
        occurred_at=envelope.receipt_time,
        payload=ResultRecordedPayload(
            result_id(result),
            action,
            outcome,
            exit_status=exit_status,
            summary=(
                f"Observed conflicting corrected result status={outcome.value}"
                if conflict
                else f"Observed corrected result status={outcome.value}"
            ),
        ),
        parents=action_parents,
        role=correction_role,
    )
    return MaterializedObservationBatch((draft,), coverage, channel, gaps, None)


def canonical_logical_identity(envelope: ObservationEnvelope) -> str:
    """Return the canonical logical-observation identity for one envelope.

    Hook ``PostToolUse`` and stream completed-tool copies of the same host
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


def observation_content_identity(envelope: ObservationEnvelope) -> str:
    """Return the phase-scoped identity used to recover captured manifests.

    Ledger action/result identity intentionally collapses hook/stream copies and
    several phases of one host call. Captured content follows only the first
    half of that rule: equivalent same-phase copies share recovery, while
    Pre/Post/unpaired siblings remain isolated so they cannot change one
    another's materialized role sets.
    """

    if type(envelope) is not ObservationEnvelope:
        return _logical_identity_digest(("content", "opaque", "invalid"))
    structural = cast(Mapping[str, JsonValue], envelope.structural_payload)
    kind = envelope.event_kind
    if (
        envelope.source is ObservationSource.CODEX_SESSION_STREAM
        and stream_event_is_completed_tool(kind, structural)
        and _correlation(structural) is not None
    ):
        kind = "PostToolUse"
    phase = (
        f"{kind}:unpaired"
        if kind == "PostToolUse" and ObservationGapCode.UNPAIRED_EVENT.value in envelope.gap_codes
        else kind
    )
    return _logical_identity_digest(
        (
            "content",
            canonical_logical_identity(envelope),
            phase,
        )
    )


def observation_claim_identity(
    envelope: ObservationEnvelope,
    draft_roles: tuple[str, ...],
    *,
    mapping_version: str = MATERIALIZATION_MAPPING_VERSION,
) -> str:
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

    _validate_materialization_mapping_version(mapping_version)
    return _logical_identity_digest(
        (
            "claim",
            mapping_version,
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
    logical_identity: str,
    draft_roles: tuple[str, ...],
    mapping_version: str = MATERIALIZATION_MAPPING_VERSION,
    session_id: str | None = None,
    writer_id: str | None = None,
) -> str:
    """Stable request digest for idempotent observation appends.

    Keyed on the canonical *logical* identity rather than the source-specific
    identity so matching hook/stream copies produce one ledger operation.

    Under the current mapping the digest is task-scoped: the stable event ids it
    commits depend on the task and the source identity only, so the operation
    that owns them must be findable from any later Yoetz session of the same
    task (a workflow reattach rotates the session and writer, #560). The legacy
    session-bound versions still require ``session_id`` and ``writer_id`` so a
    pre-upgrade committed operation keeps its exact replay identity.
    """

    _validate_materialization_mapping_version(mapping_version)
    material: dict[str, JsonValue] = {
        "protocol": "yoetz",
        "kind": "observation_materialize",
        "task_id": task_id,
        "logical_identity": logical_identity,
        "roles": draft_roles,
        "mapping_version": mapping_version,
    }
    if mapping_version in SESSION_BOUND_MAPPING_VERSIONS:
        if type(session_id) is not str or type(writer_id) is not str:
            raise ValueError("session-bound observation mapping requires session and writer")
        material["session_id"] = session_id
        material["writer_id"] = writer_id
    elif session_id is not None or writer_id is not None:
        raise ValueError("task-scoped observation mapping digest takes no session or writer")
    return request_digest(JsonObject(material))


def _validate_materialization_mapping_version(mapping_version: str) -> None:
    if type(mapping_version) is not str or mapping_version not in {
        MATERIALIZATION_MAPPING_VERSION,
        *MATERIALIZATION_LEGACY_MAPPING_VERSIONS,
    }:
        raise ValueError("unsupported observation materialization mapping version")


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
