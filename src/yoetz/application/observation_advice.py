"""Build observation AdviceSnapshot from envelopes, optional inspect, and semantic add-ons."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Protocol

from yoetz.domain.findings import FindingId, finding_id
from yoetz.domain.observation import (
    AdviceSnapshot,
    ObservationEnvelope,
    ObservationLifecycle,
)
from yoetz.kernel.policies.observation_advice import (
    OBSERVATION_ADVICE_POLICY_ID,
    OBSERVATION_ADVICE_POLICY_VERSION,
    ObservationAdviceCandidate,
    ObservationAdviceContext,
    ObservationCheckFact,
    ObservationCompositionFact,
    ObservationInspectFact,
    evidence_basis_digest,
    observation_advice_findings,
)
from yoetz.protocol.coverage import (
    ArtifactObservation,
    AuthorshipAssurance,
    CheckType,
    Coverage,
    EvidenceImmutability,
    LedgerFreshness,
    PublicationChannel,
)
from yoetz.protocol.ids import PREFIX_BY_KIND, IdKind

__all__ = [
    "ObservationAdviceBuildInput",
    "ObservationAdviceSemanticAddon",
    "SemanticAdvicePort",
    "build_observation_advice_snapshot",
    "should_reissue_advice",
    "stable_advice_finding_id",
]

_SUPPRESSION_DOMAIN: Final = b"yoetz/observation-advice-suppress/v1\x00"
_FINDING_DOMAIN: Final = b"yoetz/observation-advice-finding/v1\x00"


@dataclass(frozen=True, slots=True)
class ObservationAdviceSemanticAddon:
    """Additive semantic advice identities already privacy-gated upstream."""

    finding_ids: tuple[FindingId, ...]
    evidence_digest: str | None
    next_action: str | None = None


class SemanticAdvicePort(Protocol):
    """Optional semantic advisor; never required for deterministic correctness guidance."""

    def review(
        self,
        *,
        evidence_packet: Mapping[str, object],
    ) -> ObservationAdviceSemanticAddon | None: ...


@dataclass(frozen=True, slots=True)
class ObservationAdviceBuildInput:
    envelopes: tuple[ObservationEnvelope, ...]
    lifecycle: ObservationLifecycle
    gaps: tuple[str, ...]
    check_facts: tuple[ObservationCheckFact, ...] = ()
    inspect_fact: ObservationInspectFact | None = None
    composition: ObservationCompositionFact | None = None
    plan_path_digests: tuple[str, ...] = ()
    prior_snapshot: AdviceSnapshot | None = None
    semantic_addon: ObservationAdviceSemanticAddon | None = None
    has_real_observation: bool = False


def stable_advice_finding_id(rule_code: str, detail_token: str, evidence_digest: str) -> FindingId:
    """Allocate a deterministic UUIDv4-shaped finding id for observation advice."""

    material = _FINDING_DOMAIN + f"{rule_code}\0{detail_token}\0{evidence_digest}".encode()
    digest = hashlib.sha256(material).digest()
    raw = bytearray(digest[:16])
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    return finding_id(PREFIX_BY_KIND[IdKind.FINDING] + str(uuid.UUID(bytes=bytes(raw))))


def _coverage(*, observation_qualified: bool, semantic: bool, gaps: Sequence[str]) -> Coverage:
    channels = (PublicationChannel.ENGINE_DERIVED,)
    authorship = AuthorshipAssurance.SERVICE_AUTHENTICATED
    observation = ArtifactObservation.PUBLISHED_ONLY
    if observation_qualified:
        channels = (PublicationChannel.HOOK_OBSERVED, PublicationChannel.ENGINE_DERIVED)
        authorship = AuthorshipAssurance.HARNESS_OBSERVED
        observation = ArtifactObservation.HOOK_OBSERVED
    checks = [CheckType.DETERMINISTIC]
    if semantic:
        checks.append(CheckType.SEMANTIC_MODEL_DERIVED)
    known = tuple(sorted({gap for gap in gaps if gap}, key=str.encode))
    freshness = LedgerFreshness.PARTIAL if known else LedgerFreshness.CURRENT
    return Coverage(
        publication_channels=tuple(sorted(channels, key=lambda item: item.value.encode("ascii"))),
        authorship_assurance=authorship,
        artifact_observation=observation,
        evidence_immutability=EvidenceImmutability.CONTENT_DIGEST,
        ledger_freshness=freshness,
        check_types=tuple(sorted(checks, key=lambda item: item.value.encode("ascii"))),
        known_gaps=known,
    )


def _suppression_identity(
    finding_ids: Sequence[FindingId],
    evidence_digest: str,
    next_action: str,
) -> str:
    material = _SUPPRESSION_DOMAIN + canonical_material(finding_ids, evidence_digest, next_action)
    digest = hashlib.sha256(material).hexdigest()
    return f"suppress-{digest[:48]}"


def canonical_material(
    finding_ids: Sequence[FindingId], evidence_digest: str, next_action: str
) -> bytes:
    joined = ",".join(str(item) for item in finding_ids)
    return f"{joined}\0{evidence_digest}\0{next_action}".encode()


def should_reissue_advice(
    prior: AdviceSnapshot | None,
    candidate: AdviceSnapshot,
    *,
    prior_severity: int | None = None,
    candidate_severity: int | None = None,
    unresolved_after_work: bool = False,
) -> bool:
    """Reissue when evidence changes, severity increases, or work left prior advice open."""

    if prior is None:
        return True
    if prior.suppression_identity == candidate.suppression_identity:
        return unresolved_after_work
    if prior.evidence_basis_digest != candidate.evidence_basis_digest:
        return True
    if (
        prior_severity is not None
        and candidate_severity is not None
        and candidate_severity < prior_severity
    ):
        # Lower priority number is higher severity in FindingKind traits.
        return True
    if unresolved_after_work:
        return True
    return prior.ranked_finding_ids != candidate.ranked_finding_ids


def _next_action(candidates: Sequence[ObservationAdviceCandidate]) -> str:
    if not candidates:
        return "reground_status"
    return candidates[0].next_action


def _freshness_frontier(envelopes: Sequence[ObservationEnvelope], evidence_digest: str) -> str:
    if not envelopes:
        return f"frontier-{evidence_digest.removeprefix('sha256:')[:24]}"
    last = envelopes[-1]
    return (
        f"frontier-g{last.cursor.source_generation}-"
        f"e{last.cursor.event_position}-"
        f"{evidence_digest.removeprefix('sha256:')[:16]}"
    )


def build_observation_advice_snapshot(
    input_value: ObservationAdviceBuildInput,
) -> AdviceSnapshot | None:
    """Return an AdviceSnapshot, or None when there is nothing actionable to surface."""

    if type(input_value) is not ObservationAdviceBuildInput:
        raise ValueError("observation_advice_invalid")
    context = ObservationAdviceContext(
        envelopes=input_value.envelopes,
        lifecycle=input_value.lifecycle,
        gaps=input_value.gaps,
        check_facts=input_value.check_facts,
        inspect_fact=input_value.inspect_fact,
        composition=input_value.composition,
        plan_path_digests=input_value.plan_path_digests,
    )
    candidates = observation_advice_findings(context)
    basis = evidence_basis_digest(
        candidates,
        input_value.envelopes,
        extra={
            "policy": f"{OBSERVATION_ADVICE_POLICY_ID}/{OBSERVATION_ADVICE_POLICY_VERSION}",
            "lifecycle": input_value.lifecycle.value,
        },
    )
    finding_ids = tuple(
        stable_advice_finding_id(item.rule_code, item.detail_token, basis) for item in candidates
    )
    semantic = input_value.semantic_addon
    semantic_ids: tuple[FindingId, ...] = ()
    if semantic is not None and semantic.finding_ids:
        semantic_ids = semantic.finding_ids
        if semantic.evidence_digest is not None:
            basis = evidence_basis_digest(
                candidates,
                input_value.envelopes,
                extra={
                    "policy": f"{OBSERVATION_ADVICE_POLICY_ID}/{OBSERVATION_ADVICE_POLICY_VERSION}",
                    "lifecycle": input_value.lifecycle.value,
                    "semantic_evidence": semantic.evidence_digest,
                },
            )
    ranked = finding_ids + tuple(item for item in semantic_ids if item not in finding_ids)
    if not ranked:
        # Zero cooperative publications still yield observation-gap advice when empty/degraded.
        return None
    next_action = (
        semantic.next_action
        if semantic is not None and semantic.next_action is not None and not candidates
        else _next_action(candidates)
    )
    # next_action must be a token; semantic addon may supply one.
    if type(next_action) is not str or not next_action:
        next_action = "reground_status"
    coverage = _coverage(
        observation_qualified=input_value.has_real_observation
        and input_value.lifecycle is ObservationLifecycle.ACTIVE,
        semantic=semantic is not None and bool(semantic_ids),
        gaps=input_value.gaps,
    )
    # Honest observation-qualified coverage: when envelopes exist but lifecycle is not active,
    # keep engine-derived coverage and include known gaps.
    if input_value.envelopes and not (
        input_value.has_real_observation and input_value.lifecycle is ObservationLifecycle.ACTIVE
    ):
        gap_set = set(coverage.known_gaps)
        gap_set.add("observation_qualified_partial")
        coverage = Coverage(
            publication_channels=coverage.publication_channels,
            authorship_assurance=AuthorshipAssurance.SERVICE_AUTHENTICATED,
            artifact_observation=ArtifactObservation.PUBLISHED_ONLY,
            evidence_immutability=coverage.evidence_immutability,
            ledger_freshness=LedgerFreshness.PARTIAL,
            check_types=coverage.check_types,
            known_gaps=tuple(sorted(gap_set, key=str.encode)),
        )
    suppression = _suppression_identity(ranked, basis, next_action)
    snapshot = AdviceSnapshot(
        ranked_finding_ids=ranked,
        evidence_basis_digest=basis,
        confidence_coverage=coverage,
        recommended_next_action=next_action,
        freshness_frontier=_freshness_frontier(input_value.envelopes, basis),
        suppression_identity=suppression,
    )
    if not should_reissue_advice(input_value.prior_snapshot, snapshot):
        return input_value.prior_snapshot
    return snapshot


def minimized_semantic_evidence_packet(
    candidates: Sequence[ObservationAdviceCandidate],
    basis_digest: str,
) -> dict[str, object]:
    """Build a minimized packet for optional semantic review (no repo/transcript/logs)."""

    return {
        "format": "yoetz.observation-advice-semantic/1",
        "policy": f"{OBSERVATION_ADVICE_POLICY_ID}/{OBSERVATION_ADVICE_POLICY_VERSION}",
        "evidence_basis_digest": basis_digest,
        "deterministic_rules": tuple(
            {
                "kind": item.kind.value,
                "rule_code": item.rule_code,
                "next_action": item.next_action,
                "evidence_ref_count": len(item.evidence_refs),
            }
            for item in candidates
        ),
    }
