"""Pure frozen-authority builder for privacy-selected AI-powered review cases.

Constructs ``SemanticCase`` / ``ReviewPacket`` from the already-frozen check case,
pinned local findings/bases, and the active ``ReviewSelectionPolicy``.

This module is deliberately capability-free: no Git, filesystem, network, transcript,
environment, database, or provider access. Captured bytes, when present, arrive as frozen
service-authenticated values; missing material becomes an omission.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Final, Literal, cast

from yoetz.application.check import (
    CheckScope,
    case_coverage,
    run_deterministic_policies,
)
from yoetz.application.missing_for_assessment import supplied_since
from yoetz.domain.events import (
    ActionKind,
    ClaimRecordedPayload,
    ClaimRecordedPayloadV1_1,
    DecisionRecordedPayload,
    EvidenceContentAvailability,
    EvidenceDigestBinding,
    EvidenceDigestProvenance,
    EvidenceDigestSubject,
    EvidenceImmutability,
    EvidenceKind,
    EvidenceRecordedPayload,
    ObligationPublishedPayload,
    ResultOutcome,
    encode_payload,
)
from yoetz.domain.findings import Finding, FindingKind, FindingOrigin
from yoetz.domain.observation import (
    ObservationContentKind,
    ObservationContentManifest,
    ObservationSource,
)
from yoetz.domain.observation_profiles import ORDINARY_CONTENT_CAPTURE_PROFILE_IDS
from yoetz.domain.privacy import (
    MAX_EGRESS_ENVELOPE_BYTES,
    REVIEW_PACKET_ITEM_ID,
    AuthorizationScope,
    CandidateContext,
    CandidateContextItem,
    EgressChannel,
    ProviderBinding,
    ReviewContextProfile,
    ReviewSelectionPolicy,
)
from yoetz.domain.receipts import (
    SEMANTIC_CASE_CONTENT_OVER_ITEM_LIMIT_GAP,
    SEMANTIC_CASE_FINDING_REFS_OVER_LIMIT_GAP,
    SEMANTIC_PRIOR_FINDINGS_OVER_LIMIT_GAP,
)
from yoetz.domain.values import (
    SubjectStateRelation,
    session_id,
    task_id,
    validate_commitment,
    validate_sha256_digest,
)
from yoetz.domain.values import (
    evidence_id as validate_evidence_id,
)
from yoetz.domain.values import (
    finding_id as finding_id_value,
)
from yoetz.kernel.claims import effective_claim_items
from yoetz.kernel.deterministic_checks import (
    DeterministicAssessment,
    DeterministicCase,
    FrozenHistoryEvent,
)
from yoetz.kernel.lineage import LineageEvaluation
from yoetz.kernel.projections import (
    EvidenceProjectionRecord,
    FindingProjectionRecord,
    ProjectionState,
)
from yoetz.ports.objects import ObjectKind, ObjectRef
from yoetz.ports.semantic import (
    MAX_PRIOR_FINDING_ITEMS,
    MAX_SEMANTIC_CASE_ITEMS,
    MAX_SEMANTIC_ITEM_SUBJECT_REFS,
    ChangeObservation,
    ExcerptDigestProvenance,
    ReviewAssessment,
    ReviewAssessmentSkipped,
    ReviewOmission,
    ReviewPacket,
    SemanticCase,
    SemanticCaseItem,
    TargetedExcerptRef,
    project_review_assessment,
)
from yoetz.protocol.canonical import (
    JsonValue,
    canonical_digest,
    canonical_encode,
    strict_json_parse,
)
from yoetz.protocol.coverage import LedgerFreshness, coverage_to_json
from yoetz.protocol.models import (
    MAX_REVIEW_TEXT_BYTES,
    MAX_REVIEW_TIMELINE_ITEMS,
    MAX_SEMANTIC_CASE_BYTES,
    MAX_SEMANTIC_ITEM_BYTES,
    DataCategory,
)

__all__ = [
    "CapturedContentScope",
    "CapturedSemanticContent",
    "MAX_CAPTURED_SEMANTIC_CONTENT_BYTES",
    "MAX_CAPTURED_SEMANTIC_CONTENT_PARTS",
    "MAX_CAPTURED_SEMANTIC_INPUT_BYTES",
    "LineageSemanticCapacityExceeded",
    "OVER_CASE_ITEM_LIMIT_REASON",
    "REVIEW_PACKET_ITEM_ID",
    "SEMANTIC_REVIEW_PURPOSE",
    "assemble_filtered_review_packet",
    "build_semantic_case",
    "captured_edit_paths",
    "review_selection_digest",
    "repair_evidence_refs",
    "SemanticPacketView",
    "semantic_case_packet_view",
    "semantic_case_to_candidate_context",
    "semantic_case_to_prepared_payload",
]

SEMANTIC_REVIEW_PURPOSE: Final = "semantic-review"
# Marker reason for content the case admitted and then could not carry whole. Distinct from the
# `not_selected` omission vocabulary, which means the selection policy declined to carry it.
OVER_CASE_ITEM_LIMIT_REASON: Final = "over_case_item_limit"
# Version 2 (issue #907) carries each item's recording order and excerpt freshness marks, and
# lists items in case order (section, then recording order) instead of by opaque item id.
_PACKET_SCHEMA: Final = "yoetz.review-packet-case/2"
# Long output keeps its head and its tail: the command and first failure sit at the top, and the
# test/lint summary line sits at the bottom. The marker says how much was cut and where.
_ELISION_MARKER: Final = "\n[yoetz: {elided} of {total} bytes elided here; head and tail kept]\n"
# Below this many bytes per side a head-and-tail split is not worth its marker; a very narrow
# custom excerpt bound keeps the head instead.
_MIN_HEAD_TAIL_SIDE_BYTES: Final = 64
# Strings at or below this size are identifiers, digests or enum tokens, never prose to clip.
_MIN_CLIPPABLE_PROSE_BYTES: Final = 256
# The gateway compares the whole prepared provider document with the channel byte ceiling, whose
# schema maximum is MAX_SEMANTIC_CASE_BYTES (and the bytes/4 token estimate reaches the same
# ceiling). Selection plans below it so a larger excerpt can never turn a reviewable case into a
# policy denial; the reserve absorbs privacy redaction markers that can differ in length. The
# same reserve applies below a narrower owner ceiling.
_PLANNING_RESERVE_BYTES: Final = 4_096
_PREPARED_PAYLOAD_PLANNING_BYTES: Final = MAX_SEMANTIC_CASE_BYTES - _PLANNING_RESERVE_BYTES
_PLANNING_GRANULARITY_BYTES: Final = 1_024
_PACKET_ID_LIST_KEYS: Final = (
    "goal_item_ids",
    "obligation_item_ids",
    "claim_item_ids",
    "decision_item_ids",
    "prior_finding_item_ids",
    "timeline_item_ids",
)
# The prior-findings section (issue #905) has its own bounds, outside the timeline's 64 rows.
MAX_PRIOR_FINDINGS: Final = 8
MAX_PRIOR_FINDING_SECTION_BYTES: Final = 48 * 1024
# Refs listed per structural row; the full counts travel beside them.
_MAX_PRIOR_FINDING_LISTED_REFS: Final = 8
_CANONICAL_PACKS: Final = ("research-evidence/0.1.0", "work-integrity/0.1.0")
_QUESTION_SET: Final = (
    "Does the supplied packet contain a material discrepancy against the goal and obligations?",
    "If so, which case-bound refs support the discrepancy?",
    "What is the smallest next step the main agent should take?",
)

type _Section = Literal[
    "goal",
    "obligation",
    "claim",
    "decision",
    "prior_finding",
    "timeline",
    "deterministic_summary",
    "deterministic_detail",
    "excerpt",
]
type _SourceKind = Literal[
    "task",
    "obligation",
    "claim",
    "decision",
    "action",
    "result",
    "evidence",
    "finding",
    "test",
    "failure",
    "diff",
    "command",
    "repository",
]
type _ExcerptKind = Literal["evidence", "test", "failure", "diff", "command", "repository"]
type _OmissionReason = Literal[
    "not_recorded", "not_selected", "withheld_by_policy", "redacted_never_send"
]

# The observation ingest bound is intentionally larger than one AI-powered review case item. The service-side
# resolver authenticates a complete retained chunk here, after which the selection policy clips it
# to its own excerpt/item/total limits. Keeping this bound below the ordinary object-store limit
# prevents a malformed captured-content wrapper from becoming an unbounded AI-powered review input.
MAX_CAPTURED_SEMANTIC_CONTENT_BYTES: Final = 512 * 1024
_CAPTURED_CONTENT_MEDIA_TYPE: Final = "application/vnd.yoetz.observation-content+json"
_CAPTURED_CONTENT_KINDS: Final = frozenset(
    {
        ObservationContentKind.TOOL_OUTPUT,
        ObservationContentKind.CHANGED_FILE,
        ObservationContentKind.WORKSPACE_DIFF,
    }
)
# The resolver and pure builder share the closed profile vocabulary. The builder still
# treats its scope as a service-authenticated assertion; it does not discover consent.
#
# Codex's original observation consent predates the opt-in Claude/Cursor content profiles.  The
# source token below is an internal scope label for that historical, profileless grant; it is not a
# user-selectable content profile and is never accepted by the local consent/profile adapters.
_CODEX_HISTORICAL_CAPTURE_SCOPE: Final = ObservationSource.CODEX_HOOK.value
_AUTHORIZED_CAPTURE_PROFILES: Final = frozenset(
    {*ORDINARY_CONTENT_CAPTURE_PROFILE_IDS, _CODEX_HISTORICAL_CAPTURE_SCOPE}
)
MAX_CAPTURED_SEMANTIC_CONTENT_PARTS: Final = 64
MAX_CAPTURED_SEMANTIC_INPUT_BYTES: Final = 2 * MAX_CAPTURED_SEMANTIC_CONTENT_BYTES
_CAPTURE_GAP_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_]{0,127}$", re.ASCII)


@dataclass(frozen=True, slots=True)
class CapturedContentScope:
    """Current service-authorized boundary for native captured AI-powered review content.

    The scope is assembled by the service after it checks the active local consent arm. The pure
    case builder accepts it as a frozen assertion and still rechecks every excerpt against the
    projection and scope; it never discovers consent or reads an object itself.
    """

    task_id: str
    session_id: str
    workspace_commitment: str
    authorized_profiles: tuple[str, ...]
    # Each entry is a service-derived evidence ref → phase identity binding.  A
    # caller-supplied phase digest is useful only when it agrees with the durable
    # evidence/envelope identity that the resolver derived before opening bytes.
    phase_bindings: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "task_id", task_id(self.task_id))
            object.__setattr__(self, "session_id", session_id(self.session_id))
            object.__setattr__(
                self,
                "workspace_commitment",
                validate_commitment(self.workspace_commitment),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("semantic_case_capture_scope_invalid") from exc
        if type(self.authorized_profiles) is not tuple or not self.authorized_profiles:
            raise ValueError("semantic_case_capture_scope_invalid")
        if any(profile not in _AUTHORIZED_CAPTURE_PROFILES for profile in self.authorized_profiles):
            raise ValueError("semantic_case_capture_scope_invalid")
        if self.authorized_profiles != tuple(sorted(set(self.authorized_profiles), key=str.encode)):
            raise ValueError("semantic_case_capture_scope_invalid")
        if (
            type(self.phase_bindings) is not tuple
            or len(self.phase_bindings) > MAX_CAPTURED_SEMANTIC_CONTENT_PARTS
        ):
            raise ValueError("semantic_case_capture_scope_invalid")
        normalized_bindings: list[tuple[str, str]] = []
        for binding in self.phase_bindings:
            if type(binding) is not tuple or len(binding) != 2:
                raise ValueError("semantic_case_capture_scope_invalid")
            try:
                ref = validate_evidence_id(binding[0])
                phase = validate_sha256_digest(binding[1])
            except (TypeError, ValueError) as exc:
                raise ValueError("semantic_case_capture_scope_invalid") from exc
            normalized_bindings.append((str(ref), phase))
        if tuple(normalized_bindings) != tuple(
            sorted(set(normalized_bindings), key=lambda item: item[0].encode("ascii"))
        ):
            raise ValueError("semantic_case_capture_scope_invalid")


@dataclass(frozen=True, slots=True)
class CapturedSemanticContent:
    """One service-authenticated, complete observation-content part.

    ``content`` is the decoded inner bytes, never the encrypted object wrapper. The resolver owns
    object authentication and supplies the exact ``ObjectRef``/manifest pair. The builder then
    verifies the pair and maps only content whose object is already represented by a case-bound
    observation evidence row.
    """

    object_ref: ObjectRef
    manifest: ObservationContentManifest
    content: bytes
    task_id: str
    session_id: str
    workspace_commitment: str
    phase_identity: str
    capture_profile: str
    capture_gaps: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.object_ref) is not ObjectRef
            or type(self.manifest) is not ObservationContentManifest
        ):
            raise ValueError("semantic_case_captured_content_invalid")
        if (
            self.manifest.content_kind not in _CAPTURED_CONTENT_KINDS
            or self.manifest.envelope_digest is None
            or self.manifest.content_digest is None
            or self.manifest.content_bytes is None
            or self.manifest.correlation_identity is None
            or self.manifest.source_commitment is None
            or type(self.manifest.redacted) is not bool
        ):
            raise ValueError("semantic_case_captured_content_invalid")
        if (
            self.object_ref.metadata.kind is not ObjectKind.CAPTURED_CONTENT
            or self.object_ref.metadata.media_type != _CAPTURED_CONTENT_MEDIA_TYPE
            or self.object_ref.object_id != self.manifest.object_id
            or self.object_ref.envelope_digest != self.manifest.envelope_digest
        ):
            raise ValueError("semantic_case_captured_content_invalid")
        if (
            type(self.content) is not bytes
            or not 1 <= len(self.content) <= MAX_CAPTURED_SEMANTIC_CONTENT_BYTES
        ):
            raise ValueError("semantic_case_captured_content_invalid")
        try:
            self.content.decode("utf-8", errors="strict")
            actual_digest = "sha256:" + hashlib.sha256(self.content).hexdigest()
            object.__setattr__(self, "task_id", task_id(self.task_id))
            object.__setattr__(self, "session_id", session_id(self.session_id))
            object.__setattr__(
                self,
                "workspace_commitment",
                validate_commitment(self.workspace_commitment),
            )
            object.__setattr__(
                self,
                "phase_identity",
                validate_sha256_digest(self.phase_identity),
            )
        except (TypeError, ValueError, UnicodeDecodeError) as exc:
            raise ValueError("semantic_case_captured_content_invalid") from exc
        if self.object_ref.metadata.task_id != self.task_id:
            raise ValueError("semantic_case_captured_content_invalid")
        if self.manifest.content_digest != actual_digest or self.manifest.content_bytes != len(
            self.content
        ):
            raise ValueError("semantic_case_captured_content_invalid")
        if (
            type(self.capture_profile) is not str
            or self.capture_profile not in _AUTHORIZED_CAPTURE_PROFILES
        ):
            raise ValueError("semantic_case_captured_content_invalid")
        if type(self.capture_gaps) is not tuple or len(self.capture_gaps) > 16:
            raise ValueError("semantic_case_captured_content_invalid")
        if any(
            type(gap) is not str or _CAPTURE_GAP_PATTERN.fullmatch(gap) is None
            for gap in self.capture_gaps
        ) or self.capture_gaps != tuple(sorted(set(self.capture_gaps), key=str.encode)):
            raise ValueError("semantic_case_captured_content_invalid")


@dataclass(frozen=True, slots=True)
class _CapturedGroup:
    evidence_refs: tuple[str, ...]
    content: bytes
    source_kind: _ExcerptKind
    digest_provenance: ExcerptDigestProvenance
    capture_gaps: tuple[str, ...]


_EVIDENCE_EXCERPT_KIND: Final[Mapping[EvidenceKind, _ExcerptKind]] = {
    EvidenceKind.ARTIFACT: "evidence",
    EvidenceKind.COMMAND_OUTPUT: "command",
    EvidenceKind.TEST_RESULT: "test",
    EvidenceKind.RESEARCH_SOURCE: "repository",
    EvidenceKind.IMPORT_REPORT: "evidence",
    EvidenceKind.OTHER: "evidence",
}
_HISTORY_KIND: Final[Mapping[str, tuple[_SourceKind, DataCategory]]] = {
    "action_recorded": ("action", DataCategory.COMMAND_METADATA),
    "check_recorded": ("finding", DataCategory.BOUNDED_STRUCTURAL_METADATA),
    "claim_recorded": ("claim", DataCategory.CLAIM_TEXT),
    "decision_recorded": ("decision", DataCategory.DECISION_EXCERPT),
    "evidence_recorded": ("evidence", DataCategory.EVIDENCE_EXCERPT),
    "finding_recorded": ("finding", DataCategory.FINDING_SUMMARY),
    "obligation_published": ("obligation", DataCategory.OBLIGATION_TEXT),
    "plan_published": ("task", DataCategory.TASK_DESCRIPTION),
    "plan_revised": ("task", DataCategory.TASK_DESCRIPTION),
    "response_recorded": ("finding", DataCategory.FINDING_SUMMARY),
    "result_recorded": ("result", DataCategory.COMMAND_METADATA),
}


def review_selection_digest(selection: ReviewSelectionPolicy) -> str:
    """Digest the closed review-selection value for case/provenance binding."""

    if type(selection) is not ReviewSelectionPolicy:
        raise TypeError("review_selection_invalid")
    return canonical_digest(
        cast(
            JsonValue,
            {
                "excerpt_kinds": list(selection.excerpt_kinds),
                "include_exact_command_text": selection.include_exact_command_text,
                "include_finding_prose": selection.include_finding_prose,
                "max_assessments": selection.max_assessments,
                "max_change_observations": selection.max_change_observations,
                "max_excerpt_bytes": selection.max_excerpt_bytes,
                "max_excerpts": selection.max_excerpts,
                "max_omissions": selection.max_omissions,
                "max_timeline_items": selection.max_timeline_items,
                "max_total_excerpt_bytes": selection.max_total_excerpt_bytes,
                "relevance": selection.relevance,
                "schema": "yoetz.review-selection/1",
                "sections": list(selection.sections),
            },
        )
    )


def _utf8(text: str, *, maximum: int = MAX_SEMANTIC_ITEM_BYTES) -> bytes:
    encoded = text.encode("utf-8")
    if not 1 <= len(encoded) <= maximum:
        raise ValueError("semantic_case_content_invalid")
    return encoded


def _elision_marker(elided: int, total: int) -> str:
    return _ELISION_MARKER.format(elided=elided, total=total)


def _head_tail(raw: bytes, limit: int) -> str:
    """Keep the head and the tail of ``raw`` within ``limit`` UTF-8 bytes and mark the cut.

    A test, build or lint run prints its verdict last, so a head-only clip dropped exactly the
    line a reviewer needs (issue #907). The middle goes instead, and the marker says how many
    bytes of how many were elided. A bound too small to hold a marker keeps the head only.
    """

    total = len(raw)
    if total <= limit:
        return raw.decode("utf-8")
    available = limit - len(_elision_marker(total, total).encode("utf-8"))
    if available < 2 * _MIN_HEAD_TAIL_SIDE_BYTES:
        return raw[:limit].decode("utf-8", errors="ignore")
    best = ""
    for _attempt in range(4):
        head_bytes = available // 2
        head = raw[:head_bytes].decode("utf-8", errors="ignore")
        tail = raw[total - (available - head_bytes) :].decode("utf-8", errors="ignore")
        kept = len(head.encode("utf-8")) + len(tail.encode("utf-8"))
        text = head + _elision_marker(total - kept, total) + tail
        size = len(text.encode("utf-8"))
        if size <= limit and size > len(best.encode("utf-8")):
            best = text
        if size == limit:
            break
        # The marker's digit count depends on what was elided; settle on the exact fill.
        available += limit - size
    if not best:
        return raw[:limit].decode("utf-8", errors="ignore")
    return best


def _content_item(
    *,
    item_id: str,
    section: _Section,
    category: DataCategory,
    source_kind: _SourceKind,
    source_ref: str,
    linked_subject_refs: tuple[str, ...],
    occurred_order: int,
    text: str,
    over_limit: set[str] | None = None,
    limit: int = MAX_REVIEW_TEXT_BYTES,
    latest_for: Literal["path", "command"] | None = None,
    superseded_by: tuple[str, ...] = (),
) -> SemanticCaseItem:
    # Bound by UTF-8 bytes, not characters — multi-byte prose must not raise.
    limit = min(limit, MAX_SEMANTIC_ITEM_BYTES)
    raw = text.encode("utf-8")
    if len(raw) > limit:
        # Publish-side prose accepts up to MAX_PROSE_CHARS, which is twice what one structural
        # case item can carry. Silent truncation here is what made a 5 KB evidence description
        # publish cleanly and then reach the reviewer as a shortened fragment with nothing saying
        # so (issue #177). The cut keeps both ends and is marked in the text (issue #907).
        raw = _head_tail(raw, limit).encode("utf-8")
        if over_limit is not None:
            over_limit.add(item_id)
    if not raw:
        raise ValueError("semantic_case_content_invalid")
    content = _utf8(raw.decode("utf-8"), maximum=limit)
    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    return SemanticCaseItem(
        item_id=item_id,
        section=section,
        category=category,
        source_kind=source_kind,
        source_ref=source_ref,
        linked_subject_refs=linked_subject_refs,
        occurred_order=occurred_order,
        content=content,
        content_bytes=len(content),
        content_digest=digest,
        latest_for=latest_for,
        superseded_by=superseded_by,
    )


def _structural_json(value: Mapping[str, JsonValue]) -> str:
    # canonical_encode emits UTF-8 and does not escape non-ASCII, so these must be decoded as
    # UTF-8. Decoding as ASCII meant a single em dash, curly quote or accented character anywhere
    # in the ledger raised UnicodeDecodeError while building the case — surfacing as
    # coordinator_failure with no AI-powered review at all. Agents write such characters constantly.
    return canonical_encode(cast(JsonValue, dict(value))).decode("utf-8")


type _BoundedFit = Literal["whole", "clipped", "replaced"]


def _longest_prose_leaf(
    value: JsonValue, path: tuple[str | int, ...] = ()
) -> tuple[tuple[str | int, ...], int]:
    """Return the path and UTF-8 size of the longest string leaf (keys are never candidates)."""

    best: tuple[tuple[str | int, ...], int] = ((), 0)
    if isinstance(value, str):
        return path, len(value.encode("utf-8"))
    if isinstance(value, Mapping):
        for key in sorted(cast(Mapping[str, JsonValue], value), key=str.encode):
            found = _longest_prose_leaf(cast(Mapping[str, JsonValue], value)[key], (*path, key))
            if found[1] > best[1]:
                best = found
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(cast(Sequence[JsonValue], value)):
            found = _longest_prose_leaf(child, (*path, index))
            if found[1] > best[1]:
                best = found
    return best


def _replace_leaf(value: JsonValue, path: tuple[str | int, ...], leaf: str) -> JsonValue:
    if not path:
        return leaf
    head, rest = path[0], path[1:]
    if isinstance(value, Mapping) and type(head) is str:
        source = cast(Mapping[str, JsonValue], value)
        return {
            key: (_replace_leaf(child, rest, leaf) if key == head else child)
            for key, child in source.items()
        }
    if isinstance(value, (list, tuple)) and type(head) is int:
        return [
            _replace_leaf(child, rest, leaf) if index == head else child
            for index, child in enumerate(cast(Sequence[JsonValue], value))
        ]
    raise ValueError("semantic_case_content_invalid")


def _clip_json_prose(value: JsonValue, limit: int) -> JsonValue | None:
    """Shorten the longest prose strings, head and tail kept, until the canonical JSON fits.

    Identifiers, digests and enum tokens are short and never clipped. ``None`` means the payload
    cannot fit even with all of its prose at the minimum clip, so the caller must fall back to
    the digest-only omission marker.
    """

    current = value
    for _attempt in range(64):
        encoded = canonical_encode(current)
        if len(encoded) <= limit:
            return current
        path, size = _longest_prose_leaf(current)
        if size <= _MIN_CLIPPABLE_PROSE_BYTES:
            return None
        leaf: JsonValue = current
        for step in path:
            leaf = (
                cast(Mapping[str, JsonValue], leaf)[step]
                if type(step) is str
                else cast(Sequence[JsonValue], leaf)[cast(int, step)]
            )
        raw = cast(str, leaf).encode("utf-8")
        target = max(_MIN_CLIPPABLE_PROSE_BYTES, size - (len(encoded) - limit))
        clipped = _head_tail(raw, target)
        if len(clipped.encode("utf-8")) >= size:
            clipped = raw[:target].decode("utf-8", errors="ignore")
        current = _replace_leaf(current, path, clipped)
    return None


def _bounded_json(value: Mapping[str, JsonValue]) -> tuple[str, _BoundedFit]:
    encoded = canonical_encode(cast(JsonValue, dict(value)))
    if len(encoded) <= MAX_REVIEW_TEXT_BYTES:
        return encoded.decode("utf-8"), "whole"
    # An oversized plan, obligation, claim or decision keeps its structure and every identifier;
    # only its longest prose fields are clipped, with the cut marked in the text (issue #907).
    # Replacing the whole payload by a digest left the reviewer with nothing to read.
    clipped = _clip_json_prose(cast(JsonValue, dict(value)), MAX_REVIEW_TEXT_BYTES)
    if clipped is not None:
        return canonical_encode(clipped).decode("utf-8"), "clipped"
    # `not_selected` read as a selection-policy choice, indistinguishable from a section the
    # profile declined to carry. The payload was in fact admitted and then dropped for size, and
    # the reviewer needs to know which of the two happened (issue #177).
    marker = {
        "content_digest": canonical_digest(cast(JsonValue, dict(value))),
        "original_bytes": len(encoded),
        "reason": OVER_CASE_ITEM_LIMIT_REASON,
        "schema": "yoetz.bounded-content-omission/1",
    }
    return _structural_json(marker), "replaced"


class LineageSemanticCapacityExceeded(ValueError):
    """Recorded lineage cannot be carried as complete semantic items.

    One child or gap fact is larger than a single item, or the partitioned set would exceed the
    timeline item budget or the complete case byte budget, including retained parent content.
    Callers map this to a pre-dispatch capacity outcome. Partial JSON is never a substitute.
    """


_LINEAGE_INPUT_SCHEMA: Final = "yoetz.lineage-semantic-input/1"
_LINEAGE_INPUT_PART_SCHEMA: Final = "yoetz.lineage-semantic-input/2"
# One part per timeline slot. Fan-out admission is also capped at 64 children.
_MAX_LINEAGE_PARTS: Final = MAX_REVIEW_TIMELINE_ITEMS


def _lineage_rows(
    evaluation: LineageEvaluation,
) -> tuple[list[dict[str, JsonValue]], list[dict[str, JsonValue]]]:
    snapshots = {item.child_task_id: item for item in evaluation.snapshots}
    children: list[dict[str, JsonValue]] = []
    for rollup in evaluation.children:
        snapshot = snapshots.get(rollup.child_task_id)
        if snapshot is None:
            # Omitting a child would make the semantic case claim less source than the check
            # evaluated. Fail closed at case construction instead.
            raise ValueError("lineage_semantic_input_invalid")
        children.append(
            {
                "acceptance": snapshot.acceptance.value,
                "blocking_conditions": list(rollup.blockers),
                "child_check_id": snapshot.child_check_id,
                "child_check_subject_frontier": (
                    None
                    if snapshot.child_check_subject_frontier is None
                    else dict(snapshot.child_check_subject_frontier.as_wire())
                ),
                "child_frontier": (
                    None
                    if snapshot.child_frontier is None
                    else dict(snapshot.child_frontier.as_wire())
                ),
                "child_receipt_id": snapshot.child_receipt_id,
                "child_task_id": str(rollup.child_task_id),
                "coverage_gaps": list(snapshot.coverage.known_gaps),
                "finding_ids": [str(value) for value in rollup.finding_ids],
                "freshness": rollup.freshness,
                "lineage_authority_revision": snapshot.lineage_authority_revision,
                "origin": snapshot.origin.value,
                "provenance_restrictions": list(snapshot.provenance_restrictions),
                "read_gap_reasons": list(snapshot.read_gap_reasons),
                "rollup_state": rollup.state.value,
                "session_health": snapshot.session_health.value,
                "work_state": snapshot.work_state.value,
            }
        )
    gaps: list[dict[str, JsonValue]] = []
    for gap in evaluation.gaps:
        gaps.append(
            cast(
                dict[str, JsonValue],
                {
                    "code": gap.code,
                    "child_task_id": None if gap.child_task_id is None else str(gap.child_task_id),
                    "finding_ids": [str(value) for value in gap.finding_ids],
                    "manifest_event_id": (
                        None if gap.manifest_event_id is None else str(gap.manifest_event_id)
                    ),
                },
            )
        )
    return children, gaps


def _lineage_encode(body: Mapping[str, JsonValue]) -> bytes:
    return canonical_encode(cast(JsonValue, dict(body)))


def _lineage_v1_body(
    children: Sequence[Mapping[str, JsonValue]],
    gaps: Sequence[Mapping[str, JsonValue]],
    manifest_digest: str | None,
) -> dict[str, JsonValue]:
    return {
        "children": list(children),
        "gaps": list(gaps),
        "manifest_digest": manifest_digest,
        "schema": _LINEAGE_INPUT_SCHEMA,
    }


def _lineage_v2_body(
    children: Sequence[Mapping[str, JsonValue]],
    gaps: Sequence[Mapping[str, JsonValue]],
    *,
    manifest_digest: str | None,
    part_index: int,
    part_count: int,
    child_count: int,
    gap_count: int,
) -> dict[str, JsonValue]:
    return {
        "child_count": child_count,
        "children": list(children),
        "gap_count": gap_count,
        "gaps": list(gaps),
        "manifest_digest": manifest_digest,
        "part_count": part_count,
        "part_index": part_index,
        "schema": _LINEAGE_INPUT_PART_SCHEMA,
    }


def _lineage_part_bytes(
    children: Sequence[Mapping[str, JsonValue]],
    gaps: Sequence[Mapping[str, JsonValue]],
    *,
    manifest_digest: str | None,
    part_index: int,
    part_count: int,
    child_count: int,
    gap_count: int,
) -> bytes:
    return _lineage_encode(
        _lineage_v2_body(
            children,
            gaps,
            manifest_digest=manifest_digest,
            part_index=part_index,
            part_count=part_count,
            child_count=child_count,
            gap_count=gap_count,
        )
    )


def _lineage_partition(
    children: Sequence[Mapping[str, JsonValue]],
    gaps: Sequence[Mapping[str, JsonValue]],
    manifest_digest: str | None,
) -> tuple[bytes, ...]:
    """Split children and gaps into complete documents that each fit one item.

    Fit is measured against the widest part header (``part_count`` at the timeline cap) so the
    final encoding with the real count cannot grow past the item bound.
    """

    child_count = len(children)
    gap_count = len(gaps)
    child_at = 0
    gap_at = 0
    slices: list[tuple[int, int, int, int]] = []
    while child_at < child_count or gap_at < gap_count:
        if len(slices) >= _MAX_LINEAGE_PARTS:
            raise LineageSemanticCapacityExceeded("lineage_semantic_input_too_large")
        part_index = len(slices)
        child_take = 0
        if child_at < child_count:
            low, high = 1, child_count - child_at
            if (
                len(
                    _lineage_part_bytes(
                        children[child_at : child_at + 1],
                        (),
                        manifest_digest=manifest_digest,
                        part_index=part_index,
                        part_count=_MAX_LINEAGE_PARTS,
                        child_count=child_count,
                        gap_count=gap_count,
                    )
                )
                > MAX_SEMANTIC_ITEM_BYTES
            ):
                raise LineageSemanticCapacityExceeded("lineage_semantic_input_too_large")
            while low < high:
                mid = (low + high + 1) // 2
                encoded = _lineage_part_bytes(
                    children[child_at : child_at + mid],
                    (),
                    manifest_digest=manifest_digest,
                    part_index=part_index,
                    part_count=_MAX_LINEAGE_PARTS,
                    child_count=child_count,
                    gap_count=gap_count,
                )
                if len(encoded) <= MAX_SEMANTIC_ITEM_BYTES:
                    low = mid
                else:
                    high = mid - 1
            child_take = low
        gap_take = 0
        if gap_at < gap_count:
            alone = _lineage_part_bytes(
                children[child_at : child_at + child_take],
                gaps[gap_at : gap_at + 1],
                manifest_digest=manifest_digest,
                part_index=part_index,
                part_count=_MAX_LINEAGE_PARTS,
                child_count=child_count,
                gap_count=gap_count,
            )
            if len(alone) <= MAX_SEMANTIC_ITEM_BYTES:
                low, high = 1, gap_count - gap_at
                while low < high:
                    mid = (low + high + 1) // 2
                    encoded = _lineage_part_bytes(
                        children[child_at : child_at + child_take],
                        gaps[gap_at : gap_at + mid],
                        manifest_digest=manifest_digest,
                        part_index=part_index,
                        part_count=_MAX_LINEAGE_PARTS,
                        child_count=child_count,
                        gap_count=gap_count,
                    )
                    if len(encoded) <= MAX_SEMANTIC_ITEM_BYTES:
                        low = mid
                    else:
                        high = mid - 1
                gap_take = low
            elif child_take == 0:
                raise LineageSemanticCapacityExceeded("lineage_semantic_input_too_large")
        if child_take == 0 and gap_take == 0:
            raise LineageSemanticCapacityExceeded("lineage_semantic_input_too_large")
        slices.append((child_at, child_at + child_take, gap_at, gap_at + gap_take))
        child_at += child_take
        gap_at += gap_take
    part_count = len(slices)
    encoded_parts: list[bytes] = []
    for part_index, (child_start, child_end, gap_start, gap_end) in enumerate(slices):
        encoded = _lineage_part_bytes(
            children[child_start:child_end],
            gaps[gap_start:gap_end],
            manifest_digest=manifest_digest,
            part_index=part_index,
            part_count=part_count,
            child_count=child_count,
            gap_count=gap_count,
        )
        if len(encoded) > MAX_SEMANTIC_ITEM_BYTES:
            raise LineageSemanticCapacityExceeded("lineage_semantic_input_too_large")
        encoded_parts.append(encoded)
    return tuple(encoded_parts)


def _lineage_structural_item(item_id: str, occurred_order: int, encoded: bytes) -> SemanticCaseItem:
    # Structural lineage is not review prose. ``_content_item`` cuts text at the 4 KiB review
    # bound, which would publish a partial JSON document the 16 KiB item check had accepted.
    if not 1 <= len(encoded) <= MAX_SEMANTIC_ITEM_BYTES:
        raise LineageSemanticCapacityExceeded("lineage_semantic_input_too_large")
    digest = "sha256:" + hashlib.sha256(encoded).hexdigest()
    return SemanticCaseItem(
        item_id=item_id,
        section="timeline",
        category=DataCategory.BOUNDED_STRUCTURAL_METADATA,
        source_kind="task",
        source_ref="lineage",
        linked_subject_refs=(),
        occurred_order=occurred_order,
        content=encoded,
        content_bytes=len(encoded),
        content_digest=digest,
    )


def _lineage_semantic_items(evaluation: LineageEvaluation) -> tuple[SemanticCaseItem, ...]:
    """Encode recorded lineage as one or more complete structural items.

    A single ``yoetz.lineage-semantic-input/1`` document is used when it fits. Larger legal
    fan-out is split into ``yoetz.lineage-semantic-input/2`` parts that together retain every
    child and gap identity plus the shared manifest digest. Child prose never enters the case.
    """

    children, gaps = _lineage_rows(evaluation)
    single = _lineage_encode(_lineage_v1_body(children, gaps, evaluation.manifest_digest))
    if len(single) <= MAX_SEMANTIC_ITEM_BYTES:
        return (_lineage_structural_item("lineage", 0, single),)
    parts = _lineage_partition(children, gaps, evaluation.manifest_digest)
    if len(parts) == 1:
        # The part header made a one-part split larger than v1, which already did not fit.
        raise LineageSemanticCapacityExceeded("lineage_semantic_input_too_large")
    return tuple(
        _lineage_structural_item(f"lineage-{index:02d}", index, encoded)
        for index, encoded in enumerate(parts)
    )


def _history_json(
    item: FrozenHistoryEvent,
    *,
    include_content: bool,
    include_exact_command_text: bool,
) -> tuple[str, _BoundedFit]:
    body: dict[str, JsonValue] = {
        "content_visibility": item.content_visibility,
        "event_id": item.event_id,
        "ingestion_sequence": item.ingestion_sequence,
        "kind": item.schema_name,
        "occurred_at": item.occurred_at,
        "payload_digest": item.payload_digest,
    }
    if item.accepted_at is None:
        body["occurred_at_consistency"] = "not_available_in_legacy_case"
    else:
        body["accepted_at"] = item.accepted_at
        body["occurred_at_consistency"] = item.occurred_at_consistency
    if include_content and item.payload is not None:
        payload = dict(cast(Mapping[str, JsonValue], item.payload))
        if item.schema_name == "action_recorded" and not include_exact_command_text:
            payload.pop("command", None)
        body["payload"] = cast(JsonValue, payload)
    return _bounded_json(body)


def _omit(
    subject_ref: str,
    category: DataCategory,
    source_kind: _SourceKind,
    reason: _OmissionReason,
) -> ReviewOmission:
    return ReviewOmission(
        subject_ref=subject_ref,
        category=category,
        source_kind=source_kind,
        reason=reason,
    )


def _captured_content_groups(
    projection: object,
    allowed: frozenset[str],
    content: Sequence[CapturedSemanticContent],
    scope: CapturedContentScope | None,
) -> tuple[dict[str, _CapturedGroup], frozenset[str]]:
    """Index service-authenticated content by its case-bound evidence row.

    This is intentionally a pure check. The service has already decrypted and secret-scanned the
    object before constructing ``CapturedSemanticContent``; this function verifies that the bytes
    cannot be attached to another task, phase, profile, or evidence identity and that multipart
    content is complete before it becomes an AI-powered review excerpt.
    """

    # Importing the concrete projection type would make the public builder depend on the adapter
    # implementation. The frozen case owns the mapping and the small attribute checks below keep
    # this seam capability-free.
    evidence = getattr(projection, "evidence", None)
    if not isinstance(evidence, Mapping):
        return {}, frozenset({"content_capture_unavailable"}) if content else frozenset()

    gaps: set[str] = set()
    expected_phases = dict(scope.phase_bindings) if scope is not None else {}
    by_object: dict[str, list[tuple[str, object]]] = {}
    payload_by_ref: dict[str, EvidenceRecordedPayload] = {}
    evidence_map = cast(Mapping[object, object], evidence)
    for raw_ref, record in evidence_map.items():
        ref = str(raw_ref)
        typed_record = cast(EvidenceProjectionRecord, record)
        payload = typed_record.payload
        object_value = getattr(payload, "captured_object_id", None)
        if object_value is not None:
            by_object.setdefault(str(object_value), []).append((ref, record))
        if type(payload) is EvidenceRecordedPayload:
            payload_by_ref[ref] = payload

    parts: dict[
        tuple[str, str, str, str, str, int],
        list[tuple[str, CapturedSemanticContent]],
    ] = {}
    for item in content:
        if scope is None:
            gaps.add("content_capture_unavailable")
            continue
        if (
            item.task_id != scope.task_id
            or item.session_id != scope.session_id
            or item.workspace_commitment != scope.workspace_commitment
        ):
            gaps.add("content_unselected")
            continue
        if item.capture_profile not in scope.authorized_profiles:
            gaps.add("content_unselected")
            continue

        associations = by_object.get(item.object_ref.object_id, ())
        if len(associations) != 1:
            gaps.add("content_unselected")
            continue
        evidence_ref, record = associations[0]
        if evidence_ref not in allowed:
            gaps.add("content_unselected")
            continue
        expected_phase = expected_phases.get(evidence_ref)
        if expected_phase is None or item.phase_identity != expected_phase:
            # The phase identity is only meaningful when the service derived the
            # same identity from the envelope that materialized this evidence row.
            # A valid digest from another phase must remain excluded.
            gaps.add("content_unselected")
            continue
        payload = getattr(record, "payload", None)
        binding = getattr(payload, "digest_binding", None)
        if (
            type(payload) is not EvidenceRecordedPayload
            or payload.evidence_kind is not EvidenceKind.OTHER
            or payload.strength is not EvidenceImmutability.IMMUTABLE_SNAPSHOT
            or payload.captured_object_id != item.object_ref.object_id
            or payload.content_digest != item.manifest.content_digest
            or type(binding) is not EvidenceDigestBinding
            or binding.subject is not EvidenceDigestSubject.BOUNDED_EXCERPT
            or binding.content_availability is not EvidenceContentAvailability.CAPTURED
            or binding.provenance is not EvidenceDigestProvenance.OBSERVATION_CAPTURED
            or binding.byte_count != item.manifest.content_bytes
        ):
            gaps.add("content_capture_unavailable")
            continue
        if (
            getattr(record, "redacted", False)
            or not getattr(record, "object_available", False)
            or getattr(record, "redacted_object_id", None) is not None
        ):
            gaps.add(
                "content_redacted"
                if getattr(record, "redacted", False)
                else "content_capture_unavailable"
            )
            continue

        if item.manifest.redacted:
            gaps.add("content_redacted")
        gaps.update(item.capture_gaps)
        key = (
            item.phase_identity,
            item.capture_profile,
            item.manifest.content_kind.value,
            item.manifest.correlation_identity or "",
            item.manifest.source_commitment or "",
            item.manifest.part_count,
        )
        parts.setdefault(key, []).append((evidence_ref, item))

    groups: dict[str, _CapturedGroup] = {}
    for rows in parts.values():
        rows.sort(key=lambda row: (row[1].manifest.part_index, row[0].encode("ascii")))
        expected_count = rows[0][1].manifest.part_count
        indexes = [item.manifest.part_index for _ref, item in rows]
        if (
            len(rows) != expected_count
            or indexes != list(range(expected_count))
            or len({ref for ref, _item in rows}) != len(rows)
        ):
            gaps.add("content_capture_unavailable")
            continue
        combined = b"".join(item.content for _ref, item in rows)
        if not 1 <= len(combined) <= MAX_CAPTURED_SEMANTIC_CONTENT_BYTES:
            gaps.add("content_capture_unavailable")
            continue
        first_ref, first_item = rows[0]
        source_kind: _ExcerptKind = (
            "evidence"
            if first_item.manifest.content_kind is ObservationContentKind.TOOL_OUTPUT
            else "diff"
        )
        if len(rows) == 1:
            payload = payload_by_ref[first_ref]
            binding = payload.digest_binding
            assert type(binding) is EvidenceDigestBinding
            provenance = ExcerptDigestProvenance(
                evidence_kind=payload.evidence_kind,
                strength=payload.strength,
                content_digest=payload.content_digest or first_item.manifest.content_digest or "",
                digest_subject=binding.subject,
                content_availability=binding.content_availability,
                byte_count=binding.byte_count,
                provenance=binding.provenance,
                approval_commitment=binding.approval_commitment,
                approved_check_result_digest=binding.approved_check_result_digest,
            )
        else:
            # The ledger binds each part independently. The combined excerpt gets a freshly
            # computed digest over exactly those authenticated parts, with the same observation
            # provenance; no caller description is used as a substitute.
            provenance = ExcerptDigestProvenance(
                evidence_kind=EvidenceKind.OTHER,
                strength=EvidenceImmutability.IMMUTABLE_SNAPSHOT,
                content_digest="sha256:" + hashlib.sha256(combined).hexdigest(),
                digest_subject=EvidenceDigestSubject.BOUNDED_EXCERPT,
                content_availability=EvidenceContentAvailability.CAPTURED,
                byte_count=len(combined),
                provenance=EvidenceDigestProvenance.OBSERVATION_CAPTURED,
            )
        group = _CapturedGroup(
            evidence_refs=tuple(ref for ref, _item in rows),
            content=combined,
            source_kind=source_kind,
            digest_provenance=provenance,
            capture_gaps=tuple(
                sorted({gap for _ref, item in rows for gap in item.capture_gaps}, key=str.encode)
            ),
        )
        groups[first_ref] = group

    return groups, frozenset(gaps)


def _match_assessments(
    case: DeterministicCase,
    findings: Sequence[Finding],
) -> tuple[tuple[Finding, DeterministicAssessment], ...]:
    assessments, _executions = run_deterministic_policies(
        case,
        CheckScope((), ()),
        _CANONICAL_PACKS,
    )
    by_key: dict[tuple[object, tuple[str, ...]], DeterministicAssessment] = {}
    for assessment in assessments:
        key = (
            assessment.candidate.kind,
            tuple(str(ref) for ref in assessment.candidate.subject_refs),
        )
        by_key[key] = assessment
    matched: list[tuple[Finding, DeterministicAssessment]] = []
    for finding in findings:
        key = (finding.kind, tuple(str(ref) for ref in finding.subject_refs))
        assessment = by_key.get(key)
        if assessment is not None:
            matched.append((finding, assessment))
    return tuple(matched)


def repair_evidence_refs(projection: ProjectionState, allowed: frozenset[str]) -> frozenset[str]:
    """Select readable evidence linked by responses to in-scope, unresolved findings.

    A response may link evidence directly or one result with evidence refs. This is relevance
    selection only: it never clears a finding or bypasses capture authentication or privacy.
    """

    evidence = {str(key): row for key, row in projection.evidence.items()}
    results = {str(key): row for key, row in projection.results.items()}
    selected: set[str] = set()
    for finding_id, response in projection.responses.items():
        finding = projection.findings.get(finding_id)
        if (
            str(finding_id) not in allowed
            or finding is None
            or finding.payload is None
            or finding.redacted
            or finding.resolved_by_check_event_id is not None
            or response.payload is None
            or response.redacted
            or str(response.source_event_id) not in allowed
        ):
            continue
        refs: set[str] = set()
        for ref in response.payload.evidence_refs:
            value = str(ref)
            if value not in allowed:
                continue
            if value in evidence:
                refs.add(value)
            result = results.get(value)
            if result is not None and result.payload is not None and not result.redacted:
                refs.update(str(item) for item in result.payload.evidence_refs)
        selected.update(
            ref
            for ref in refs & allowed
            if ref in evidence and evidence[ref].payload is not None and not evidence[ref].redacted
        )
    return frozenset(selected)


# Excerpt ranks, in the reserved-room order issue #907 fixes. The task statement (#908) is a packet
# section of its own, not an excerpt: it takes no excerpt slot, and neither the excerpt byte budget
# nor the prepared-payload planning in ``build_semantic_case`` ever trims a non-excerpt section, so
# its room is held by construction ahead of every excerpt class below.
_RANK_CURRENT_DIFF: Final = 0
_RANK_LATEST_VERIFICATION: Final = 1
_RANK_PRIOR_FINDING_CONTEXT: Final = 2
# An older captured edit of a path that changed again. Captured edits are hunks, not whole files:
# a later hunk elsewhere in the file leaves this one's lines in place, so it is still code under
# review and keeps the #883 rule that a patch is never starved by tool output or file reads.
_RANK_OLDER_EDIT: Final = 3
_RANK_UNRESERVED: Final = 4
_RANK_SUPERSEDED: Final = 5
_OUTSIDE_WORKSPACE_PATH: Final = "<outside-workspace>"
_MAX_EDIT_PATHS: Final = 64
_PATCH_FILE_LINE: Final = re.compile(r"^\*\*\* (?:Update|Add|Delete) File: (.+?)[ \t]*$", re.M)
_PATCH_MOVE_LINE: Final = re.compile(r"^\*\*\* Move to: (.+?)[ \t]*$", re.M)
_GIT_DIFF_LINE: Final = re.compile(r"^diff --git a/(\S+) b/(\S+)[ \t]*$", re.M)
_UNIFIED_HEADER: Final = re.compile(r"^--- \S[^\n]*\n\+\+\+ (?:b/)?(\S+)[ \t]*$", re.M)


@dataclass(frozen=True, slots=True)
class _ExcerptCandidate:
    """One evidence row, command, or failed result that may become one or more excerpt items.

    Each candidate is exactly one recorded source: one evidence row (or one authenticated
    multipart capture), one action's command, or one result's summary. A candidate split into
    parts spends one excerpt slot per part; no excerpt ever joins two sources (issue #907).
    """

    source_ref: str
    item_base: str
    source_kind: _ExcerptKind
    category: DataCategory
    parts: tuple[str, ...]
    clipped: bool
    linked: tuple[str, ...]
    occurred_order: int
    content_visibility: Literal["available", "not_recorded"]
    digest_provenance: ExcerptDigestProvenance | None
    identity_refs: frozenset[str]
    # Changed paths of an applied (or unconfirmed) captured edit; ``None`` for anything else.
    edit_paths: tuple[str, ...] | None = None
    verification_identity: str | None = None
    verification_outcome: ResultOutcome | None = None
    verification_action: str | None = None
    verification_run: str | None = None
    structural_only: bool = False


@dataclass(frozen=True, slots=True)
class _ExcerptSelection:
    items: tuple[SemanticCaseItem, ...]
    targeted: tuple[TargetedExcerptRef, ...]
    omissions: tuple[ReviewOmission, ...]
    gaps: frozenset[str]
    over_limit: frozenset[str]


def _captured_edit_paths(content: bytes) -> tuple[bool, tuple[str, ...]]:
    """Return ``(failed, paths)`` for one captured edit, from its own recorded bytes only.

    Codex ``apply_patch`` and shell ``git apply`` captures are patch text; Claude Code, Cursor
    and shell whole-file writes are the changed-file JSON the hook selected. Paths are already
    workspace-relative (the hook masks anything outside the workspace), are used only to relate
    hunks of the same file, and are never copied into the packet by this function.
    """

    paths: set[str] = set()
    failed = False
    parsed: JsonValue = None
    if content.lstrip().startswith(b"{"):
        try:
            parsed = strict_json_parse(content)
        except ValueError:
            parsed = None
    if isinstance(parsed, Mapping):
        body = cast(Mapping[str, JsonValue], parsed)
        failed = body.get("edit_outcome") == "failed"
        path = body.get("path")
        if type(path) is str:
            paths.add(path)
        writes = body.get("writes")
        if isinstance(writes, (list, tuple)):
            for write in cast(Sequence[JsonValue], writes):
                if isinstance(write, Mapping):
                    target = cast(Mapping[str, JsonValue], write).get("path")
                    if type(target) is str:
                        paths.add(target)
    else:
        text = content.decode("utf-8", errors="replace")
        failed = text.startswith("# yoetz edit outcome: failed")
        paths.update(_PATCH_FILE_LINE.findall(text))
        paths.update(_PATCH_MOVE_LINE.findall(text))
        for old, new in _GIT_DIFF_LINE.findall(text):
            paths.update((old, new))
        paths.update(path for path in _UNIFIED_HEADER.findall(text) if path != "/dev/null")
    paths.discard(_OUTSIDE_WORKSPACE_PATH)
    paths.discard("")
    ordered = tuple(sorted(paths, key=lambda value: value.encode("utf-8")))
    return failed, ordered[:_MAX_EDIT_PATHS]


def _edit_paths_by_ref(groups: Mapping[str, _CapturedGroup]) -> dict[str, frozenset[str]]:
    """Every evidence ref of an applied captured edit, mapped to the paths the capture records."""

    by_ref: dict[str, frozenset[str]] = {}
    for group in groups.values():
        if group.source_kind != "diff":
            continue
        failed, paths = _captured_edit_paths(group.content)
        if failed or not paths:
            continue
        for ref in group.evidence_refs:
            by_ref[ref] = frozenset(paths)
    return by_ref


def captured_edit_paths(
    frozen_case: DeterministicCase,
    captured_content: Sequence[CapturedSemanticContent],
    captured_content_scope: CapturedContentScope | None,
) -> dict[str, frozenset[str]]:
    """Map each hook-captured edit's evidence refs to the workspace-relative paths it records.

    Issue #907: a later review compares these paths with the paths the agent names when it
    publishes a fresh diff, so a repeated ``current_diff_for_path`` request for a captured edit
    can converge. Only content the case builder would itself accept is read. The paths stay in
    process for that comparison; they are never recorded, logged or sent.
    """

    allowed = frozenset(str(ref) for ref in frozen_case.allowed_ids)
    groups, _gaps = _captured_content_groups(
        frozen_case.projection, allowed, captured_content, captured_content_scope
    )
    return _edit_paths_by_ref(groups)


# ``(identity, outcome, action ref, result ref)``: the result ref names one run, so an output and
# the failure summary of the same run are one run, never a newer run superseding its own output.
type _VerificationRun = tuple[str, ResultOutcome, str, str]


def _verification_identities(
    projection: ProjectionState,
) -> tuple[dict[str, _VerificationRun], dict[str, _VerificationRun]]:
    """Map evidence and result refs to the command whose run they record.

    Each run is ``(identity, outcome, action ref)``. Identity is the digest of the recorded command
    text of the action a result answers; nothing else is inferred. Captured tool output carries no command until #910 records one, so it stays
    unidentified and competes by recency. The digest never leaves this function's callers.
    """

    by_evidence: dict[str, _VerificationRun] = {}
    by_result: dict[str, _VerificationRun] = {}
    for result_ref, row in sorted(
        projection.results.items(),
        key=lambda pair: (pair[1].source_frontier, str(pair[0]).encode("ascii")),
    ):
        payload = row.payload
        if payload is None or row.redacted:
            continue
        action = projection.actions.get(payload.action_id)
        if (
            action is None
            or action.payload is None
            or action.redacted
            or action.payload.action_kind is not ActionKind.COMMAND
            or not action.payload.command
        ):
            continue
        identity = "command:" + hashlib.sha256(action.payload.command.encode("utf-8")).hexdigest()
        run = (identity, payload.outcome, str(payload.action_id), str(result_ref))
        by_result[str(result_ref)] = run
        for evidence_ref in payload.evidence_refs:
            by_evidence[str(evidence_ref)] = run
    return by_evidence, by_result


def _evidence_candidates(
    *,
    projection: ProjectionState,
    allowed: frozenset[str],
    selection: ReviewSelectionPolicy,
    linked_subjects: set[str],
    captured_groups: Mapping[str, _CapturedGroup],
    captured_group_leader: Mapping[str, str],
    excerpt_limit: int,
    part_limit: int,
    verification_by_evidence: Mapping[str, _VerificationRun],
    omissions: list[ReviewOmission],
    gaps: set[str],
) -> list[_ExcerptCandidate]:
    candidates: list[_ExcerptCandidate] = []
    admitted_capture_digests: set[bytes] = set()
    for evidence_id, record in sorted(
        projection.evidence.items(),
        key=lambda pair: (pair[1].source_frontier, str(pair[0]).encode("ascii")),
    ):
        ref = str(evidence_id)
        if ref not in allowed:
            continue
        payload = record.payload
        if payload is None or record.redacted:
            omissions.append(
                _omit(
                    ref,
                    DataCategory.EVIDENCE_EXCERPT,
                    "evidence",
                    "redacted_never_send" if record.redacted else "not_recorded",
                )
            )
            continue
        assert type(payload) is EvidenceRecordedPayload
        leader = captured_group_leader.get(ref)
        if leader is not None and leader != ref:
            # Multipart captured evidence is one AI-powered review excerpt. Carrying each part as a
            # separate excerpt would let an incomplete group look reviewable and would spend
            # the selection budget on duplicate structural descriptions.
            continue
        captured_group = captured_groups.get(ref)
        excerpt_kind = (
            captured_group.source_kind
            if captured_group is not None
            else _EVIDENCE_EXCERPT_KIND.get(payload.evidence_kind, "evidence")
        )
        if excerpt_kind not in selection.excerpt_kinds:
            if captured_group is not None:
                gaps.add("content_unselected")
            omissions.append(
                _omit(ref, DataCategory.EVIDENCE_EXCERPT, excerpt_kind, "not_selected")
            )
            continue
        if selection.relevance == "linked_subjects_only":
            source_event = str(record.source_event_id)
            if ref not in linked_subjects and source_event not in linked_subjects:
                if captured_group is not None:
                    gaps.add("content_unselected")
                omissions.append(
                    _omit(ref, DataCategory.EVIDENCE_EXCERPT, excerpt_kind, "not_selected")
                )
                continue
        if (
            captured_group is None
            and payload.captured_object_id is not None
            and payload.digest_binding is not None
            and payload.digest_binding.provenance is EvidenceDigestProvenance.OBSERVATION_CAPTURED
        ):
            # A structural capture description is never a substitute for authenticated bytes.
            # Preserve the coverage gap even when the omission list itself is capped away.
            omissions.append(
                _omit(ref, DataCategory.EVIDENCE_EXCERPT, excerpt_kind, "not_recorded")
            )
            gaps.add("captured_object_unavailable")
            continue
        digest_provenance: ExcerptDigestProvenance | None = None
        text: str | None
        if captured_group is not None:
            capture_digest = hashlib.sha256(captured_group.content).digest()
            if capture_digest in admitted_capture_digests:
                # Identical retained bytes (for example a patch captured by an older build
                # on both its pre- and post-tool events) are one excerpt, not two.
                omissions.append(
                    _omit(ref, DataCategory.EVIDENCE_EXCERPT, excerpt_kind, "not_selected")
                )
                continue
            admitted_capture_digests.add(capture_digest)
            # The service-authenticated inner bytes are the only source that may populate a
            # captured AI-powered review excerpt. Their digest provenance is retained separately
            # from the digest of the selection-clipped item below.
            text = captured_group.content.decode("utf-8")
            digest_provenance = captured_group.digest_provenance
        elif payload.content_digest is not None:
            binding = payload.digest_binding
            if binding is None:
                omissions.append(
                    _omit(ref, DataCategory.EVIDENCE_EXCERPT, excerpt_kind, "not_recorded")
                )
                continue
            digest_provenance = ExcerptDigestProvenance(
                evidence_kind=payload.evidence_kind,
                strength=payload.strength,
                content_digest=payload.content_digest,
                digest_subject=binding.subject,
                content_availability=binding.content_availability,
                byte_count=binding.byte_count,
                provenance=binding.provenance,
                approval_commitment=binding.approval_commitment,
                approved_check_result_digest=binding.approved_check_result_digest,
            )
            if payload.description:
                # Caller-authored narrative stays legible; digest identity rides on the
                # excerpt ref instead of replacing the content (issue #176).
                text = payload.description
            else:
                text = canonical_encode(
                    cast(
                        JsonValue,
                        {
                            "schema": "yoetz.evidence-digest-provenance/1",
                            "evidence_kind": payload.evidence_kind.value,
                            "strength": payload.strength.value,
                            "content_digest": payload.content_digest,
                            "digest_subject": binding.subject.value,
                            "content_availability": binding.content_availability.value,
                            "byte_count": binding.byte_count,
                            "provenance": binding.provenance.value,
                            **(
                                {}
                                if binding.approval_commitment is None
                                else {"approval_commitment": binding.approval_commitment}
                            ),
                            **(
                                {}
                                if binding.approved_check_result_digest is None
                                else {
                                    "approved_check_result_digest": (
                                        binding.approved_check_result_digest
                                    )
                                }
                            ),
                        },
                    )
                ).decode("utf-8")
        else:
            text = payload.description or payload.reference
        if text is None or not text:
            omissions.append(
                _omit(ref, DataCategory.EVIDENCE_EXCERPT, excerpt_kind, "not_recorded")
            )
            continue
        encoded = text.encode("utf-8")
        split_diff = captured_group is not None and excerpt_kind == "diff"
        clipped = False
        if split_diff:
            # Split authenticated code, not freeform claim prose, into independently bounded
            # items. Later hunks compete for the explicit count/total caps as parts of this one
            # capture; each part is one excerpt slot and no part joins two captures.
            parts: list[str] = []
            remaining = encoded
            while remaining and len(parts) <= selection.max_excerpts:
                part = remaining[:part_limit].decode("utf-8", errors="ignore")
                if not part:
                    break
                parts.append(part)
                remaining = remaining[len(part.encode("utf-8")) :]
            clipped = bool(remaining)
        else:
            # Long output keeps its head and its tail with the cut marked (issue #907).
            clipped = len(encoded) > excerpt_limit
            parts = [_head_tail(encoded, excerpt_limit) if clipped else text]
        group_refs = captured_group.evidence_refs if captured_group is not None else ()
        linked = tuple(
            sorted(
                {
                    ref,
                    *group_refs,
                    str(record.source_event_id),
                    *(
                        str(projection.evidence[validate_evidence_id(evidence_ref)].source_event_id)
                        for evidence_ref in group_refs
                        if validate_evidence_id(evidence_ref) in projection.evidence
                    ),
                    *(
                        str(claim_id)
                        for claim_id, claim_record in effective_claim_items(projection)
                        if claim_record.payload is not None
                        and any(
                            str(support) in (set(group_refs) if group_refs else {ref})
                            for support in claim_record.payload.supporting_refs
                        )
                    ),
                }
                & allowed,
                key=str.encode,
            )
        )[:16]
        if not linked:
            if captured_group is not None:
                gaps.add("content_unselected")
            omissions.append(
                _omit(ref, DataCategory.EVIDENCE_EXCERPT, excerpt_kind, "not_selected")
            )
            continue
        edit_paths: tuple[str, ...] | None = None
        if split_diff:
            assert captured_group is not None
            failed_edit, paths = _captured_edit_paths(captured_group.content)
            edit_paths = None if failed_edit else paths
        verification = verification_by_evidence.get(ref)
        candidates.append(
            _ExcerptCandidate(
                source_ref=ref,
                item_base=f"excerpt-{ref}",
                source_kind=excerpt_kind,
                category=DataCategory.EVIDENCE_EXCERPT,
                parts=tuple(parts),
                clipped=clipped,
                linked=linked,
                occurred_order=record.source_frontier,
                content_visibility=(
                    "available"
                    if captured_group is not None or payload.captured_object_id is None
                    else "not_recorded"
                ),
                digest_provenance=digest_provenance,
                identity_refs=frozenset({ref, *group_refs}),
                edit_paths=edit_paths,
                verification_identity=None if verification is None else verification[0],
                verification_outcome=None if verification is None else verification[1],
                verification_action=None if verification is None else verification[2],
                verification_run=None if verification is None else verification[3],
                structural_only=(
                    captured_group is None
                    and payload.evidence_kind is EvidenceKind.OTHER
                    and payload.strength is EvidenceImmutability.METADATA_ONLY
                    and payload.content_digest is None
                ),
            )
        )
    return candidates


def _select_targeted_excerpts(
    *,
    projection: ProjectionState,
    allowed: frozenset[str],
    selection: ReviewSelectionPolicy,
    findings: Sequence[Finding],
    review_assessments: Sequence[ReviewAssessment],
    captured_groups: Mapping[str, _CapturedGroup],
    captured_group_leader: Mapping[str, str],
    excerpt_byte_budget: int,
) -> _ExcerptSelection:
    """Choose the excerpts that fill the approved count and byte budget, most valuable first.

    Reserved room comes first, in the order issue #907 fixes: the newest captured edit of every
    changed path, then the latest output of every identified verification command (with the last
    failure kept beside a later pass). Evidence supplied to repair a finding (#898) follows, then
    older hunks of a changed path (still code under review), then everything else by recency,
    then by link class; older runs of the same command come last. Older hunks and runs are marked
    ``superseded_by``. Every excerpt still holds exactly one
    recorded source or one part of one capture.
    """

    repair_refs = repair_evidence_refs(projection, allowed)
    linked_subjects: set[str] = set(repair_refs)
    for finding in findings:
        linked_subjects.update(str(ref) for ref in finding.subject_refs)
    for claim_id, _ in effective_claim_items(projection):
        linked_subjects.add(str(claim_id))
    for obligation_id in projection.obligations:
        linked_subjects.add(str(obligation_id))
    # Assessment supporting refs are case-bound links the reviewer may need as excerpts.
    for assessment in review_assessments:
        linked_subjects.update(str(ref) for ref in assessment.supporting_refs)
        for fact in (*assessment.observed_facts, *assessment.required_but_missing_facts):
            linked_subjects.update(str(ref) for ref in fact.subject_refs)
    for _, claim_record in effective_claim_items(projection):
        if claim_record.payload is None:
            continue
        linked_subjects.update(str(ref) for ref in claim_record.payload.supporting_refs)

    # The approved policy already allows ``max_excerpt_bytes`` per excerpt; the old 4 KiB
    # structural item clip no longer applies to excerpts (issue #907, open question 3). Nothing
    # here widens the count, the per-excerpt bound or the total the owner approved.
    excerpt_limit = min(selection.max_excerpt_bytes, MAX_SEMANTIC_ITEM_BYTES)
    # A capture split into parts never makes one part larger than the whole excerpt budget.
    part_limit = min(excerpt_limit, selection.max_total_excerpt_bytes)
    omissions: list[ReviewOmission] = []
    gaps: set[str] = set()
    verification_by_evidence, verification_by_result = _verification_identities(projection)
    candidates = _evidence_candidates(
        projection=projection,
        allowed=allowed,
        selection=selection,
        linked_subjects=linked_subjects,
        captured_groups=captured_groups,
        captured_group_leader=captured_group_leader,
        excerpt_limit=excerpt_limit,
        part_limit=part_limit,
        verification_by_evidence=verification_by_evidence,
        omissions=omissions,
        gaps=gaps,
    )

    # Optional command excerpts from actions when expanded selection allows exact commands.
    if selection.include_exact_command_text and "command" in selection.excerpt_kinds:
        for action_id, record in sorted(projection.actions.items(), key=lambda pair: str(pair[0])):
            ref = str(action_id)
            if ref not in allowed or record.payload is None or record.redacted:
                continue
            if record.payload.action_kind is not ActionKind.COMMAND:
                continue
            command = record.payload.command
            if command is None:
                omissions.append(
                    _omit(ref, DataCategory.COMMAND_METADATA, "command", "not_recorded")
                )
                continue
            linked = (ref,) if ref in allowed else (str(record.source_event_id),)
            linked = tuple(item for item in linked if item in allowed)
            if not linked:
                continue
            encoded = command.encode("utf-8")
            clipped = len(encoded) > excerpt_limit
            candidates.append(
                _ExcerptCandidate(
                    source_ref=ref,
                    item_base=f"excerpt-cmd-{ref}",
                    source_kind="command",
                    category=DataCategory.COMMAND_METADATA,
                    parts=(_head_tail(encoded, excerpt_limit) if clipped else command,),
                    clipped=clipped,
                    linked=linked,
                    occurred_order=record.source_frontier,
                    content_visibility="available",
                    digest_provenance=None,
                    identity_refs=frozenset({ref, str(record.source_event_id)}),
                )
            )

    # Failed results as failure excerpts when description-like summary exists.
    if "failure" in selection.excerpt_kinds:
        for result_id, record in sorted(projection.results.items(), key=lambda pair: str(pair[0])):
            ref = str(result_id)
            if ref not in allowed or record.payload is None or record.redacted:
                continue
            if record.payload.outcome is not ResultOutcome.FAILURE:
                continue
            summary = record.payload.summary
            if summary is None or not summary:
                omissions.append(
                    _omit(ref, DataCategory.EVIDENCE_EXCERPT, "failure", "not_recorded")
                )
                continue
            linked = tuple(
                sorted(
                    {
                        item
                        for item in (
                            ref,
                            str(record.payload.action_id),
                            str(record.source_event_id),
                        )
                        if item in allowed
                    },
                    key=str.encode,
                )
            )[:16]
            if not linked:
                continue
            encoded = summary.encode("utf-8")
            clipped = len(encoded) > excerpt_limit
            verification = verification_by_result.get(ref)
            candidates.append(
                _ExcerptCandidate(
                    source_ref=ref,
                    item_base=f"excerpt-fail-{ref}",
                    source_kind="failure",
                    category=DataCategory.EVIDENCE_EXCERPT,
                    parts=(_head_tail(encoded, excerpt_limit) if clipped else summary,),
                    clipped=clipped,
                    linked=linked,
                    occurred_order=record.source_frontier,
                    content_visibility="available",
                    digest_provenance=None,
                    identity_refs=frozenset(
                        {ref, str(record.payload.action_id), str(record.source_event_id)}
                    ),
                    verification_identity=None if verification is None else verification[0],
                    verification_outcome=None if verification is None else verification[1],
                    verification_action=None if verification is None else verification[2],
                    verification_run=None if verification is None else verification[3],
                )
            )

    latest_for: dict[int, Literal["path", "command"]] = {}
    superseded_by: dict[int, tuple[str, ...]] = {}
    current_diffs: set[int] = set()
    reserved_runs: set[int] = set()

    def recency(index: int) -> tuple[int, bytes]:
        candidate = candidates[index]
        return (candidate.occurred_order, candidate.source_ref.encode("ascii"))

    # Current diff: the newest applied (or unconfirmed) captured edit of each changed path. A
    # failed edit changed nothing, so it neither is current code nor supersedes anything.
    newest_for_path: dict[str, int] = {}
    for index, candidate in enumerate(candidates):
        for path in candidate.edit_paths or ():
            known = newest_for_path.get(path)
            if known is None or recency(index) > recency(known):
                newest_for_path[path] = index
    for index, candidate in enumerate(candidates):
        if candidate.edit_paths is None:
            continue
        newer = {newest_for_path[path] for path in candidate.edit_paths} - {index}
        if not candidate.edit_paths or len(newer) < len(
            {newest_for_path[path] for path in candidate.edit_paths}
        ):
            # Newest for at least one path (or a capture whose paths cannot be read): current.
            current_diffs.add(index)
            if candidate.edit_paths:
                latest_for[index] = "path"
        else:
            superseded_by[index] = tuple(
                sorted({candidates[other].source_ref for other in newer}, key=str.encode)
            )

    # Latest verification output per command identity, keeping a failure beside a later pass. A
    # run is one recorded result: its captured output and its failure summary are the same run.
    runs: dict[str, dict[str, list[int]]] = {}
    for index, candidate in enumerate(candidates):
        if candidate.verification_identity is not None and candidate.verification_run is not None:
            runs.setdefault(candidate.verification_identity, {}).setdefault(
                candidate.verification_run, []
            ).append(index)
    for by_run in runs.values():
        ordered = sorted(
            by_run.values(), key=lambda members: max(recency(i) for i in members), reverse=True
        )
        latest_run = ordered[0]
        newest_refs = tuple(sorted({candidates[i].source_ref for i in latest_run}, key=str.encode))
        for index in latest_run:
            reserved_runs.add(index)
            latest_for.setdefault(index, "command")
        if candidates[latest_run[0]].verification_outcome is ResultOutcome.SUCCESS:
            prior_failure = next(
                (
                    members
                    for members in ordered[1:]
                    if candidates[members[0]].verification_outcome is ResultOutcome.FAILURE
                ),
                None,
            )
            reserved_runs.update(prior_failure or ())
        for members in ordered[1:]:
            for index in members:
                if index not in current_diffs:
                    superseded_by.setdefault(index, newest_refs)
    # The exact command of a reserved run travels beside its output, so the reviewer can name
    # which verification it reads (Expanded selection only carries command text).
    reserved_actions = {
        candidates[index].verification_action
        for index in reserved_runs
        if candidates[index].verification_action is not None
    }
    for index, candidate in enumerate(candidates):
        if candidate.source_kind == "command" and candidate.source_ref in reserved_actions:
            reserved_runs.add(index)

    def rank(index: int) -> tuple[int, int, int, int, bytes, bytes]:
        candidate = candidates[index]
        linked = bool(candidate.identity_refs & linked_subjects)
        if index in current_diffs:
            klass = _RANK_CURRENT_DIFF
        elif index in reserved_runs:
            klass = _RANK_LATEST_VERIFICATION
        elif candidate.identity_refs & repair_refs:
            klass = _RANK_PRIOR_FINDING_CONTEXT
        elif index in superseded_by:
            klass = _RANK_OLDER_EDIT if candidate.edit_paths is not None else _RANK_SUPERSEDED
        else:
            klass = _RANK_UNRESERVED
        # Unlinked observation metadata rows describe an event, not its content; they only fill
        # room that content-bearing material leaves.
        metadata_last = (
            1 if klass == _RANK_UNRESERVED and candidate.structural_only and not linked else 0
        )
        return (
            klass,
            metadata_last,
            -candidate.occurred_order,
            0 if linked else 1,
            candidate.source_ref.encode("ascii"),
            candidate.item_base.encode("ascii"),
        )

    items: list[SemanticCaseItem] = []
    targeted: list[TargetedExcerptRef] = []
    over_limit: set[str] = set()
    excerpt_bytes_used = 0
    for index in sorted(range(len(candidates)), key=rank):
        candidate = candidates[index]
        admitted_before = len(targeted)
        truncated = candidate.clipped
        for part_index, part in enumerate(candidate.parts):
            part_bytes = len(part.encode("utf-8"))
            if (
                len(targeted) >= selection.max_excerpts
                or excerpt_bytes_used + part_bytes > excerpt_byte_budget
            ):
                truncated = True
                # Issue #907: every candidate the count or byte budget cuts is disclosed, ledger
                # evidence as well as captured content. The omission row below may be capped away
                # with the omission list; the coverage gap is then the only trace.
                gaps.add("content_unselected")
                omissions.append(
                    _omit(
                        candidate.source_ref,
                        candidate.category,
                        candidate.source_kind,
                        "not_selected",
                    )
                )
                break
            item_id = (
                candidate.item_base
                if len(candidate.parts) == 1
                else f"{candidate.item_base}-part-{part_index + 1:02d}"
            )
            item = _content_item(
                item_id=item_id,
                section="excerpt",
                category=candidate.category,
                source_kind=candidate.source_kind,
                source_ref=candidate.source_ref,
                linked_subject_refs=candidate.linked,
                occurred_order=candidate.occurred_order,
                text=part,
                over_limit=over_limit,
                limit=excerpt_limit,
                latest_for=latest_for.get(index) if index not in superseded_by else None,
                superseded_by=superseded_by.get(index, ()),
            )
            items.append(item)
            targeted.append(
                TargetedExcerptRef(
                    excerpt_item_id=item_id,
                    source_kind=candidate.source_kind,
                    linked_subject_refs=candidate.linked,
                    subject_state_relation=SubjectStateRelation.UNKNOWN,
                    content_visibility=candidate.content_visibility,
                    content_digest=item.content_digest,
                    content_bytes=item.content_bytes,
                    digest_provenance=candidate.digest_provenance,
                )
            )
            excerpt_bytes_used += item.content_bytes
        if truncated and len(targeted) > admitted_before:
            gaps.add("truncated_payload")
    return _ExcerptSelection(
        items=tuple(items),
        targeted=tuple(targeted),
        omissions=tuple(omissions),
        gaps=frozenset(gaps),
        over_limit=frozenset(over_limit),
    )


def _prior_finding_candidates(
    projection: ProjectionState, allowed: frozenset[str]
) -> list[tuple[str, FindingProjectionRecord]]:
    """Readable, unresolved AI-powered findings inside the fence, newest first.

    Local findings are proven by the local packs that raised them and reach the reviewer as this
    check's assessments; resolved findings are history. Neither is a live question for the reviewer.
    """

    rows: list[tuple[str, FindingProjectionRecord]] = []
    for key, record in projection.findings.items():
        payload = record.payload
        ref = str(key)
        if (
            payload is None
            or record.redacted
            or record.resolved_by_check_event_id is not None
            or payload.origin is not FindingOrigin.SEMANTIC_MODEL_DERIVED
            or ref not in allowed
        ):
            continue
        rows.append((ref, record))
    rows.sort(key=lambda pair: (-pair[1].source_frontier, pair[0].encode("ascii")))
    return rows


def _prior_finding_items(
    ref: str,
    record: FindingProjectionRecord,
    projection: ProjectionState,
    allowed: frozenset[str],
    *,
    include_prose: bool,
) -> tuple[list[SemanticCaseItem], list[ReviewOmission]]:
    """One earlier finding, what the reviewer asked, and how the main agent answered.

    The structural row is always carried: kind, ids, recorded order, the requested next step, the
    response disposition and cited refs, and the readable evidence and results recorded after the
    finding (the material a repair would have produced). Prose rows follow the profile's finding
    prose selection. A finding recorded before challenge fields were persisted degrades to its
    summary and message with an explicit ``not_recorded`` omission.
    """

    payload = record.payload
    assert payload is not None
    items: list[SemanticCaseItem] = []
    omissions: list[ReviewOmission] = []
    response_record = projection.responses.get(finding_id_value(ref))
    response = (
        None if response_record is None or response_record.redacted else response_record.payload
    )
    newer = sorted(
        (
            (row.source_frontier, str(key))
            for family in (projection.evidence, projection.results)
            for key, row in family.items()
            if row.payload is not None
            and not row.redacted
            and row.source_frontier > record.source_frontier
            and str(key) in allowed
        ),
        key=lambda pair: (-pair[0], pair[1].encode("ascii")),
    )
    subjects: list[JsonValue] = [str(item) for item in payload.subject_refs if str(item) in allowed]
    after: list[JsonValue] = [value for _order, value in newer]
    body: dict[str, JsonValue] = {
        "challenge_fields": "not_recorded" if payload.challenge is None else "recorded",
        "finding_kind": payload.kind.value,
        "finding_ref": ref,
        "recorded_after_finding": after[:_MAX_PRIOR_FINDING_LISTED_REFS],
        "recorded_after_finding_count": len(newer),
        "recorded_sequence": record.source_frontier,
        "schema": "yoetz.prior-finding/1",
        "subject_ref_count": len(payload.subject_refs),
        "subject_refs": subjects[:_MAX_PRIOR_FINDING_LISTED_REFS],
    }
    related: list[JsonValue] = [
        str(item) for item in payload.related_finding_ids if str(item) in allowed
    ]
    if related:
        body["relates_to"] = related
    if payload.challenge is not None:
        body["requested_next_step"] = payload.challenge.requested_next_step
    if response_record is not None and response is None:
        body["response"] = {"visibility": "redacted_never_send"}
    elif response_record is not None and response is not None:
        cited: list[JsonValue] = [
            str(item) for item in response.evidence_refs if str(item) in allowed
        ]
        answer: dict[str, JsonValue] = {
            "disposition": response.disposition.value,
            "evidence_ref_count": len(response.evidence_refs),
            "evidence_refs": cited[:_MAX_PRIOR_FINDING_LISTED_REFS],
            "recorded_sequence": response_record.source_frontier,
        }
        if str(response_record.source_event_id) in allowed:
            answer["response_event_ref"] = str(response_record.source_event_id)
        body["response"] = answer
    prose: list[tuple[str, str, int]] = []
    if include_prose:
        prose.append(("summary", payload.summary, record.source_frontier))
        prose.append(("message", payload.detail, record.source_frontier))
        if payload.challenge is None:
            omissions.append(_omit(ref, DataCategory.FINDING_SUMMARY, "finding", "not_recorded"))
        else:
            prose.append(("discrepancy", payload.challenge.discrepancy, record.source_frontier))
            prose.append(
                (
                    "alternative",
                    payload.challenge.alternative_interpretation,
                    record.source_frontier,
                )
            )
            prose.append(("uncertainty", payload.challenge.uncertainty, record.source_frontier))
        if response_record is not None and response is not None and response.reason is not None:
            prose.append(("response", response.reason, response_record.source_frontier))
        body["text_item_ids"] = {
            field: f"prior-finding-{field}-{ref}" for field, _text, _order in prose
        }
    text, _omitted = _bounded_json(body)
    items.append(
        _content_item(
            item_id=f"prior-finding-{ref}",
            section="prior_finding",
            category=DataCategory.BOUNDED_STRUCTURAL_METADATA,
            source_kind="finding",
            source_ref=ref,
            linked_subject_refs=(ref,),
            occurred_order=record.source_frontier,
            text=text,
        )
    )
    for field, value, order in prose:
        items.append(
            _content_item(
                item_id=f"prior-finding-{field}-{ref}",
                section="prior_finding",
                category=DataCategory.FINDING_SUMMARY,
                source_kind="finding",
                source_ref=ref,
                linked_subject_refs=(ref,),
                occurred_order=order,
                text=value,
            )
        )
    return items, omissions


def _prior_findings_section(
    projection: ProjectionState,
    allowed: frozenset[str],
    *,
    include_prose: bool,
    remaining_items: int,
    remaining_bytes: int,
) -> tuple[list[SemanticCaseItem], list[ReviewOmission], bool]:
    """Carry the dialogue so far, newest first, inside the section's own bounds.

    A finding is carried whole or not at all; one that does not fit is named as a ``not_selected``
    omission and the returned flag declares the truncation as a coverage gap.
    """

    items: list[SemanticCaseItem] = []
    omissions: list[ReviewOmission] = []
    truncated = False
    item_budget = min(MAX_PRIOR_FINDING_ITEMS, remaining_items)
    byte_budget = min(MAX_PRIOR_FINDING_SECTION_BYTES, remaining_bytes)
    carried = 0
    for ref, record in _prior_finding_candidates(projection, allowed):
        rows, row_omissions = _prior_finding_items(
            ref, record, projection, allowed, include_prose=include_prose
        )
        size = sum(item.content_bytes for item in rows)
        if (
            carried >= MAX_PRIOR_FINDINGS
            or len(rows) > item_budget - len(items)
            or size > byte_budget
        ):
            truncated = True
            omissions.append(
                _omit(
                    ref,
                    DataCategory.FINDING_SUMMARY
                    if include_prose
                    else DataCategory.BOUNDED_STRUCTURAL_METADATA,
                    "finding",
                    "not_selected",
                )
            )
            continue
        carried += 1
        byte_budget -= size
        items.extend(rows)
        omissions.extend(row_omissions)
    return items, omissions, truncated


def build_semantic_case(
    *,
    case_id: str,
    frozen_case: DeterministicCase,
    dependency_digest: str,
    findings: Sequence[Finding],
    review_context_profile: ReviewContextProfile,
    review_selection: ReviewSelectionPolicy,
    policy_id: str,
    policy_version: str,
    lineage_evaluation: LineageEvaluation | None = None,
    captured_content: Sequence[CapturedSemanticContent] = (),
    captured_content_scope: CapturedContentScope | None = None,
    captured_content_gaps: Sequence[str] = (),
    prepared_byte_ceiling: int | None = None,
) -> SemanticCase:
    """Build one pre-egress AI-powered review case from frozen authority only.

    Excerpts may now fill the approved per-excerpt bound, so the case plans its excerpts below
    the channel byte ceiling as measured on the exact prepared document the gateway will size
    (issue #907): the schema maximum, narrowed to ``prepared_byte_ceiling`` when the caller
    passes the effective policy's own channel ceiling (its ``max_bytes`` and ``max_tokens``
    measured the way the gateway measures them). An over-plan case is rebuilt with a smaller
    excerpt budget; the dropped excerpts are ordinary ``not_selected`` omissions with
    ``content_unselected``. The gateway still enforces the owner's ceiling on every dispatch.
    """

    if prepared_byte_ceiling is not None and (
        type(prepared_byte_ceiling) is not int or prepared_byte_ceiling < 1
    ):
        raise ValueError("semantic_case_prepared_ceiling_invalid")
    planning_bytes = (
        _PREPARED_PAYLOAD_PLANNING_BYTES
        if prepared_byte_ceiling is None
        else max(0, min(MAX_SEMANTIC_CASE_BYTES, prepared_byte_ceiling) - _PLANNING_RESERVE_BYTES)
    )

    def build(excerpt_byte_budget: int | None) -> SemanticCase:
        return _build_semantic_case_once(
            case_id=case_id,
            frozen_case=frozen_case,
            dependency_digest=dependency_digest,
            findings=findings,
            review_context_profile=review_context_profile,
            review_selection=review_selection,
            policy_id=policy_id,
            policy_version=policy_version,
            lineage_evaluation=lineage_evaluation,
            captured_content=captured_content,
            captured_content_scope=captured_content_scope,
            captured_content_gaps=captured_content_gaps,
            excerpt_byte_budget=excerpt_byte_budget,
        )

    def fits(candidate: SemanticCase) -> bool | None:
        try:
            prepared = semantic_case_to_prepared_payload(
                candidate, {item.item_id for item in candidate.items}
            )
        except SemanticCaseTooLarge:
            # The structural envelope cannot be bounded at all; the coordinator maps that to
            # its own terminal capacity outcome. Excerpt planning cannot help it.
            return None
        return len(prepared) <= planning_bytes

    case = build(None)
    excerpt_bytes = sum(item.content_bytes for item in case.items if item.section == "excerpt")
    if not excerpt_bytes or fits(case) is not False:
        return case
    # JSON escaping makes the prepared document grow by a content-dependent factor, so search the
    # largest excerpt byte budget whose exact document fits. Admission runs in rank order, so a
    # smaller budget drops the lowest-ranked excerpts first.
    best = build(0)
    low, high = 0, excerpt_bytes
    while high - low > _PLANNING_GRANULARITY_BYTES:
        middle = (low + high) // 2
        candidate = build(middle)
        if fits(candidate):
            low, best = middle, candidate
        else:
            high = middle
    return best


def _build_semantic_case_once(
    *,
    case_id: str,
    frozen_case: DeterministicCase,
    dependency_digest: str,
    findings: Sequence[Finding],
    review_context_profile: ReviewContextProfile,
    review_selection: ReviewSelectionPolicy,
    policy_id: str,
    policy_version: str,
    lineage_evaluation: LineageEvaluation | None,
    captured_content: Sequence[CapturedSemanticContent],
    captured_content_scope: CapturedContentScope | None,
    captured_content_gaps: Sequence[str],
    excerpt_byte_budget: int | None,
) -> SemanticCase:
    if type(frozen_case) is not DeterministicCase:
        raise TypeError("deterministic_case_invalid")
    if type(review_context_profile) is not ReviewContextProfile:
        raise TypeError("review_context_profile_invalid")
    if type(review_selection) is not ReviewSelectionPolicy:
        raise TypeError("review_selection_invalid")
    if type(captured_content) not in {tuple, list} or any(
        type(item) is not CapturedSemanticContent for item in captured_content
    ):
        raise TypeError("semantic_case_captured_content_invalid")
    if len(captured_content) > MAX_CAPTURED_SEMANTIC_CONTENT_PARTS:
        raise ValueError("semantic_case_captured_content_over_limit")
    captured_input_bytes = sum(len(item.content) for item in captured_content)
    if captured_input_bytes > MAX_CAPTURED_SEMANTIC_INPUT_BYTES:
        raise ValueError("semantic_case_captured_content_over_limit")
    if (
        captured_content_scope is not None
        and type(captured_content_scope) is not CapturedContentScope
    ):
        raise TypeError("semantic_case_capture_scope_invalid")
    if type(captured_content_gaps) not in {tuple, list} or len(captured_content_gaps) > 16:
        raise TypeError("semantic_case_capture_gaps_invalid")
    capture_gaps = tuple(captured_content_gaps)
    if any(
        type(gap) is not str or _CAPTURE_GAP_PATTERN.fullmatch(gap) is None for gap in capture_gaps
    ) or capture_gaps != tuple(sorted(set(capture_gaps), key=str.encode)):
        raise ValueError("semantic_case_capture_gaps_invalid")
    if captured_content and captured_content_scope is None:
        # Content is an explicitly scoped disclosure. A bare byte sequence cannot enter a case,
        # even when it happens to match a durable evidence digest.
        raise ValueError("semantic_case_capture_scope_required")
    if review_context_profile is not ReviewContextProfile.CUSTOM:
        expected = ReviewSelectionPolicy.for_profile(review_context_profile)
        if review_selection != expected:
            # Custom overlays may meet() narrower; allow any selection that is a meet of profile.
            meet = expected.meet(review_selection)
            if meet != review_selection:
                raise ValueError("review_selection_profile_mismatch")

    selection = review_selection
    sections = frozenset(selection.sections)
    frontier_refs = frozenset(str(ref) for ref in frozen_case.allowed_ids)
    local_check_refs = frozenset(str(item.finding_id) for item in findings)
    # A recheck re-derives a live recorded finding under its recorded id (issue #186), so the same
    # fnd_ ref is both frozen ledger material and one of this run's findings. It is this check's
    # own finding: the packet names it as assessment.finding_ref and post-validation resolves any
    # cited fnd_ against this run's findings. Own the overlap locally so the two sets stay a
    # partition of `allowed` and the assessment survives the boundary fence (issue #304).
    frontier_refs = frontier_refs - local_check_refs
    allowed = frontier_refs | local_check_refs
    projection = frozen_case.projection

    captured_groups, captured_gaps = _captured_content_groups(
        projection,
        allowed,
        captured_content,
        captured_content_scope,
    )
    capture_gap_set = {*capture_gaps, *captured_gaps}
    if captured_content_scope is not None and "content_unselected" not in capture_gap_set:
        supplied_objects = {item.object_ref.object_id for item in captured_content}
        for record in projection.evidence.values():
            payload = record.payload
            if (
                payload is not None
                and payload.captured_object_id is not None
                and str(payload.captured_object_id) not in supplied_objects
            ):
                capture_gap_set.add("content_capture_unavailable")
    capture_gaps = tuple(sorted(capture_gap_set, key=str.encode))
    captured_group_leader: dict[str, str] = {}
    for leader, group in captured_groups.items():
        for evidence_ref in group.evidence_refs:
            captured_group_leader[evidence_ref] = leader

    items: list[SemanticCaseItem] = []
    # Item ids whose recorded text was admitted by the publish-side prose bound and then could
    # not be carried whole by the case. Folded into the packet coverage below so the omission is
    # an author-visible fact rather than a silent shortening (issue #177).
    over_limit: set[str] = set()
    omissions: list[ReviewOmission] = []
    goal_ids: list[str] = []
    obligation_ids: list[str] = []
    claim_ids: list[str] = []
    decision_ids: list[str] = []
    timeline_ids: list[str] = []
    targeted: list[TargetedExcerptRef] = []
    changes: list[ChangeObservation] = []

    # --- Goal (latest plan summary) ---
    if projection.plans:
        latest_version = max(projection.plans)
        plan = projection.plans[latest_version]
        plan_ref = str(plan.source_event_id)
        if "goal" in sections and plan.payload is not None and not plan.redacted:
            text, fit = _bounded_json(cast(Mapping[str, JsonValue], encode_payload(plan.payload)))
            item = _content_item(
                item_id=f"goal-{latest_version}",
                section="goal",
                category=DataCategory.TASK_DESCRIPTION,
                source_kind="task",
                source_ref=plan_ref,
                linked_subject_refs=(plan_ref,) if plan_ref in allowed else (),
                occurred_order=plan.source_frontier,
                text=text,
                over_limit=over_limit,
            )
            items.append(item)
            goal_ids.append(item.item_id)
            if fit != "whole":
                over_limit.add(item.item_id)
            if fit == "replaced":
                omissions.append(
                    _omit(plan_ref, DataCategory.TASK_DESCRIPTION, "task", "not_selected")
                )
        elif "goal" not in sections:
            omissions.append(_omit(plan_ref, DataCategory.TASK_DESCRIPTION, "task", "not_selected"))
        elif plan.redacted:
            omissions.append(
                _omit(plan_ref, DataCategory.TASK_DESCRIPTION, "task", "redacted_never_send")
            )
        else:
            omissions.append(_omit(plan_ref, DataCategory.TASK_DESCRIPTION, "task", "not_recorded"))

    # --- Obligations ---
    for obligation_id, record in sorted(
        projection.obligations.items(), key=lambda pair: str(pair[0])
    ):
        ref = str(obligation_id)
        if ref not in allowed and str(record.source_event_id) not in allowed:
            continue
        source_ref = ref if ref in allowed else str(record.source_event_id)
        if "obligations" not in sections:
            omissions.append(
                _omit(source_ref, DataCategory.OBLIGATION_TEXT, "obligation", "not_selected")
            )
            continue
        payload = record.payload
        if record.redacted or payload is None:
            omissions.append(
                _omit(
                    source_ref,
                    DataCategory.OBLIGATION_TEXT,
                    "obligation",
                    "redacted_never_send" if record.redacted else "not_recorded",
                )
            )
            continue
        assert type(payload) is ObligationPublishedPayload
        linked = tuple(
            sorted(
                {
                    source_ref,
                    *(str(item) for item in payload.source_refs if str(item) in allowed),
                },
                key=str.encode,
            )
        )[:16]
        text, fit = _bounded_json(cast(Mapping[str, JsonValue], encode_payload(payload)))
        item = _content_item(
            item_id=f"obligation-{ref}",
            section="obligation",
            category=DataCategory.OBLIGATION_TEXT,
            source_kind="obligation",
            source_ref=source_ref,
            linked_subject_refs=linked if linked else (source_ref,),
            occurred_order=record.source_frontier,
            text=text,
            over_limit=over_limit,
        )
        items.append(item)
        obligation_ids.append(item.item_id)
        if fit != "whole":
            over_limit.add(item.item_id)
        if fit == "replaced":
            omissions.append(
                _omit(source_ref, DataCategory.OBLIGATION_TEXT, "obligation", "not_selected")
            )

    # --- Claims ---
    for claim_id, record in effective_claim_items(projection):
        ref = str(claim_id)
        if ref not in allowed:
            continue
        if "claims" not in sections:
            omissions.append(_omit(ref, DataCategory.CLAIM_TEXT, "claim", "not_selected"))
            continue
        payload = record.payload
        if record.redacted or payload is None:
            omissions.append(
                _omit(
                    ref,
                    DataCategory.CLAIM_TEXT,
                    "claim",
                    "redacted_never_send" if record.redacted else "not_recorded",
                )
            )
            continue
        assert type(payload) in {ClaimRecordedPayload, ClaimRecordedPayloadV1_1}
        text, fit = _bounded_json(cast(Mapping[str, JsonValue], encode_payload(payload)))
        item = _content_item(
            item_id=f"claim-{ref}",
            section="claim",
            category=DataCategory.CLAIM_TEXT,
            source_kind="claim",
            source_ref=ref,
            linked_subject_refs=(ref,),
            occurred_order=record.source_frontier,
            text=text,
            over_limit=over_limit,
        )
        items.append(item)
        claim_ids.append(item.item_id)
        if fit != "whole":
            over_limit.add(item.item_id)
        if fit == "replaced":
            omissions.append(_omit(ref, DataCategory.CLAIM_TEXT, "claim", "not_selected"))

        if "change_observations" in sections and payload.subject_state is not None:
            changes.append(
                ChangeObservation(
                    subject_refs=(ref,),
                    claimed_change=True,
                    subject_state_relation=SubjectStateRelation.UNKNOWN,
                    content_visibility=(
                        "available" if "targeted_excerpts" in sections else "not_selected"
                    ),
                )
            )

    # --- Decisions ---
    for event_id, record in sorted(projection.decisions.items(), key=lambda pair: str(pair[0])):
        ref = str(event_id)
        if ref not in allowed:
            continue
        if "decisions" not in sections:
            omissions.append(_omit(ref, DataCategory.DECISION_EXCERPT, "decision", "not_selected"))
            continue
        payload = record.payload
        if record.redacted or payload is None:
            omissions.append(
                _omit(
                    ref,
                    DataCategory.DECISION_EXCERPT,
                    "decision",
                    "redacted_never_send" if record.redacted else "not_recorded",
                )
            )
            continue
        assert type(payload) is DecisionRecordedPayload
        text, fit = _bounded_json(cast(Mapping[str, JsonValue], encode_payload(payload)))
        item = _content_item(
            item_id=f"decision-{ref}",
            section="decision",
            category=DataCategory.DECISION_EXCERPT,
            source_kind="decision",
            source_ref=ref,
            linked_subject_refs=(ref,),
            occurred_order=record.source_frontier,
            text=text,
            over_limit=over_limit,
        )
        items.append(item)
        decision_ids.append(item.item_id)
        if fit != "whole":
            over_limit.add(item.item_id)
        if fit == "replaced":
            omissions.append(_omit(ref, DataCategory.DECISION_EXCERPT, "decision", "not_selected"))

    # --- Frozen accepted-event history ---
    if "timeline" in sections and frozen_case.history_availability == "available":
        detailed_history = bool({"goal", "obligations", "claims", "decisions"} & sections)
        reserve_window_item = bool(frozen_case.history_omitted_before_count)
        history_limit = max(0, selection.max_timeline_items - (1 if reserve_window_item else 0))
        history_rows = frozen_case.history[-history_limit:] if history_limit else ()
        omitted_rows = frozen_case.history[: len(frozen_case.history) - len(history_rows)]
        for history_item in omitted_rows:
            source_kind, category = _HISTORY_KIND[history_item.schema_name]
            omissions.append(
                _omit(str(history_item.event_id), category, source_kind, "not_selected")
            )
        if reserve_window_item and selection.max_timeline_items:
            first_sequence = history_rows[0].ingestion_sequence if history_rows else 1
            item = _content_item(
                item_id="history-window",
                section="timeline",
                category=DataCategory.BOUNDED_STRUCTURAL_METADATA,
                source_kind="task",
                source_ref="history-window",
                linked_subject_refs=(),
                occurred_order=max(0, first_sequence - 1),
                text=_structural_json(
                    {
                        "kind": "history_window",
                        "omitted_before_count": frozen_case.history_omitted_before_count,
                        "reason": "not_selected",
                    }
                ),
                over_limit=over_limit,
            )
            items.append(item)
            timeline_ids.append(item.item_id)
        for history_item in history_rows:
            source_kind, content_category = _HISTORY_KIND[history_item.schema_name]
            include_content = detailed_history and history_item.content_visibility == "available"
            category = (
                content_category if include_content else DataCategory.BOUNDED_STRUCTURAL_METADATA
            )
            text, fit = _history_json(
                history_item,
                include_content=include_content,
                include_exact_command_text=selection.include_exact_command_text,
            )
            event_ref = str(history_item.event_id)
            item = _content_item(
                item_id=f"history-{event_ref}",
                section="timeline",
                category=category,
                source_kind=source_kind,
                source_ref=event_ref,
                linked_subject_refs=(event_ref,) if event_ref in allowed else (),
                occurred_order=history_item.ingestion_sequence,
                text=text,
                over_limit=over_limit,
            )
            items.append(item)
            timeline_ids.append(item.item_id)
            if history_item.content_visibility != "available":
                omissions.append(
                    _omit(
                        event_ref,
                        content_category,
                        source_kind,
                        history_item.content_visibility,
                    )
                )
            else:
                if fit != "whole":
                    over_limit.add(item.item_id)
                if not detailed_history or fit == "replaced":
                    omissions.append(
                        _omit(event_ref, content_category, source_kind, "not_selected")
                    )

    if (
        "timeline" in sections
        and frozen_case.history_availability == "not_recorded"
        and projection.plans
    ):
        latest_plan = projection.plans[max(projection.plans)]
        omissions.append(
            _omit(
                str(latest_plan.source_event_id),
                DataCategory.BOUNDED_STRUCTURAL_METADATA,
                "task",
                "not_recorded",
            )
        )

    # --- Projection fallback timeline for legacy/synthetic frozen cases ---
    if "timeline" in sections and frozen_case.history_availability == "not_recorded":
        timeline_candidates: list[tuple[int, str, _SourceKind, str, Mapping[str, JsonValue]]] = []
        for obligation_id, record in projection.obligations.items():
            timeline_candidates.append(
                (
                    record.source_frontier,
                    str(record.source_event_id),
                    "obligation",
                    str(obligation_id)
                    if str(obligation_id) in allowed
                    else str(record.source_event_id),
                    {
                        "kind": "obligation_published",
                        "obligation_id": str(obligation_id),
                        "source_event_id": str(record.source_event_id),
                        "status": (
                            record.payload.status.value
                            if record.payload is not None
                            else "unavailable"
                        ),
                    },
                )
            )
        for claim_id, record in projection.claims.items():
            timeline_candidates.append(
                (
                    record.source_frontier,
                    str(record.source_event_id),
                    "claim",
                    str(claim_id),
                    {
                        "kind": "claim_recorded",
                        "claim_id": str(claim_id),
                        "source_event_id": str(record.source_event_id),
                        "claim_kind": (
                            record.payload.claim_kind.value
                            if record.payload is not None
                            else "unavailable"
                        ),
                    },
                )
            )
        for action_id, record in projection.actions.items():
            timeline_candidates.append(
                (
                    record.source_frontier,
                    str(record.source_event_id),
                    "action",
                    str(action_id) if str(action_id) in allowed else str(record.source_event_id),
                    {
                        "kind": "action_recorded",
                        "action_id": str(action_id),
                        "source_event_id": str(record.source_event_id),
                        "action_kind": (
                            record.payload.action_kind.value
                            if record.payload is not None
                            else "unavailable"
                        ),
                    },
                )
            )
        for result_id, record in projection.results.items():
            timeline_candidates.append(
                (
                    record.source_frontier,
                    str(record.source_event_id),
                    "result",
                    str(result_id) if str(result_id) in allowed else str(record.source_event_id),
                    {
                        "kind": "result_recorded",
                        "result_id": str(result_id),
                        "source_event_id": str(record.source_event_id),
                        "outcome": (
                            record.payload.outcome.value
                            if record.payload is not None
                            else "unavailable"
                        ),
                    },
                )
            )
        for evidence_id, record in projection.evidence.items():
            timeline_candidates.append(
                (
                    record.source_frontier,
                    str(record.source_event_id),
                    "evidence",
                    str(evidence_id)
                    if str(evidence_id) in allowed
                    else str(record.source_event_id),
                    {
                        "kind": "evidence_recorded",
                        "evidence_id": str(evidence_id),
                        "source_event_id": str(record.source_event_id),
                        "evidence_kind": (
                            record.payload.evidence_kind.value
                            if record.payload is not None
                            else "unavailable"
                        ),
                    },
                )
            )
        for event_id, record in projection.decisions.items():
            timeline_candidates.append(
                (
                    record.source_frontier,
                    str(record.source_event_id),
                    "decision",
                    str(event_id),
                    {
                        "kind": "decision_recorded",
                        "source_event_id": str(event_id),
                    },
                )
            )
        # Always record the frozen frontier as a structural anchor.
        timeline_candidates.append(
            (
                frozen_case.frontier.sequence,
                f"frontier-{frozen_case.frontier.sequence}",
                "task",
                f"frontier-{frozen_case.frontier.sequence}",
                {
                    "kind": "frontier",
                    "sequence": frozen_case.frontier.sequence,
                    "head_digest": frozen_case.frontier.head_digest,
                    "dependency_digest": dependency_digest,
                },
            )
        )
        timeline_candidates.sort(
            key=lambda row: (row[0], row[1].encode("ascii"), row[3].encode("ascii"))
        )
        # item_id is timeline-{source_ref}; duplicate source_ref would invalidate the case.
        seen_timeline_refs: set[str] = set()
        deduped_timeline: list[tuple[int, str, _SourceKind, str, Mapping[str, JsonValue]]] = []
        for row in timeline_candidates:
            source_ref = row[3]
            if source_ref in seen_timeline_refs:
                continue
            seen_timeline_refs.add(source_ref)
            deduped_timeline.append(row)
        for order, _event, source_kind, source_ref, body in deduped_timeline[
            : selection.max_timeline_items
        ]:
            linked = (source_ref,) if source_ref in allowed else ()
            item = _content_item(
                item_id=f"timeline-{source_ref}",
                section="timeline",
                category=DataCategory.BOUNDED_STRUCTURAL_METADATA,
                source_kind=source_kind,
                source_ref=source_ref,
                linked_subject_refs=linked,
                occurred_order=order,
                text=_structural_json(body),
                over_limit=over_limit,
            )
            items.append(item)
            timeline_ids.append(item.item_id)

    # --- The prior review's missing-item request and what was recorded since (issue #907) ---
    prior_missing_item: SemanticCaseItem | None = None
    pending_missing = projection.pending_missing_for_assessment
    if pending_missing is not None and "timeline" in sections and selection.max_timeline_items:
        answered = supplied_since(
            projection,
            pending_missing,
            frozenset(allowed),
            frozenset(str(item) for item in frozen_case.observation_event_ids),
            _edit_paths_by_ref(captured_groups),
        )
        check_ref = str(pending_missing.source_check_event_id)
        prior_missing_item = _content_item(
            item_id="prior-missing-for-assessment",
            section="timeline",
            category=DataCategory.BOUNDED_STRUCTURAL_METADATA,
            source_kind="finding",
            source_ref=check_ref,
            linked_subject_refs=(check_ref,) if check_ref in allowed else (),
            occurred_order=pending_missing.source_frontier,
            text=_structural_json(
                {
                    "items": [
                        {
                            "availability": item.availability,
                            "kind": item.kind,
                            "supplied_since": list(supplied),
                            "target_refs": [ref for ref in item.target_refs if ref in allowed],
                        }
                        for item, supplied in zip(pending_missing.items, answered, strict=True)
                    ],
                    "kind": "prior_missing_for_assessment",
                    "source_check_event_id": check_ref,
                }
            ),
            over_limit=over_limit,
            limit=MAX_SEMANTIC_ITEM_BYTES,
        )
        items.append(prior_missing_item)

    # --- Local assessments + optional finding prose ---
    review_assessments: list[ReviewAssessment] = []
    finding_refs_over_limit = False
    if "deterministic_assessments" in sections:
        matched = _match_assessments(frozen_case, findings)
        for finding, assessment in matched[: selection.max_assessments]:
            summary_id: str | None = None
            detail_id: str | None = None
            if selection.include_finding_prose:
                finding_ref = str(finding.finding_id)
                linked = tuple(str(ref) for ref in finding.subject_refs)
                # Prose requires exact-match allowlist on every subject_ref; otherwise keep the
                # local assessment without summary/detail content items.
                if linked and len(linked) > MAX_SEMANTIC_ITEM_SUBJECT_REFS:
                    # A finding may cite up to 64 subjects; one case item links at most 16. The
                    # complete tuple used to reach SemanticCaseItem and fail its bound, which
                    # surfaced as coordinator_failure with no review at all (issue #858). Slicing
                    # the tuple would present a partial subject list as the finding's own, so the
                    # prose is omitted whole and named: the finding keeps its identity in
                    # local_check_refs and the check result, the omission says which category was
                    # withheld, and coverage carries the capacity reason. The projected
                    # assessment below skips itself for the same width with its own omission.
                    omissions.append(
                        _omit(finding_ref, DataCategory.FINDING_SUMMARY, "finding", "not_selected")
                    )
                    finding_refs_over_limit = True
                elif linked and set(linked) <= allowed:
                    summary_id = f"finding-summary-{finding_ref}"
                    detail_id = f"finding-detail-{finding_ref}"
                    items.append(
                        _content_item(
                            item_id=summary_id,
                            section="deterministic_summary",
                            category=DataCategory.FINDING_SUMMARY,
                            source_kind="finding",
                            source_ref=finding_ref,
                            linked_subject_refs=linked,
                            occurred_order=finding.subject_frontier.sequence,
                            text=finding.summary,
                            over_limit=over_limit,
                        )
                    )
                    items.append(
                        _content_item(
                            item_id=detail_id,
                            section="deterministic_detail",
                            category=DataCategory.FINDING_SUMMARY,
                            source_kind="finding",
                            source_ref=finding_ref,
                            linked_subject_refs=linked,
                            occurred_order=finding.subject_frontier.sequence,
                            text=finding.detail,
                            over_limit=over_limit,
                        )
                    )
            projected = project_review_assessment(
                assessment,
                str(finding.finding_id),
                summary_item_id=summary_id,
                detail_item_id=detail_id,
            )
            if type(projected) is ReviewAssessment:
                review_assessments.append(projected)
            elif type(projected) is ReviewAssessmentSkipped:
                omissions.append(projected.omission)

    # --- Targeted excerpts (recorded text only; never fetch objects) ---
    if "targeted_excerpts" in sections and selection.max_excerpts > 0:
        excerpts = _select_targeted_excerpts(
            projection=projection,
            allowed=allowed,
            selection=selection,
            findings=findings,
            review_assessments=review_assessments,
            captured_groups=captured_groups,
            captured_group_leader=captured_group_leader,
            excerpt_byte_budget=(
                selection.max_total_excerpt_bytes
                if excerpt_byte_budget is None
                else min(selection.max_total_excerpt_bytes, excerpt_byte_budget)
            ),
        )
        items.extend(excerpts.items)
        targeted.extend(excerpts.targeted)
        omissions.extend(excerpts.omissions)
        capture_gap_set.update(excerpts.gaps)
        over_limit.update(excerpts.over_limit)

    lineage_items: tuple[SemanticCaseItem, ...] = ()
    if lineage_evaluation is not None:
        # Lineage is an independent C9 channel.  Keep every part in the provider packet even
        # when the review selection caps ordinary timeline rows; it is structural authority for
        # this parent check, not child prose selected by the review profile.
        lineage_items = _lineage_semantic_items(lineage_evaluation)
        items.extend(lineage_items)

    # Cap lists per selection.  Reserve one timeline slot per lineage part.  Dropped ordinary
    # timeline items are removed below with the rest of the post-cap catalog so SemanticCase's
    # referenced-item invariant remains exact.
    # A valid capture can exist even when a custom policy disables the excerpt section entirely or
    # sets its excerpt count to zero. Keep that policy exclusion visible just as we do for a group
    # rejected by kind/relevance, while retaining only bounded identity metadata in the omission.
    if captured_groups and ("targeted_excerpts" not in sections or selection.max_excerpts == 0):
        capture_gap_set.add("content_unselected")
        for ref, captured_group in sorted(
            captured_groups.items(), key=lambda pair: pair[0].encode("ascii")
        ):
            omissions.append(
                _omit(
                    ref,
                    DataCategory.EVIDENCE_EXCERPT,
                    captured_group.source_kind,
                    "not_selected",
                )
            )

    # Cap lists per selection.
    goal_ids = goal_ids[:4]
    obligation_ids = obligation_ids[:32]
    claim_ids = claim_ids[:32]
    decision_ids = decision_ids[:16]
    timeline_ids = timeline_ids[: selection.max_timeline_items]
    if lineage_items:
        timeline_ids = timeline_ids[: max(0, selection.max_timeline_items - len(lineage_items))]
        timeline_ids.extend(item.item_id for item in lineage_items)
    if prior_missing_item is not None:
        # The prior request holds its own timeline slot: without it the reviewer cannot tell a
        # supplied item from one still missing, which is the loop issue #907 closes.
        timeline_ids = [
            item_id for item_id in timeline_ids if item_id != prior_missing_item.item_id
        ]
        if len(timeline_ids) >= selection.max_timeline_items:
            ordinary = [
                item_id
                for item_id in timeline_ids
                if item_id not in {item.item_id for item in lineage_items}
            ]
            if ordinary:
                timeline_ids.remove(ordinary[-1])
        if len(timeline_ids) < selection.max_timeline_items:
            timeline_ids.append(prior_missing_item.item_id)
    review_assessments = review_assessments[: selection.max_assessments]
    changes = changes[: selection.max_change_observations]
    targeted = targeted[: selection.max_excerpts]

    kind_order = {
        kind: ordinal
        for ordinal, kind in enumerate(
            (
                FindingKind.COMPLETION_WITH_OPEN_OBLIGATIONS,
                FindingKind.REQUESTED_ITEM_NEVER_ATTEMPTED,
                FindingKind.FAILED_WORK_OMITTED,
                FindingKind.CLAIM_WITHOUT_ADMISSIBLE_EVIDENCE,
                FindingKind.RESULT_WITHOUT_ACTION,
                FindingKind.ACTION_WITHOUT_RESULT,
                FindingKind.STALE_EVIDENCE_FOR_CHANGED_STATE,
                FindingKind.CONTRADICTORY_CLAIMS_UNRESOLVED,
                FindingKind.LEDGER_STALE_OR_INCOMPLETE,
                FindingKind.WEAK_OR_STALE_RESPONSE,
                FindingKind.EVIDENCE_DOES_NOT_SUPPORT_CLAIM,
                FindingKind.DIFF_DOES_NOT_MATCH_ACCOUNT,
                FindingKind.MATERIAL_LIMITATION_OMITTED,
                FindingKind.QUESTIONABLE_FINDING_REJECTION,
            )
        )
    }
    review_assessments.sort(
        key=lambda item: (
            # The finding registry is extensible by coordination lanes.  Keep the historical
            # review order for the core kinds, while placing a newly registered kind after that
            # stable prefix instead of raising a KeyError during semantic case construction.
            kind_order.get(item.finding_kind, len(kind_order)),
            tuple(ref.encode("ascii") for ref in item.subject_refs),
        )
    )
    changes.sort(key=lambda item: tuple(ref.encode("ascii") for ref in item.subject_refs))
    omissions = sorted(
        set(omissions),
        key=lambda item: (
            item.subject_ref.encode("ascii"),
            item.category.value.encode("ascii"),
            item.reason.encode("ascii"),
        ),
    )[: selection.max_omissions]

    # Keep only items that remain referenced after caps.
    keep_ids = (
        set(goal_ids) | set(obligation_ids) | set(claim_ids) | set(decision_ids) | set(timeline_ids)
    )
    for assessment in review_assessments:
        if assessment.summary_item_id is not None and assessment.detail_item_id is not None:
            keep_ids.add(assessment.summary_item_id)
            keep_ids.add(assessment.detail_item_id)
    for excerpt in targeted:
        keep_ids.add(excerpt.excerpt_item_id)

    items = [item for item in items if item.item_id in keep_ids]

    # --- Earlier AI-powered findings and the main agent's answers (issue #905) ---
    # The reviewer has no memory between checks; this ledger-backed section is the channel. It
    # rides the findings selection (never a new disclosure category): structural rows always,
    # prose only where the profile already sends finding prose. Its own bounds keep it out of the
    # 64-row timeline, and it takes only capacity the case bounds leave.
    prior_finding_ids: list[str] = []
    # A selection without the assessments section shows the reviewer no earlier finding at all;
    # when any is open, that is the same disclosed truncation as a full section.
    prior_findings_truncated = "deterministic_assessments" not in sections and bool(
        _prior_finding_candidates(projection, allowed)
    )
    if "deterministic_assessments" in sections:
        prior_items, prior_omissions, prior_findings_truncated = _prior_findings_section(
            projection,
            allowed,
            include_prose=selection.include_finding_prose,
            remaining_items=MAX_SEMANTIC_CASE_ITEMS - len(items),
            remaining_bytes=MAX_SEMANTIC_CASE_BYTES - sum(item.content_bytes for item in items),
        )
        items.extend(prior_items)
        prior_finding_ids = [item.item_id for item in prior_items]
        if prior_omissions:
            omissions = sorted(
                set([*omissions, *prior_omissions]),
                key=lambda item: (
                    item.subject_ref.encode("ascii"),
                    item.category.value.encode("ascii"),
                    item.reason.encode("ascii"),
                ),
            )[: selection.max_omissions]

    # Sort items per SemanticCase order.
    _SECTION_ORDINAL = {
        "goal": 0,
        "obligation": 1,
        "claim": 2,
        "decision": 3,
        "prior_finding": 4,
        "timeline": 5,
        "deterministic_summary": 6,
        "deterministic_detail": 7,
        "excerpt": 8,
    }
    items.sort(
        key=lambda item: (
            _SECTION_ORDINAL[item.section],
            item.occurred_order,
            item.source_ref.encode("ascii"),
            item.item_id.encode("ascii"),
        )
    )

    if not items:
        # Empty frozen case: still emit a frontier structural item so the case is valid.
        body = {
            "kind": "frontier",
            "sequence": frozen_case.frontier.sequence,
            "head_digest": frozen_case.frontier.head_digest,
            "dependency_digest": dependency_digest,
        }
        item = _content_item(
            item_id="timeline-frontier",
            section="timeline",
            category=DataCategory.BOUNDED_STRUCTURAL_METADATA,
            source_kind="task",
            source_ref=f"frontier-{frozen_case.frontier.sequence}",
            linked_subject_refs=(),
            occurred_order=frozen_case.frontier.sequence,
            text=_structural_json(body),
            over_limit=over_limit,
        )
        items = [item]
        timeline_ids = [item.item_id]

    if lineage_items and sum(item.content_bytes for item in items) > MAX_SEMANTIC_CASE_BYTES:
        # Every lineage part may fit individually while their sum, or their sum with
        # selected parent content, exceeds SemanticCase's independent aggregate bound.
        # Refuse with the same typed pre-dispatch outcome instead of leaking the
        # constructor's generic semantic_case_invalid ValueError to the coordinator.
        raise LineageSemanticCapacityExceeded("lineage_semantic_case_too_large")

    capture_gaps = tuple(sorted(capture_gap_set, key=str.encode))
    coverage = case_coverage(frozen_case, semantic=True)
    if capture_gaps:
        coverage = replace(
            coverage,
            ledger_freshness=(
                LedgerFreshness.PARTIAL
                if coverage.ledger_freshness is LedgerFreshness.CURRENT
                else coverage.ledger_freshness
            ),
            known_gaps=tuple(sorted({*coverage.known_gaps, *capture_gaps}, key=str.encode)),
        )
    # Count only overflow on items the caps kept: an item dropped downstream is already disclosed
    # as an omission, and naming it here would report a shortening the reviewer never saw.
    if over_limit & {item.item_id for item in items}:
        coverage = replace(
            coverage,
            ledger_freshness=(
                LedgerFreshness.PARTIAL
                if coverage.ledger_freshness is LedgerFreshness.CURRENT
                else coverage.ledger_freshness
            ),
            known_gaps=tuple(
                sorted(
                    {*coverage.known_gaps, SEMANTIC_CASE_CONTENT_OVER_ITEM_LIMIT_GAP},
                    key=str.encode,
                )
            ),
        )
    if finding_refs_over_limit:
        # The finding is still a local check result the reviewer can cite; only its prose and
        # projected basis are absent from this case. Coverage says so, as with shortened prose.
        coverage = replace(
            coverage,
            ledger_freshness=(
                LedgerFreshness.PARTIAL
                if coverage.ledger_freshness is LedgerFreshness.CURRENT
                else coverage.ledger_freshness
            ),
            known_gaps=tuple(
                sorted(
                    {*coverage.known_gaps, SEMANTIC_CASE_FINDING_REFS_OVER_LIMIT_GAP},
                    key=str.encode,
                )
            ),
        )
    if prior_findings_truncated:
        # Earlier findings the section could not carry are named as omissions above; coverage
        # says the reviewer saw only part of the dialogue.
        coverage = replace(
            coverage,
            ledger_freshness=(
                LedgerFreshness.PARTIAL
                if coverage.ledger_freshness is LedgerFreshness.CURRENT
                else coverage.ledger_freshness
            ),
            known_gaps=tuple(
                sorted(
                    {*coverage.known_gaps, SEMANTIC_PRIOR_FINDINGS_OVER_LIMIT_GAP},
                    key=str.encode,
                )
            ),
        )
    packet = ReviewPacket(
        goal_item_ids=tuple(goal_ids),
        obligation_item_ids=tuple(obligation_ids),
        claim_item_ids=tuple(claim_ids),
        decision_item_ids=tuple(decision_ids),
        timeline_item_ids=tuple(timeline_ids),
        deterministic_assessments=tuple(review_assessments),
        change_observations=tuple(changes),
        coverage=coverage,
        targeted_excerpts=tuple(targeted),
        omissions=tuple(omissions),
        prior_finding_item_ids=tuple(prior_finding_ids),
    )

    # The local case owns the complete frontier. The reviewer needs the dependency
    # closure of its selected packet, not every unrelated logical/source ID in that frontier.
    required_refs: set[str] = set(local_check_refs) | {
        str(ref) for ref in projection.findings if str(ref) in allowed
    }

    def retain(value: JsonValue) -> None:
        if isinstance(value, str):
            if value in allowed:
                required_refs.add(value)
        elif isinstance(value, dict):
            for child in value.values():
                retain(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                retain(child)

    retain(_packet_to_json(packet))
    retain(cast(JsonValue, _item_catalog_json(items)))
    for item in items:
        if item.section == "excerpt":
            continue
        # Canonical payload/structural items carry typed dependencies beyond their short
        # linked_subject_refs catalog. Prose and captured byte excerpts are not parsed as IDs.
        try:
            content = strict_json_parse(item.content)
        except ValueError:
            continue
        retain(content)
    # Follow typed recorded payload dependencies to a fixed point. This keeps support,
    # limitations, availability and provenance connected without emitting unselected prose.
    records_by_ref = {
        str(ref): record
        for family in (
            projection.obligations,
            projection.actions,
            projection.results,
            projection.evidence,
            projection.claims,
            projection.findings,
        )
        for ref, record in family.items()
    }
    visited: set[str] = set()
    while pending := required_refs - visited:
        for ref in sorted(pending):
            visited.add(ref)
            record = records_by_ref.get(ref)
            if record is None:
                continue
            if str(record.source_event_id) in allowed:
                required_refs.add(str(record.source_event_id))
            if record.payload is not None and not record.redacted:
                retain(cast(JsonValue, encode_payload(record.payload)))
    selected_frontier_refs = frozenset(required_refs & frontier_refs)
    omitted_reference_count = len(frontier_refs - selected_frontier_refs)
    frontier_refs = selected_frontier_refs
    if omitted_reference_count:
        packet = replace(
            packet,
            coverage=replace(
                packet.coverage,
                ledger_freshness=LedgerFreshness.PARTIAL,
                known_gaps=tuple(
                    sorted(
                        {*packet.coverage.known_gaps, "semantic_reference_scope_reduced"},
                        key=str.encode,
                    )
                ),
            ),
        )

    selection_digest = review_selection_digest(selection)
    # Bind assessments/omissions/packet lists into the digest so provenance covers the full case.
    case_digest = canonical_digest(
        cast(
            JsonValue,
            {
                "dependency_digest": dependency_digest,
                "frontier_refs": sorted(frontier_refs),
                "omitted_reference_count": omitted_reference_count,
                "items": [
                    {
                        "content_digest": item.content_digest,
                        "item_id": item.item_id,
                        "occurred_order": item.occurred_order,
                        "section": item.section,
                        **_freshness_json(item),
                    }
                    for item in items
                ],
                "local_check_refs": sorted(local_check_refs),
                "policy_id": policy_id,
                "policy_version": policy_version,
                "question_set": list(_QUESTION_SET),
                "review_context_profile": review_context_profile.value,
                "review_packet": _packet_to_json(packet),
                "review_selection_digest": selection_digest,
                "schema": "yoetz.semantic-case/1",
                "subject_frontier": dict(frozen_case.frontier.as_wire()),
                "captured_content_gaps": list(capture_gaps),
                "captured_content_scope": (
                    None
                    if captured_content_scope is None
                    else {
                        "authorized_profiles": list(captured_content_scope.authorized_profiles),
                        "phase_bindings": [
                            {"evidence_ref": ref, "phase_identity": phase}
                            for ref, phase in captured_content_scope.phase_bindings
                        ],
                        "session_id": captured_content_scope.session_id,
                        "task_id": captured_content_scope.task_id,
                        "workspace_commitment": captured_content_scope.workspace_commitment,
                    }
                ),
                "captured_content": [
                    {
                        "capture_profile": item.capture_profile,
                        "content_digest": item.manifest.content_digest,
                        "content_kind": item.manifest.content_kind.value,
                        "correlation_identity": item.manifest.correlation_identity,
                        "envelope_digest": item.object_ref.envelope_digest,
                        "object_id": item.object_ref.object_id,
                        "part_count": item.manifest.part_count,
                        "part_index": item.manifest.part_index,
                        "phase_identity": item.phase_identity,
                        "source_commitment": item.manifest.source_commitment,
                    }
                    for item in sorted(
                        captured_content,
                        key=lambda item: (
                            item.object_ref.object_id.encode("ascii"),
                            item.manifest.part_index,
                        ),
                    )
                ],
            },
        )
    )

    return SemanticCase(
        case_id=case_id,
        subject_frontier=frozen_case.frontier,
        dependency_digest=dependency_digest,
        frontier_refs=frontier_refs,
        local_check_refs=local_check_refs,
        review_context_profile=review_context_profile,
        review_selection=selection,
        policy_id=policy_id,
        policy_version=policy_version,
        packet=packet,
        items=tuple(items),
        question_set=_QUESTION_SET,
        case_digest=case_digest,
        omitted_reference_count=omitted_reference_count,
    )


def _assessment_to_json(assessment: ReviewAssessment) -> dict[str, JsonValue]:
    body: dict[str, JsonValue] = {
        "coverage_gaps": list(assessment.coverage_gaps),
        "finding_kind": assessment.finding_kind.value,
        "finding_ref": str(assessment.finding_ref),
        "observed_facts": [
            {"fact_code": fact.fact_code, "subject_refs": list(fact.subject_refs)}
            for fact in assessment.observed_facts
        ],
        "priority": assessment.priority,
        "required_but_missing_facts": [
            {"fact_code": fact.fact_code, "subject_refs": list(fact.subject_refs)}
            for fact in assessment.required_but_missing_facts
        ],
        "rule_id": assessment.rule_id,
        "source_availability": assessment.source_availability.value,
        "subject_refs": list(assessment.subject_refs),
        "subject_state_relation": assessment.subject_state_relation.value,
        "supporting_refs": list(assessment.supporting_refs),
    }
    if assessment.summary_item_id is not None and assessment.detail_item_id is not None:
        body["summary_item_id"] = assessment.summary_item_id
        body["detail_item_id"] = assessment.detail_item_id
    return body


def _digest_provenance_json(provenance: ExcerptDigestProvenance) -> dict[str, JsonValue]:
    return {
        "byte_count": provenance.byte_count,
        "content_availability": provenance.content_availability.value,
        "content_digest": provenance.content_digest,
        "digest_subject": provenance.digest_subject.value,
        "evidence_kind": provenance.evidence_kind.value,
        "provenance": provenance.provenance.value,
        "strength": provenance.strength.value,
        **(
            {}
            if provenance.approval_commitment is None
            else {"approval_commitment": provenance.approval_commitment}
        ),
        **(
            {}
            if provenance.approved_check_result_digest is None
            else {"approved_check_result_digest": provenance.approved_check_result_digest}
        ),
    }


def _packet_to_json(packet: ReviewPacket) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "change_observations": [
                {
                    "claimed_change": item.claimed_change,
                    "content_visibility": item.content_visibility,
                    "subject_refs": list(item.subject_refs),
                    "subject_state_relation": item.subject_state_relation.value,
                    **(
                        {
                            "after_state_digest": item.after_state_digest,
                            "before_state_digest": item.before_state_digest,
                        }
                        if item.before_state_digest is not None
                        else {}
                    ),
                }
                for item in packet.change_observations
            ],
            "claim_item_ids": list(packet.claim_item_ids),
            "coverage": coverage_to_json(packet.coverage),
            "decision_item_ids": list(packet.decision_item_ids),
            "deterministic_assessments": [
                _assessment_to_json(item) for item in packet.deterministic_assessments
            ],
            "goal_item_ids": list(packet.goal_item_ids),
            "obligation_item_ids": list(packet.obligation_item_ids),
            "prior_finding_item_ids": list(packet.prior_finding_item_ids),
            "omissions": [
                {
                    "category": item.category.value,
                    "reason": item.reason,
                    "source_kind": item.source_kind,
                    "subject_ref": item.subject_ref,
                }
                for item in packet.omissions
            ],
            "targeted_excerpts": [
                {
                    "content_bytes": item.content_bytes,
                    "content_digest": item.content_digest,
                    "content_visibility": item.content_visibility,
                    **(
                        {}
                        if item.digest_provenance is None
                        else {"digest_provenance": _digest_provenance_json(item.digest_provenance)}
                    ),
                    "excerpt_item_id": item.excerpt_item_id,
                    "linked_subject_refs": list(item.linked_subject_refs),
                    "source_kind": item.source_kind,
                    "subject_state_relation": item.subject_state_relation.value,
                }
                for item in packet.targeted_excerpts
            ],
            "timeline_item_ids": list(packet.timeline_item_ids),
        },
    )


def _freshness_json(item: SemanticCaseItem) -> dict[str, JsonValue]:
    """Excerpt freshness marks; absent on every item that has none."""

    body: dict[str, JsonValue] = {}
    if item.latest_for is not None:
        body["latest_for"] = item.latest_for
    if item.superseded_by:
        body["superseded_by"] = list(item.superseded_by)
    return body


def _item_catalog_json(items: Sequence[SemanticCaseItem]) -> list[dict[str, JsonValue]]:
    """Metadata-only catalog so egress can project without reverse-engineering origin_ref.

    ``occurred_order`` is the ledger ingestion order of the item's source, so the reviewer can
    tell earlier material from later material without trusting the opaque item ids (#907).
    """

    return [
        cast(
            dict[str, JsonValue],
            {
                "category": item.category.value,
                "content_bytes": item.content_bytes,
                "content_digest": item.content_digest,
                "item_id": item.item_id,
                "linked_subject_refs": list(item.linked_subject_refs),
                "occurred_order": item.occurred_order,
                "section": item.section,
                "source_kind": item.source_kind,
                "source_ref": item.source_ref,
                **_freshness_json(item),
            },
        )
        for item in items
    ]


def _case_envelope_json(case: SemanticCase) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "case_digest": case.case_digest,
            "case_id": case.case_id,
            "dependency_digest": case.dependency_digest,
            "frontier_refs": sorted(case.frontier_refs),
            "omitted_reference_count": str(case.omitted_reference_count),
            "item_catalog": _item_catalog_json(case.items),
            "local_check_refs": sorted(case.local_check_refs),
            "policy_id": case.policy_id,
            "policy_version": case.policy_version,
            "question_set": list(case.question_set),
            "review_context_profile": case.review_context_profile.value,
            "review_packet": _packet_to_json(case.packet),
            "review_selection_digest": review_selection_digest(case.review_selection),
            "schema": _PACKET_SCHEMA,
            "subject_frontier": dict(case.subject_frontier.as_wire()),
        },
    )


def assemble_filtered_review_packet(
    envelope: Mapping[str, object],
    *,
    content_by_id: Mapping[str, bytes],
    included_item_ids: frozenset[str] | set[str],
) -> bytes:
    """Assemble ``yoetz.review-packet-case/2`` from a builder envelope + approved content.

    Single shared projection used by the application prepared-payload path and the privacy
    enforcer. Filters by approved item id only; never re-derives section/source metadata from
    origin pointers.
    """

    included = set(included_item_ids)
    frontier_raw = envelope.get("frontier_refs")
    local_raw = envelope.get("local_check_refs")
    frontier_refs: set[str] = (
        {item for item in cast(list[object], frontier_raw) if type(item) is str}
        if type(frontier_raw) is list
        else set()
    )
    local_check_refs: set[str] = (
        {item for item in cast(list[object], local_raw) if type(item) is str}
        if type(local_raw) is list
        else set()
    )
    allowed: set[str] = frontier_refs | local_check_refs

    catalog_raw = envelope.get("item_catalog")
    catalog: list[dict[str, object]] = []
    if type(catalog_raw) is list:
        for row in cast(list[object], catalog_raw):
            if isinstance(row, dict):
                catalog.append(cast(dict[str, object], row))

    content_rows: list[dict[str, JsonValue]] = []
    omitted_extra: list[dict[str, JsonValue]] = []
    for meta in catalog:
        item_id = meta.get("item_id")
        if type(item_id) is not str or item_id == REVIEW_PACKET_ITEM_ID:
            continue
        category = meta.get("category")
        source_kind = meta.get("source_kind")
        source_ref = meta.get("source_ref")
        section = meta.get("section")
        linked_raw = meta.get("linked_subject_refs")
        linked = (
            [ref for ref in cast(list[object], linked_raw) if type(ref) is str]
            if type(linked_raw) is list
            else []
        )
        if item_id in included and item_id in content_by_id:
            plaintext = content_by_id[item_id]
            try:
                text = plaintext.decode("utf-8")
            except UnicodeDecodeError:
                continue
            occurred_order = meta.get("occurred_order")
            latest_for = meta.get("latest_for")
            superseded_raw = meta.get("superseded_by")
            superseded = (
                [ref for ref in cast(list[object], superseded_raw) if type(ref) is str]
                if type(superseded_raw) is list
                else []
            )
            content_rows.append(
                cast(
                    dict[str, JsonValue],
                    {
                        "category": category if type(category) is str else "",
                        "content": text,
                        "content_bytes": len(plaintext),
                        "content_digest": "sha256:" + hashlib.sha256(plaintext).hexdigest(),
                        "item_id": item_id,
                        "linked_subject_refs": linked,
                        "occurred_order": occurred_order if type(occurred_order) is int else 0,
                        "section": section if type(section) is str else "timeline",
                        "source_kind": source_kind if type(source_kind) is str else "task",
                        "source_ref": source_ref if type(source_ref) is str else item_id,
                        **({"latest_for": latest_for} if type(latest_for) is str else {}),
                        **({"superseded_by": cast(JsonValue, superseded)} if superseded else {}),
                    },
                )
            )
            continue
        if item_id in included:
            continue
        # Prefer allowlisted source_ref, then any allowlisted linked ref; skip only if none.
        subject: str | None = None
        if type(source_ref) is str and source_ref in allowed:
            subject = source_ref
        else:
            for ref in linked:
                if ref in allowed:
                    subject = ref
                    break
        if subject is None:
            continue
        omitted_extra.append(
            cast(
                dict[str, JsonValue],
                {
                    "category": category if type(category) is str else "",
                    "reason": "withheld_by_policy",
                    "source_kind": source_kind if type(source_kind) is str else "task",
                    "subject_ref": subject,
                },
            )
        )
    # Rows keep the case's own order: section, then recording order. Sorting by item id put
    # excerpts in the order of their random evidence ids, so a superseded hunk could read as the
    # newest one (issue #907).

    packet_raw = envelope.get("review_packet")
    packet_obj: dict[str, JsonValue] = (
        cast(dict[str, JsonValue], dict(cast(dict[object, object], packet_raw)))
        if isinstance(packet_raw, dict)
        else {}
    )

    # Filter by what the document will actually carry — approved *and* catalogued. Filtering by
    # approval alone let an id survive here whose catalog row bounding had already removed, so the
    # packet pointed at an item absent from ``items``.
    carried = {cast(str, row["item_id"]) for row in content_rows}

    def _filter_ids(raw: object) -> list[JsonValue]:
        if type(raw) is not list:
            return []
        return [
            item_id
            for item_id in cast(list[object], raw)
            if type(item_id) is str and item_id in carried
        ]

    for key in _PACKET_ID_LIST_KEYS:
        packet_obj[key] = _filter_ids(packet_obj.get(key))

    excerpts_raw = packet_obj.get("targeted_excerpts")
    if type(excerpts_raw) is list:
        packet_obj["targeted_excerpts"] = cast(
            JsonValue,
            [
                row
                for row in cast(list[object], excerpts_raw)
                if isinstance(row, dict)
                and cast(dict[str, object], row).get("excerpt_item_id") in carried
            ],
        )
    else:
        packet_obj["targeted_excerpts"] = cast(JsonValue, [])

    assessments_raw = packet_obj.get("deterministic_assessments")
    filtered_assessments: list[JsonValue] = []
    if type(assessments_raw) is list:
        for raw in cast(list[object], assessments_raw):
            if not isinstance(raw, dict):
                continue
            row = dict(cast(dict[str, JsonValue], cast(dict[object, object], raw)))
            summary = row.get("summary_item_id")
            detail = row.get("detail_item_id")
            if type(summary) is str and type(detail) is str:
                if summary not in carried or detail not in carried:
                    row.pop("summary_item_id", None)
                    row.pop("detail_item_id", None)
            filtered_assessments.append(row)
    packet_obj["deterministic_assessments"] = filtered_assessments

    base_omissions: list[dict[str, JsonValue]] = []
    omissions_raw = packet_obj.get("omissions")
    if type(omissions_raw) is list:
        for raw in cast(list[object], omissions_raw):
            if isinstance(raw, dict):
                base_omissions.append(
                    cast(dict[str, JsonValue], dict(cast(dict[object, object], raw)))
                )

    seen: set[tuple[str, str, str]] = set()
    omissions: list[JsonValue] = []
    for row in [*base_omissions, *omitted_extra]:
        subject_ref = row.get("subject_ref")
        category = row.get("category")
        reason = row.get("reason")
        if type(subject_ref) is not str or type(category) is not str or type(reason) is not str:
            continue
        key = (subject_ref, category, reason)
        if key in seen:
            continue
        if subject_ref not in allowed:
            continue
        seen.add(key)
        omissions.append(row)
    omissions.sort(
        key=lambda row: (
            cast(str, cast(dict[str, JsonValue], row)["subject_ref"]).encode("ascii"),
            cast(str, cast(dict[str, JsonValue], row)["category"]).encode("ascii"),
            cast(str, cast(dict[str, JsonValue], row)["reason"]).encode("ascii"),
        )
    )
    packet_obj["omissions"] = omissions

    # Preserve change_observations / coverage as supplied by the builder envelope.
    if "change_observations" not in packet_obj:
        packet_obj["change_observations"] = cast(JsonValue, [])
    if "coverage" not in packet_obj:
        packet_obj["coverage"] = cast(JsonValue, {})

    # Approved content whose catalog row is absent cannot be described (the catalog *is* its
    # metadata), so it cannot travel. Counting it keeps the omission visible instead of letting
    # the packet read as though that material was never approved.
    uncatalogued = sum(
        1
        for item_id in included
        if item_id != REVIEW_PACKET_ITEM_ID and item_id in content_by_id and item_id not in carried
    )
    accounting_raw = envelope.get("selection_accounting")
    accounting: dict[str, JsonValue] = (
        cast(dict[str, JsonValue], dict(cast(dict[object, object], accounting_raw)))
        if isinstance(accounting_raw, dict)
        else {
            "assessment_links_stripped_count": "0",
            "catalog_dropped_count": "0",
            "change_observations_dropped_count": "0",
            "deterministic_assessments_dropped_count": "0",
            "omissions_dropped_count": "0",
            "reason": "not_minimized",
            "targeted_excerpts_dropped_count": "0",
        }
    )
    accounting["uncatalogued_approved_count"] = str(uncatalogued)

    document = cast(
        dict[str, JsonValue],
        {
            "case_digest": envelope.get("case_digest", ""),
            "case_id": envelope.get("case_id", ""),
            # The exact ids post-validation will accept in a challenge's cited_refs, in one place
            # the reviewer can read. The packet already carried them, split across frontier_refs
            # and local_check_refs, while items[].item_id — the ids most visible in the document —
            # are not citable at all. Naming the accept set explicitly is what lets a reviewer cite
            # correctly instead of guessing and having the challenge dropped.
            "citable_refs": sorted(frontier_refs | local_check_refs),
            "omitted_reference_count": envelope.get("omitted_reference_count", "0"),
            "selection_accounting": cast(JsonValue, accounting),
            "dependency_digest": envelope.get("dependency_digest", ""),
            "frontier_refs": sorted(frontier_refs),
            "items": content_rows,
            "local_check_refs": sorted(local_check_refs),
            "policy_id": envelope.get("policy_id", ""),
            "policy_version": envelope.get("policy_version", ""),
            "question_set": (
                list(cast(list[object], envelope["question_set"]))
                if type(envelope.get("question_set")) is list
                else []
            ),
            "review_context_profile": envelope.get("review_context_profile", ""),
            "review_packet": packet_obj,
            "review_selection_digest": envelope.get("review_selection_digest", ""),
            "schema": _PACKET_SCHEMA,
            "subject_frontier": (
                dict(cast(dict[object, object], envelope["subject_frontier"]))
                if isinstance(envelope.get("subject_frontier"), dict)
                else {}
            ),
        },
    )
    return canonical_encode(cast(JsonValue, document))


class SemanticCaseTooLarge(ValueError):
    """The structural envelope cannot be reduced within its bound.

    Raised only when the irreducible core alone exceeds ``MAX_EGRESS_ENVELOPE_BYTES``. Every
    droppable row has already been removed and accounted for by then, so this is a genuine
    "this case cannot be reviewed", not a transient coordinator fault. Callers must map it to a
    terminal AI-powered review outcome rather than swallowing it as an unexpected exception.
    """


def _catalog_rows(envelope: Mapping[str, JsonValue]) -> list[dict[str, JsonValue]]:
    raw = envelope.get("item_catalog")
    if type(raw) is not list:
        return []
    return [
        cast(dict[str, JsonValue], row) for row in cast(list[object], raw) if isinstance(row, dict)
    ]


def _catalog_item_ids(envelope: Mapping[str, JsonValue]) -> frozenset[str]:
    ids: set[str] = set()
    for row in _catalog_rows(envelope):
        item_id = row.get("item_id")
        if type(item_id) is str:
            ids.add(item_id)
    return frozenset(ids)


def _drop_catalog_row(envelope: dict[str, JsonValue]) -> bool:
    """Remove the lowest-priority catalog row and every reference that would dangle.

    Catalog order is the case's own section/occurrence order, so the tail is the least
    structurally load-bearing row. Dropping a row without also dropping its ids would leave
    ``*_item_ids`` and ``targeted_excerpts`` pointing at an item the packet no longer carries.
    """

    rows = _catalog_rows(envelope)
    if not rows:
        return False
    dropped = rows.pop()
    envelope["item_catalog"] = cast(JsonValue, rows)
    dropped_id = dropped.get("item_id")
    if type(dropped_id) is not str:
        return True
    packet_obj = envelope.get("review_packet")
    if not isinstance(packet_obj, dict):
        return True
    for key in _PACKET_ID_LIST_KEYS:
        current = packet_obj.get(key)
        if type(current) is list:
            packet_obj[key] = cast(
                JsonValue,
                [
                    value
                    for value in cast(list[object], current)
                    if not (type(value) is str and value == dropped_id)
                ],
            )
    excerpts = packet_obj.get("targeted_excerpts")
    if type(excerpts) is list:
        packet_obj["targeted_excerpts"] = cast(
            JsonValue,
            [
                row
                for row in cast(list[object], excerpts)
                if not (
                    isinstance(row, dict)
                    and cast(dict[str, object], row).get("excerpt_item_id") == dropped_id
                )
            ],
        )
    for key in ("summary_item_id", "detail_item_id"):
        assessments = packet_obj.get("deterministic_assessments")
        if type(assessments) is not list:
            continue
        for raw in cast(list[object], assessments):
            if isinstance(raw, dict) and cast(dict[str, object], raw).get(key) == dropped_id:
                cast(dict[str, object], raw).pop(key, None)
    return True


def _drop_prior_finding_rows(envelope: dict[str, JsonValue]) -> int:
    """Remove prior-finding rows, oldest finding first, until the envelope fits (issue #905).

    The dialogue section yields before any work content: every other row keeps exactly the
    bounding it had without the section. A finding's rows leave together, its ids leave the
    packet, and the packet coverage names the truncation.
    """

    rows = _catalog_rows(envelope)
    order: list[str] = []
    for row in rows:
        source = row.get("source_ref")
        if row.get("section") == "prior_finding" and type(source) is str and source not in order:
            order.append(source)
    dropped = 0
    packet_obj = envelope.get("review_packet")
    for source in order:
        if len(canonical_encode(cast(JsonValue, envelope))) <= MAX_EGRESS_ENVELOPE_BYTES:
            break
        removed = {
            cast(str, row.get("item_id"))
            for row in rows
            if row.get("section") == "prior_finding" and row.get("source_ref") == source
        }
        rows = [row for row in rows if row.get("item_id") not in removed]
        envelope["item_catalog"] = cast(JsonValue, rows)
        dropped += len(removed)
        if isinstance(packet_obj, dict):
            current = packet_obj.get("prior_finding_item_ids")
            if type(current) is list:
                packet_obj["prior_finding_item_ids"] = cast(
                    JsonValue,
                    [value for value in cast(list[object], current) if value not in removed],
                )
            coverage = packet_obj.get("coverage")
            if isinstance(coverage, dict):
                gaps = coverage.get("known_gaps")
                if type(gaps) is list and SEMANTIC_PRIOR_FINDINGS_OVER_LIMIT_GAP not in gaps:
                    coverage["known_gaps"] = cast(
                        JsonValue,
                        sorted(
                            [*cast(list[str], gaps), SEMANTIC_PRIOR_FINDINGS_OVER_LIMIT_GAP],
                            key=str.encode,
                        ),
                    )
    return dropped


def _fit_packet_section(
    envelope: dict[str, JsonValue],
    reductions: dict[str, int],
    key: str,
    accounting_key: str,
) -> bytes | None:
    """Find the first fitting suffix reduction, or remove this section completely."""

    packet_obj = envelope.get("review_packet")
    if not isinstance(packet_obj, dict):
        return None
    rows = packet_obj.get(key)
    if type(rows) is not list or not rows:
        return None
    original_rows = cast(list[JsonValue], rows)
    total = len(original_rows)
    prior_count = reductions[accounting_key]

    def encode_after_dropping(count: int) -> bytes:
        packet_obj[key] = original_rows[: total - count]
        reductions[accounting_key] = prior_count + count
        _set_selection_accounting(envelope, reductions)
        return canonical_encode(cast(JsonValue, envelope))

    best = encode_after_dropping(total)
    if len(best) > MAX_EGRESS_ENVELOPE_BYTES:
        return None
    low, high = 1, total
    # Removing a row cannot increase encoded size: the row/comma bytes offset
    # any extra counter digit. Search the same first-fitting point as the old
    # one-row-at-a-time loop, including its exact selection accounting.
    while low < high:
        middle = (low + high) // 2
        candidate = encode_after_dropping(middle)
        if len(candidate) <= MAX_EGRESS_ENVELOPE_BYTES:
            high = middle
            best = candidate
        else:
            low = middle + 1
    return best


def _strip_assessment_links(envelope: dict[str, JsonValue]) -> int:
    """Drop assessment item-id links, which duplicate ids the catalog already carries."""

    packet_obj = envelope.get("review_packet")
    if not isinstance(packet_obj, dict):
        return 0
    rows = packet_obj.get("deterministic_assessments")
    if type(rows) is not list:
        return 0
    removed = 0
    for raw in cast(list[object], rows):
        if not isinstance(raw, dict):
            continue
        row = cast(dict[str, object], raw)
        for key in ("summary_item_id", "detail_item_id"):
            if row.pop(key, None) is not None:
                removed += 1
    return removed


def _set_selection_accounting(
    envelope: dict[str, JsonValue], reductions: Mapping[str, int]
) -> None:
    """Record what bounding removed, so a truncated packet can never read as a complete one."""

    minimized = any(count > 0 for count in reductions.values())
    envelope["selection_accounting"] = cast(
        JsonValue,
        {
            "assessment_links_stripped_count": str(reductions["assessment_links_stripped_count"]),
            "catalog_dropped_count": str(reductions["catalog_dropped_count"]),
            "change_observations_dropped_count": str(
                reductions["change_observations_dropped_count"]
            ),
            "deterministic_assessments_dropped_count": str(
                reductions["deterministic_assessments_dropped_count"]
            ),
            "omissions_dropped_count": str(reductions["omissions_dropped_count"]),
            "reason": "size_minimized" if minimized else "not_minimized",
            "targeted_excerpts_dropped_count": str(reductions["targeted_excerpts_dropped_count"]),
        },
    )


def bounded_case_envelope(case: SemanticCase) -> bytes:
    """Canonical case envelope guaranteed to fit ``MAX_EGRESS_ENVELOPE_BYTES``.

    The previous implementation was a fixed three-stage ladder whose last stage truncated the
    catalog to 64 rows — dead code, because a ``SemanticCase`` already admits at most 64 items.
    A real 44 KiB case therefore reduced to 38 KiB and then raised, stranding the whole check.

    Packet sections keep their declared priority and exact first-fitting suffix, found with a
    bounded search instead of re-encoding the whole document after every removed row. Catalog
    fallback still removes one row at a time with its reference cleanup. Anything removed is
    counted in ``selection_accounting`` so the packet and its receipt disclose minimization.
    """

    envelope = _case_envelope_json(case)
    reductions = {
        "assessment_links_stripped_count": 0,
        "catalog_dropped_count": 0,
        "change_observations_dropped_count": 0,
        "deterministic_assessments_dropped_count": 0,
        "omissions_dropped_count": 0,
        "targeted_excerpts_dropped_count": 0,
    }
    _set_selection_accounting(envelope, reductions)
    encoded = canonical_encode(cast(JsonValue, envelope))
    if len(encoded) <= MAX_EGRESS_ENVELOPE_BYTES:
        return encoded

    # Earlier findings yield before any work content (issue #905).
    reductions["catalog_dropped_count"] += _drop_prior_finding_rows(envelope)
    _set_selection_accounting(envelope, reductions)
    encoded = canonical_encode(cast(JsonValue, envelope))
    if len(encoded) <= MAX_EGRESS_ENVELOPE_BYTES:
        return encoded

    reductions["assessment_links_stripped_count"] += _strip_assessment_links(envelope)
    _set_selection_accounting(envelope, reductions)
    encoded = canonical_encode(cast(JsonValue, envelope))
    if len(encoded) <= MAX_EGRESS_ENVELOPE_BYTES:
        return encoded
    for key, accounting_key in (
        ("change_observations", "change_observations_dropped_count"),
        ("targeted_excerpts", "targeted_excerpts_dropped_count"),
        ("omissions", "omissions_dropped_count"),
        ("deterministic_assessments", "deterministic_assessments_dropped_count"),
    ):
        fitted = _fit_packet_section(envelope, reductions, key, accounting_key)
        if fitted is not None:
            return fitted
    while _drop_catalog_row(envelope):
        reductions["catalog_dropped_count"] += 1
        _set_selection_accounting(envelope, reductions)
        encoded = canonical_encode(cast(JsonValue, envelope))
        if len(encoded) <= MAX_EGRESS_ENVELOPE_BYTES:
            return encoded
    raise SemanticCaseTooLarge("semantic_case_envelope_too_large")


@dataclass(frozen=True, slots=True)
class SemanticPacketView:
    """What the reviewer is actually shown, for fencing its per-finding rulings (issue #905)."""

    prior_finding_refs: frozenset[str]
    citable_refs: frozenset[str]
    # Envelope bounding removed prior-finding rows the case had admitted.
    prior_findings_trimmed: bool


def semantic_case_packet_view(case: SemanticCase) -> SemanticPacketView:
    """The earlier findings the reviewer's packet carries, and the refs it offers as citable.

    A ruling may only speak for a finding whose prior-findings row survived envelope bounding,
    and only cite what ``citable_refs`` listed.
    """

    if type(case) is not SemanticCase:
        raise TypeError("semantic_case_invalid")
    try:
        catalogued = _catalog_item_ids(
            cast(Mapping[str, JsonValue], strict_json_parse(bounded_case_envelope(case)))
        )
    except SemanticCaseTooLarge:
        catalogued = frozenset(item.item_id for item in case.items)
    carried = frozenset(
        item.source_ref
        for item in case.items
        if item.section == "prior_finding" and item.item_id == f"prior-finding-{item.source_ref}"
    )
    prior = frozenset(ref for ref in carried if f"prior-finding-{ref}" in catalogued)
    return SemanticPacketView(
        prior, frozenset(case.frontier_refs | case.local_check_refs), prior != carried
    )


def semantic_case_to_candidate_context(
    case: SemanticCase,
    *,
    request_id: str,
    scope: AuthorizationScope,
    provider_binding: ProviderBinding,
) -> CandidateContext:
    """Project an AI-powered review case into separate privacy-classified candidate items."""

    if type(case) is not SemanticCase:
        raise TypeError("semantic_case_invalid")
    envelope = bounded_case_envelope(case)
    # The catalog is the authority for what the provider payload may carry, so an item bounding
    # removed from the catalog must not travel as a candidate item either. Offering content whose
    # catalog row is gone would get it approved by privacy and then silently discarded during
    # assembly — the packet would claim coverage it never had.
    catalogued = _catalog_item_ids(cast(Mapping[str, JsonValue], strict_json_parse(envelope)))

    items: list[CandidateContextItem] = [
        CandidateContextItem(
            REVIEW_PACKET_ITEM_ID,
            DataCategory.BOUNDED_STRUCTURAL_METADATA,
            scope,
            "/case/review-packet",
            envelope,
        )
    ]
    for item in case.items:
        if item.item_id not in catalogued:
            continue
        items.append(
            CandidateContextItem(
                item.item_id,
                item.category,
                scope,
                f"/case/{item.section}/{item.item_id}",
                item.content,
            )
        )
    return CandidateContext(
        request_id=request_id,
        channel=EgressChannel.LLM_INFERENCE,
        local_sink=None,
        purpose=SEMANTIC_REVIEW_PURPOSE,
        scope=scope,
        subject_digest=case.case_digest,
        provider_binding=provider_binding,
        items=tuple(items),
    )


def semantic_case_to_prepared_payload(
    case: SemanticCase,
    included_item_ids: frozenset[str] | set[str],
) -> bytes:
    """Assemble the provider-facing review-packet document from privacy-approved items."""

    if type(case) is not SemanticCase:
        raise TypeError("semantic_case_invalid")
    content_by_id = {item.item_id: item.content for item in case.items}
    # Assemble from the same bounded envelope that was offered for authorization, never the
    # unbounded one: the prepared payload must describe exactly what privacy approved.
    envelope = cast(Mapping[str, JsonValue], strict_json_parse(bounded_case_envelope(case)))
    return assemble_filtered_review_packet(
        envelope,
        content_by_id=content_by_id,
        included_item_ids=included_item_ids,
    )
