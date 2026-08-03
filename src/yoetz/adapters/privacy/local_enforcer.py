"""Deterministic local privacy classification, minimization, and exact-byte scan."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from typing import Final, Protocol, cast

from yoetz.application.semantic_case import (
    REVIEW_PACKET_ITEM_ID,
    assemble_filtered_review_packet,
)
from yoetz.domain.privacy import (
    CandidateContext,
    CandidateContextItem,
    ClassifiedContext,
    ClassifiedContextItem,
    DataClass,
    DisclosureProvenance,
    ForbiddenDataKind,
    LocalDisclosureSink,
    PrivacyDecision,
    ProjectionProvenanceContext,
)
from yoetz.observability.privacy import scan_for_sensitive_content
from yoetz.ports.privacy import EffectivePrivacyPolicy, MinimizedDisclosure
from yoetz.protocol.canonical import JsonValue, canonical_encode, strict_json_parse
from yoetz.protocol.models import DataCategory

__all__ = [
    "EGRESS_BYTES_PER_TOKEN_ESTIMATE",
    "ClassificationRuleset",
    "LocalPrivacyEnforcer",
    "MinimizationRuleset",
    "ProvenanceRuleset",
    "ReviewSelectionRuleset",
    "SecretScanRuleset",
    "TrustedProvenanceResolver",
    "estimated_token_count",
    "scan_exact_bytes",
]

# The whole-case token ceiling in ChannelPolicy is compared against this estimate, so anything
# that publishes a token budget has to derive it the same way or the two ceilings silently
# disagree. Four bytes per token is the usual rough estimate for this content.
EGRESS_BYTES_PER_TOKEN_ESTIMATE: Final = 4


def estimated_token_count(byte_count: int) -> int:
    """Estimate the token count a channel ``max_tokens`` ceiling is compared against."""

    if type(byte_count) is not int or byte_count < 0:
        raise ValueError("egress_byte_count_invalid")
    return (byte_count + EGRESS_BYTES_PER_TOKEN_ESTIMATE - 1) // EGRESS_BYTES_PER_TOKEN_ESTIMATE


_SCANNER_REGISTRY_VERSION = "observability-sensitive-content-v1"
_SCANNER_PROFILE_DIGEST = "sha256:75d5e5545aec001901b1370f502120114b583662fd473229e545553154f4d605"
_STRUCTURAL_CATEGORIES = frozenset(
    {DataCategory.BOUNDED_STRUCTURAL_METADATA, DataCategory.DECLARED_FILE_TYPE}
)
_FORBIDDEN_SOURCE_PREFIXES: tuple[tuple[str, ForbiddenDataKind], ...] = (
    ("credential:", ForbiddenDataKind.CREDENTIAL_FILE),
    ("environment:", ForbiddenDataKind.UNRELATED_ENVIRONMENT),
    ("keyring:", ForbiddenDataKind.KEYRING_CONTENT),
    ("out_of_scope:", ForbiddenDataKind.OUT_OF_SCOPE_FILE),
    ("raw_database:", ForbiddenDataKind.RAW_DATABASE),
    ("raw_log:", ForbiddenDataKind.UNRESTRICTED_LOG),
    ("raw_stderr:", ForbiddenDataKind.RAW_STDERR),
    ("transcript:", ForbiddenDataKind.COMPLETE_TRANSCRIPT),
    ("vault:", ForbiddenDataKind.HIDDEN_AUTH_CONFIGURATION),
)


@dataclass(frozen=True, slots=True)
class ClassificationRuleset:
    version: str = "privacy-classification-v1"


@dataclass(frozen=True, slots=True)
class ProvenanceRuleset:
    version: str = "privacy-provenance-v1"


@dataclass(frozen=True, slots=True)
class ReviewSelectionRuleset:
    version: str = "privacy-review-selection-v1"


@dataclass(frozen=True, slots=True)
class MinimizationRuleset:
    version: str = "privacy-minimization-v1"


@dataclass(frozen=True, slots=True)
class SecretScanRuleset:
    version: str = _SCANNER_REGISTRY_VERSION
    profile_digest: str = _SCANNER_PROFILE_DIGEST


class TrustedProvenanceResolver(Protocol):
    """Resolve a frozen-frontier ledger fact; ``None`` means ambiguous and denies widening."""

    def resolve(
        self,
        context: ProjectionProvenanceContext,
        candidate: CandidateContext,
        item: CandidateContextItem,
    ) -> DisclosureProvenance | None: ...


def scan_exact_bytes(data: bytes) -> tuple[ForbiddenDataKind, ...]:
    """Map the shared observability scanner to the closed never-send vocabulary."""

    findings = scan_for_sensitive_content(data)
    mapped = {
        ForbiddenDataKind.PRIVATE_CERTIFICATE
        if finding.kind == "private_key_marker"
        else ForbiddenDataKind.API_CREDENTIAL
        for finding in findings
    }
    return tuple(sorted(mapped, key=lambda value: value.value.encode()))


_SEMANTIC_PACKET_SCHEMA = "yoetz.review-packet-case/1"


def _assemble_semantic_review_payload(
    classified: ClassifiedContext,
    included: tuple[ClassifiedContextItem, ...],
) -> bytes:
    """Assemble the versioned review-packet document from privacy-approved case items.

    The pre-egress builder supplies one structural ``review-packet`` envelope (with item catalog
    and packet metadata) plus separate categorized content items. Projection reuses the shared
    builder filter so section/source_kind/subject_ref are never reverse-engineered from origin_ref.
    """

    del classified  # catalog on the envelope is the authority; classified only supplied included.
    included_by_id = {item.candidate.item_id: item for item in included}
    envelope_item = included_by_id.get(REVIEW_PACKET_ITEM_ID)
    if envelope_item is None:
        # Structural envelope withheld or missing: fail closed to empty authorized payload shape
        # recognized by the coordinator as insufficient approved context when ids are empty.
        return canonical_encode(
            cast(
                JsonValue,
                {
                    "items": [],
                    "omissions": [],
                    "schema": _SEMANTIC_PACKET_SCHEMA,
                },
            )
        )
    try:
        envelope = strict_json_parse(envelope_item.candidate.plaintext)
    except Exception:
        return canonical_encode(
            cast(
                JsonValue,
                {
                    "items": [],
                    "omissions": [],
                    "schema": _SEMANTIC_PACKET_SCHEMA,
                },
            )
        )
    if not isinstance(envelope, dict):
        return canonical_encode(
            cast(JsonValue, {"items": [], "omissions": [], "schema": _SEMANTIC_PACKET_SCHEMA})
        )
    content_by_id = {
        item_id: item.candidate.plaintext
        for item_id, item in included_by_id.items()
        if item_id != REVIEW_PACKET_ITEM_ID
    }
    return assemble_filtered_review_packet(
        cast(dict[str, object], envelope),
        content_by_id=content_by_id,
        included_item_ids=set(included_by_id),
    )


class LocalPrivacyEnforcer:
    """Provider-free implementation of the deterministic privacy classifier port."""

    __slots__ = (
        "_classification",
        "_minimization",
        "_provenance",
        "_provenance_resolver",
        "_review_selection",
        "_scanner",
    )

    def __init__(
        self,
        *,
        provenance_resolver: TrustedProvenanceResolver | None = None,
        classification: ClassificationRuleset = ClassificationRuleset(),
        provenance: ProvenanceRuleset = ProvenanceRuleset(),
        review_selection: ReviewSelectionRuleset = ReviewSelectionRuleset(),
        minimization: MinimizationRuleset = MinimizationRuleset(),
        scanner: SecretScanRuleset = SecretScanRuleset(),
    ) -> None:
        self._provenance_resolver = provenance_resolver
        self._classification = classification
        self._provenance = provenance
        self._review_selection = review_selection
        self._minimization = minimization
        self._scanner = scanner

    def classify(
        self, candidate: CandidateContext, policy: EffectivePrivacyPolicy
    ) -> ClassifiedContext:
        if type(candidate) is not CandidateContext or type(policy) is not EffectivePrivacyPolicy:
            raise TypeError("privacy_classification_input_invalid")
        classified: list[ClassifiedContextItem] = []
        for item in candidate.items:
            source_findings = {
                kind
                for prefix, kind in _FORBIDDEN_SOURCE_PREFIXES
                if item.origin_ref.startswith(prefix)
            }
            source_findings.update(scan_exact_bytes(item.plaintext))
            scope_valid = candidate.scope.contains(item.source_scope)
            data_class = (
                DataClass.SECRET_OR_CRYPTOGRAPHIC
                if source_findings
                else DataClass.PUBLIC_STRUCTURAL
                if item.category in _STRUCTURAL_CATEGORIES
                else DataClass.ORDINARY_USER_CONTENT
            )
            resolved_provenance: DisclosureProvenance | None = None
            if candidate.local_sink is LocalDisclosureSink.AGENT_CONTEXT and not source_findings:
                resolver = self._provenance_resolver
                if resolver is not None and candidate.provenance_context is not None:
                    context = candidate.provenance_context
                    assert context is not None
                    resolved = resolver.resolve(context, candidate, item)
                    if resolved is not None and type(resolved) is not DisclosureProvenance:
                        raise ValueError("privacy_provenance_invalid")
                    resolved_provenance = resolved
            classified.append(
                ClassifiedContextItem(
                    candidate=item,
                    data_class=data_class,
                    forbidden_findings=tuple(
                        sorted(source_findings, key=lambda value: value.value.encode())
                    ),
                    scope_valid=scope_valid,
                    classifier_ruleset_version=self._classification.version,
                    provenance=resolved_provenance,
                )
            )
        return ClassifiedContext(candidate, tuple(classified))

    def minimize_and_scan(
        self, classified: ClassifiedContext, decision: PrivacyDecision
    ) -> MinimizedDisclosure:
        if type(classified) is not ClassifiedContext or type(decision) is not PrivacyDecision:
            raise TypeError("privacy_minimization_input_invalid")
        approved = set(decision.approved_item_ids)
        included = tuple(
            item
            for item in classified.items
            if item.candidate.item_id in approved
            and item.scope_valid
            and not item.forbidden_findings
            and item.data_class is not DataClass.SECRET_OR_CRYPTOGRAPHIC
        )
        if classified.candidate.purpose == "semantic-review":
            prepared = _assemble_semantic_review_payload(classified, included)
        else:
            rows = [
                {
                    "category": item.candidate.category.value,
                    "content_base64": base64.b64encode(item.candidate.plaintext).decode("ascii"),
                    "item_id": item.candidate.item_id,
                }
                for item in included
            ]
            prepared = canonical_encode(
                cast(JsonValue, {"items": rows, "schema": "yoetz.minimized-disclosure/1"})
            )
        findings = scan_exact_bytes(prepared)
        source_digests = tuple(
            sorted(
                {
                    f"sha256:{hashlib.sha256(item.candidate.plaintext).hexdigest()}"
                    for item in included
                },
                key=str.encode,
            )
        )
        included_ids = tuple(sorted((item.candidate.item_id for item in included), key=str.encode))
        approved_categories = tuple(
            sorted({item.candidate.category for item in included}, key=lambda value: value.value)
        )
        removed = len(classified.items) - len(included)
        return MinimizedDisclosure(
            prepared_bytes=prepared,
            included_item_ids=included_ids,
            source_item_digests=source_digests,
            approved_categories=approved_categories,
            blocked_categories=decision.blocked_categories,
            transformation_summary=(("minimized_items", removed),),
            byte_count=len(prepared),
            token_count=estimated_token_count(len(prepared)),
            case_digest=f"sha256:{hashlib.sha256(prepared).hexdigest()}",
            scanner_registry_version=self._scanner.version,
            scanner_profile_digest=self._scanner.profile_digest,
            forbidden_findings=findings,
        )

    def scan_exact_bytes(self, data: bytes) -> tuple[ForbiddenDataKind, ...]:
        return scan_exact_bytes(data)
