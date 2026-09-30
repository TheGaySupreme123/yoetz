"""Observation-authored, non-actionable findings are disclosed limitations, not response work.

Issue #911: the observation advisory ("Observation coverage is incomplete or stale") was an
unclearable finding in every Codex session. It counted as unanswered, and answering it after a
check superseded that check and forced an identical recheck. These cases pin the structural rule
(service-stamped authorship plus the kind's closed ``actionable`` trait) and its exact reach: only
an unscored acknowledgement of such a row keeps a check attributable, and nothing else changes.
"""

from __future__ import annotations

import pytest

from yoetz.domain.events import ResponseRecordedPayload
from yoetz.domain.findings import (
    FINDING_KIND_TRAITS,
    Finding,
    FindingKind,
    FindingOrigin,
    ResponseDisposition,
    WaiverScope,
)
from yoetz.domain.values import Frontier, event_id, finding_id, timestamp_from_string
from yoetz.kernel.projections import FindingProjectionRecord, is_observation_limitation
from yoetz.kernel.reducers import supersedes_recorded_check
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
_FINDING_ID = finding_id("fnd_00000000-0000-4000-8000-000000000911")
_SOURCE_EVENT_ID = event_id("evt_00000000-0000-4000-8000-000000000911")


def _finding(kind: FindingKind) -> Finding:
    return Finding(
        finding_id=_FINDING_ID,
        kind=kind,
        origin=FindingOrigin.DETERMINISTIC,
        priority=FINDING_KIND_TRAITS[kind][0],
        summary="Observation coverage is incomplete or stale",
        detail="Source lag, mapping, or drain gaps prevent complete observation",
        subject_refs=(event_id("evt_00000000-0000-4000-8000-000000000001"),),
        policy_id="work-integrity",
        policy_version="0.1.0",
        subject_frontier=Frontier(1, _DIGEST),
        coverage=Coverage(
            publication_channels=(PublicationChannel.ENGINE_DERIVED,),
            authorship_assurance=AuthorshipAssurance.HARNESS_OBSERVED,
            artifact_observation=ArtifactObservation.HOOK_OBSERVED,
            evidence_immutability=EvidenceImmutability.CONTENT_DIGEST,
            ledger_freshness=LedgerFreshness.PARTIAL,
            check_types=(CheckType.DETERMINISTIC,),
            known_gaps=("unpaired_event",),
        ),
    )


def _record(kind: FindingKind, *, readable: bool = True) -> FindingProjectionRecord:
    return FindingProjectionRecord(
        payload=_finding(kind) if readable else None,
        payload_digest=_DIGEST,
        redacted=not readable,
        source_event_id=_SOURCE_EVENT_ID,
        source_frontier=2,
    )


def _response(disposition: ResponseDisposition) -> ResponseRecordedPayload:
    waived = disposition is ResponseDisposition.WAIVED
    return ResponseRecordedPayload(
        finding_id=_FINDING_ID,
        finding_frontier=Frontier(9, _DIGEST),
        disposition=disposition,
        reason=None if disposition is ResponseDisposition.ACKNOWLEDGED else "Stated.",
        waiver_scope=WaiverScope.FINDING_ONLY if waived else None,
        waiver_expiry=timestamp_from_string("2999-01-01T00:00:00.000Z") if waived else None,
    )


def test_only_a_readable_non_actionable_observation_authored_row_is_a_limitation() -> None:
    observed = frozenset({_SOURCE_EVENT_ID})
    assert is_observation_limitation(_record(FindingKind.LEDGER_STALE_OR_INCOMPLETE), observed)
    # Authorship is structural: the same payload recorded by a check is response work.
    assert not is_observation_limitation(
        _record(FindingKind.LEDGER_STALE_OR_INCOMPLETE), frozenset()
    )
    # Observation advice that names actionable work (a failed command, a stale verification)
    # still asks for a response.
    for kind in (FindingKind.FAILED_WORK_OMITTED, FindingKind.STALE_EVIDENCE_FOR_CHANGED_STATE):
        assert FINDING_KIND_TRAITS[kind][1]
        assert not is_observation_limitation(_record(kind), observed)
    # An unreadable row cannot prove its kind, so it stays conservative response work.
    assert not is_observation_limitation(
        _record(FindingKind.LEDGER_STALE_OR_INCOMPLETE, readable=False), observed
    )


@pytest.mark.parametrize(
    ("disposition", "supersedes"),
    (
        (ResponseDisposition.ACKNOWLEDGED, False),
        (ResponseDisposition.PROVENANCE_DISPUTED, False),
        # Rejections and waivers are scored by the local packs, so a recheck can change its result.
        (ResponseDisposition.REJECTED, True),
        (ResponseDisposition.WAIVED, True),
    ),
)
def test_only_an_unscored_answer_to_a_limitation_keeps_the_check(
    disposition: ResponseDisposition, supersedes: bool
) -> None:
    payload = _response(disposition)
    limitation = frozenset({_FINDING_ID})
    assert supersedes_recorded_check("response_recorded", payload, (), limitation) is supersedes
    # The same response to a finding that is not a limitation stays material (open question 1).
    assert supersedes_recorded_check("response_recorded", payload, (), frozenset()) is True
    # A response to a finding the check returned never superseded it, whatever its stance.
    assert supersedes_recorded_check("response_recorded", payload, (_FINDING_ID,)) is False


def test_lifecycle_closure_never_supersedes_a_check() -> None:
    """``work_closed`` is lineage, not material work: closing needs no recheck (issue #911)."""

    assert supersedes_recorded_check("work_closed", None, ()) is False
    # Unreadable responses still prove nothing about which finding they answered.
    assert supersedes_recorded_check("response_recorded", None, (), frozenset({_FINDING_ID}))
