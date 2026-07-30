"""No recorded response disposition clears a finding for receipt purposes.

The agent-facing guidance now says this in plain words, because the 2026-07-30 dogfood agent
repaired the record, recorded an evidence-backed `acknowledged` response, saw the next check return
no findings, and still received `unresolved_findings_remain` -- which it had not expected. That
behavior is deliberate (`application/receipt._finding_states` resolves nothing, on the reasoning
that conservatively unresolved is always safe), so these cases lock the promise the guidance makes.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from yoetz.application.receipt import (
    _finding_states,  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
)
from yoetz.domain.events import ResponseRecordedPayload, encode_payload
from yoetz.domain.findings import (
    FINDING_KIND_TRAITS,
    Finding,
    FindingKind,
    FindingOrigin,
    ResponseDisposition,
    WaiverScope,
)
from yoetz.domain.values import (
    Frontier,
    event_id,
    finding_id,
    obligation_id,
    timestamp_from_string,
)
from yoetz.kernel.projections import ProjectionRecord, empty_projection_state
from yoetz.protocol.canonical import canonical_digest
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
_HEAD = "sha256:" + "2" * 64
_FINDING_ID = finding_id("fnd_00000000-0000-4000-8000-000000000001")
_SOURCE_EVENT_ID = event_id("evt_00000000-0000-4000-8000-000000000001")
_RESPONSE_EVENT_ID = event_id("evt_00000000-0000-4000-8000-000000000003")


def _coverage() -> Coverage:
    return Coverage(
        publication_channels=(PublicationChannel.ENGINE_DERIVED,),
        authorship_assurance=AuthorshipAssurance.SERVICE_AUTHENTICATED,
        artifact_observation=ArtifactObservation.ARTIFACT_VERIFIED,
        evidence_immutability=EvidenceImmutability.CONTENT_DIGEST,
        ledger_freshness=LedgerFreshness.CURRENT,
        check_types=(CheckType.DETERMINISTIC,),
        known_gaps=(),
    )


def _finding(kind: FindingKind) -> Finding:
    return Finding(
        finding_id=_FINDING_ID,
        kind=kind,
        origin=FindingOrigin.DETERMINISTIC,
        priority=FINDING_KIND_TRAITS[kind][0],
        summary="A requested item was never attempted.",
        detail="Record the exact attempted items.",
        subject_refs=(obligation_id("obl_00000000-0000-4000-8000-000000000001"),),
        policy_id="work-integrity",
        policy_version="0.1.0",
        subject_frontier=Frontier(1, _DIGEST),
        coverage=_coverage(),
    )


def _response(disposition: ResponseDisposition) -> ResponseRecordedPayload:
    reason = None if disposition is ResponseDisposition.ACKNOWLEDGED else "Stated on the record."
    waiver_scope = WaiverScope.FINDING_ONLY if disposition is ResponseDisposition.WAIVED else None
    # Deliberately far future: this case is about an *unexpired* waiver still failing to resolve
    # its finding. A near-term date would silently become the expired-waiver path once it passed,
    # and a clock-derived one would make the payload digest nondeterministic.
    waiver_expiry = (
        timestamp_from_string("2999-01-01T00:00:00.000Z")
        if disposition is ResponseDisposition.WAIVED
        else None
    )
    return ResponseRecordedPayload(
        finding_id=_FINDING_ID,
        finding_frontier=Frontier(1, _DIGEST),
        disposition=disposition,
        evidence_refs=(),
        reason=reason,
        waiver_scope=waiver_scope,
        waiver_expiry=waiver_expiry,
    )


def _projection(response: ResponseRecordedPayload | None):
    finding = _finding(FindingKind.REQUESTED_ITEM_NEVER_ATTEMPTED)
    findings = {
        _FINDING_ID: ProjectionRecord(
            payload=finding,
            payload_digest=canonical_digest(encode_payload(finding)),
            redacted=False,
            source_event_id=_SOURCE_EVENT_ID,
            source_frontier=1,
        )
    }
    responses: dict[object, object] = {}
    if response is not None:
        responses[response.finding_id] = ProjectionRecord(
            payload=response,
            payload_digest=canonical_digest(encode_payload(response)),
            redacted=False,
            source_event_id=_RESPONSE_EVENT_ID,
            source_frontier=2,
        )
    return replace(
        empty_projection_state(),
        frontier=2,
        head_digest=_HEAD,
        findings=findings,
        responses=responses,
        freshness=LedgerFreshness.CURRENT,
    )


@pytest.mark.parametrize("disposition", tuple(ResponseDisposition))
def test_no_disposition_marks_a_finding_resolved(disposition: ResponseDisposition) -> None:
    states = _finding_states(_projection(_response(disposition)))
    assert [state.resolved for state in states] == [False], (
        f"{disposition.value} resolved the finding; the agent guidance promises it does not"
    )


def test_an_unanswered_finding_is_also_unresolved() -> None:
    states = _finding_states(_projection(None))
    assert [state.resolved for state in states] == [False]


def test_the_finding_kind_the_dogfood_hit_is_actionable() -> None:
    """Actionable kinds are the ones that bind the receipt conclusion."""

    assert FINDING_KIND_TRAITS[FindingKind.REQUESTED_ITEM_NEVER_ATTEMPTED][1] is True
