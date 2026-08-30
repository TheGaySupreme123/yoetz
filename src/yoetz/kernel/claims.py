"""Shared append-only claim revision and limitation-scope derivations."""

from __future__ import annotations

from yoetz.domain.events import (
    ClaimRecordedPayload,
    ClaimRecordedPayloadV1_1,
)
from yoetz.domain.values import ClaimId, ObligationId, ResultId
from yoetz.kernel.projections import ProjectionRecord, ProjectionState

__all__ = [
    "claim_discloses_result",
    "effective_claim_items",
    "effective_claim_ids",
    "result_is_relevant_to_claim",
]

type ClaimPayload = ClaimRecordedPayload | ClaimRecordedPayloadV1_1
type ClaimRecord = ProjectionRecord[ClaimPayload]


def effective_claim_ids(projection: ProjectionState) -> frozenset[ClaimId]:
    """Return claims not explicitly replaced by a readable v1.1 claim."""

    superseded = {
        target
        for record in projection.claims.values()
        if type(record.payload) is ClaimRecordedPayloadV1_1
        for target in record.payload.supersedes_claim_refs
    }
    return frozenset(claim for claim in projection.claims if claim not in superseded)


def effective_claim_items(
    projection: ProjectionState,
) -> tuple[tuple[ClaimId, ClaimRecord], ...]:
    """Return effective claims in stable claim-id order; history remains in the projection."""

    effective = effective_claim_ids(projection)
    return tuple((claim, projection.claims[claim]) for claim in sorted(effective, key=str.encode))


def _scope(payload: ClaimPayload) -> frozenset[ObligationId]:
    return frozenset(payload.obligation_refs)


def result_is_relevant_to_claim(
    projection: ProjectionState,
    claim_record: ClaimRecord,
    result_ref: ResultId,
) -> bool:
    """Whether a result existed by the claim and overlaps its declared obligation scope."""

    claim = claim_record.payload
    result = projection.results.get(result_ref)
    if (
        claim is None
        or result is None
        or result.payload is None
        or result.source_frontier > claim_record.source_frontier
    ):
        return False
    action = projection.actions.get(result.payload.action_id)
    if action is None or action.payload is None:
        return True
    claim_scope = _scope(claim)
    action_scope = frozenset(action.payload.obligation_refs)
    return not claim_scope or not action_scope or not claim_scope.isdisjoint(action_scope)


def claim_discloses_result(
    claim: ClaimPayload,
    result_ref: ResultId,
) -> bool:
    """Apply the versioned disclosure field without reinterpreting frozen v1 bytes."""

    if type(claim) is ClaimRecordedPayloadV1_1:
        return result_ref in claim.limitation_refs
    return result_ref in claim.supporting_refs
