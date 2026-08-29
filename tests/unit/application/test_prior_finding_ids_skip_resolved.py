"""A resolved finding is history: an issue that fires again after proof takes a fresh id.

``allocate_findings`` reuses a live recorded finding's id so a recheck converges on the record
already answered. Once a later qualifying check has resolved that row, reuse would silently
overwrite the proof; instead the re-fired issue becomes a successor under a new id, the resolved
row keeps its proof, and the successor starts unresolved (issue #458).
"""

from __future__ import annotations

from dataclasses import replace

from builders.policy_cases import evt, finding_record, fnd, obl
from yoetz.application.check import prior_finding_ids
from yoetz.domain.findings import FINDING_KIND_TRAITS, Finding, FindingKind, FindingOrigin
from yoetz.domain.values import Frontier
from yoetz.kernel.projections import empty_projection_state
from yoetz.protocol.coverage import (
    ArtifactObservation,
    AuthorshipAssurance,
    CheckType,
    Coverage,
    EvidenceImmutability,
    LedgerFreshness,
    PublicationChannel,
)

_DIGEST = "sha256:" + "1" * 64


def _finding(number: int) -> Finding:
    kind = FindingKind.COMPLETION_WITH_OPEN_OBLIGATIONS
    return Finding(
        finding_id=fnd(number),
        kind=kind,
        origin=FindingOrigin.DETERMINISTIC,
        priority=FINDING_KIND_TRAITS[kind][0],
        summary="A completion claim covers an open obligation.",
        detail="Resolve or revise the obligation before claiming completion.",
        subject_refs=(obl(number),),
        policy_id="work-integrity",
        policy_version="0.1.0",
        subject_frontier=Frontier(3, _DIGEST),
        coverage=Coverage(
            publication_channels=(PublicationChannel.ENGINE_DERIVED,),
            authorship_assurance=AuthorshipAssurance.SERVICE_AUTHENTICATED,
            artifact_observation=ArtifactObservation.PUBLISHED_ONLY,
            evidence_immutability=EvidenceImmutability.METADATA_ONLY,
            ledger_freshness=LedgerFreshness.CURRENT,
            check_types=(CheckType.DETERMINISTIC,),
            known_gaps=(),
        ),
    )


def test_resolved_rows_are_not_offered_for_id_reuse() -> None:
    live = _finding(1)
    resolved = _finding(2)
    state = replace(
        empty_projection_state(),
        frontier=9,
        head_digest=_DIGEST,
        findings={
            fnd(1): finding_record(live, 4),
            fnd(2): finding_record(resolved, 5, resolved_by_check_event_id=evt(7)),
        },
        freshness=LedgerFreshness.CURRENT,
    )
    prior = prior_finding_ids(state)
    assert prior == {(live.kind, live.policy_id, live.subject_refs): fnd(1)}
