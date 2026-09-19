"""Acceptance matrix for frozen parent-to-child lineage rollups."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from yoetz.domain.coordination import LineageAcceptance, LineageOrigin, SessionHealth, WorkState
from yoetz.domain.events import ChildDependencySnapshot as DomainChildDependencySnapshot
from yoetz.domain.findings import FINDING_KIND_TRAITS, FindingKind, FindingOrigin
from yoetz.domain.values import (
    Actor,
    ActorType,
    EventId,
    Frontier,
    actor_id,
    event_id,
    finding_id,
    receipt_id,
    task_id,
)
from yoetz.kernel.lineage import (
    ChildDependencySnapshot,
    ChildFindingSnapshot,
    LineageManifest,
    LineageRollupState,
    evaluate_lineage,
    evaluate_recorded_lineage,
    lineage_manifest_from_records,
)
from yoetz.protocol.coverage import AuthorshipAssurance, PublicationChannel, coverage_for_channel

_DIGEST = "sha256:" + "1" * 64
_CHILD = task_id("tsk_00000000-0000-4000-8000-000000000001")
_MANIFEST_1 = event_id("evt_00000000-0000-4000-8000-000000000001")
_MANIFEST_2 = event_id("evt_00000000-0000-4000-8000-000000000002")
_CHECK = event_id("evt_00000000-0000-4000-8000-000000000003")
_RECEIPT = receipt_id("rcp_00000000-0000-4000-8000-000000000001")
_FINDING = finding_id("fnd_00000000-0000-4000-8000-000000000001")
_FRONTIER = Frontier(3, _DIGEST)


def _finding(kind: FindingKind, *, resolved: bool = False) -> ChildFindingSnapshot:
    return ChildFindingSnapshot(
        finding_id=_FINDING,
        kind=kind,
        origin=FindingOrigin.DETERMINISTIC,
        priority=FINDING_KIND_TRAITS[kind][0],
        resolved=resolved,
        resolution_event_id=_CHECK if resolved else None,
    )


def _child(
    *,
    acceptance: LineageAcceptance = LineageAcceptance.ACCEPTED,
    work_state: WorkState = WorkState.CLOSED,
    session_health: SessionHealth = SessionHealth.ENDED,
    frontier: Frontier | None = _FRONTIER,
    check: bool = True,
    receipt: bool = True,
    findings: tuple[ChildFindingSnapshot, ...] = (),
    read_gaps: tuple[str, ...] = (),
    manifest: EventId | None = _MANIFEST_1,
) -> ChildDependencySnapshot:
    return ChildDependencySnapshot(
        child_task_id=_CHILD,
        origin=LineageOrigin.PARENT_MINTED,
        acceptance=acceptance,
        work_state=work_state,
        session_health=session_health,
        child_frontier=frontier,
        child_check_id=_CHECK if check else None,
        child_receipt_id=_RECEIPT if receipt else None,
        coverage=coverage_for_channel(PublicationChannel.ENGINE_DERIVED),
        findings=findings,
        lineage_authority_revision="rev-1",
        read_gap_reasons=read_gaps,
        child_check_subject_frontier=_FRONTIER if check else None,
        manifest_event_id=manifest,
    )


def _record(
    event: EventId,
    sequence: int,
    children: tuple[ChildDependencySnapshot | DomainChildDependencySnapshot, ...],
    *,
    author: Actor | None = None,
    publication_channel: PublicationChannel = PublicationChannel.ENGINE_DERIVED,
) -> object:
    return SimpleNamespace(
        schema=SimpleNamespace(name="child_dependencies_recorded"),
        event_id=event,
        ledger=SimpleNamespace(ingestion_sequence=sequence),
        payload=SimpleNamespace(children=children),
        author=(
            author
            if author is not None
            else Actor(
                actor_id("yoetz:observation-coordinator"),
                ActorType.HARNESS,
                AuthorshipAssurance.HARNESS_OBSERVED,
            )
        ),
        publication_channel=publication_channel,
    )


@pytest.mark.parametrize(
    ("child", "state", "blocks"),
    (
        (_child(), LineageRollupState.CLEAN, False),
        (
            _child(findings=(_finding(FindingKind.COMPLETION_WITH_OPEN_OBLIGATIONS),)),
            LineageRollupState.BLOCKED,
            True,
        ),
        (
            _child(findings=(_finding(FindingKind.LEDGER_STALE_OR_INCOMPLETE),)),
            LineageRollupState.ANNOTATION,
            False,
        ),
        (
            _child(acceptance=LineageAcceptance.PENDING),
            LineageRollupState.ANNOTATION,
            False,
        ),
        (
            _child(work_state=WorkState.OPEN, session_health=SessionHealth.ACTIVE),
            LineageRollupState.OPEN_GAP,
            True,
        ),
        (
            _child(session_health=SessionHealth.CONTACT_LOST),
            LineageRollupState.INCOMPLETE,
            True,
        ),
        (
            _child(frontier=None, read_gaps=("missing",)),
            LineageRollupState.UNAVAILABLE,
            True,
        ),
    ),
)
def test_rollup_acceptance_matrix(
    child: ChildDependencySnapshot,
    state: LineageRollupState,
    blocks: bool,
) -> None:
    evaluation = evaluate_lineage(LineageManifest((child,)))
    assert evaluation.children[0].state is state
    assert evaluation.blocks_clean_completion is blocks
    assert evaluation.children[0].freshness == (
        "unknown"
        if child.child_frontier is None
        or child.child_check_id is None
        or child.child_receipt_id is None
        else "known"
    )


def test_recorded_aggregate_replaces_removed_child_and_never_reads_live_state() -> None:
    first = _record(_MANIFEST_1, 1, (_child(),))
    second = _record(_MANIFEST_2, 2, ())
    manifest = lineage_manifest_from_records((first, second))
    assert manifest.children == ()
    assert manifest.source_event_id == _MANIFEST_2

    class LiveChild:
        def __getattribute__(self, name: str) -> object:
            raise AssertionError(f"live child read: {name}")

    # The evaluator accepts only the recorded immutable value; a live object is never passed to
    # or consulted by the rollup path.
    assert evaluate_lineage(manifest).children == ()
    del LiveChild


def test_forged_manifest_authority_is_replayed_as_a_named_gap() -> None:
    forged = _record(
        _MANIFEST_1,
        1,
        (_child(),),
        author=Actor(
            actor_id("agent:forged-manifest"),
            ActorType.LOGICAL_AGENT,
            AuthorshipAssurance.SELF_ASSERTED,
        ),
    )
    manifest = lineage_manifest_from_records((forged,))
    assert manifest.children == ()
    assert manifest.read_gap_reasons == ("not_authorized",)


def test_recorded_domain_event_revision_normalizes_to_kernel_token() -> None:
    domain_child = DomainChildDependencySnapshot(
        child_task_id=_CHILD,
        origin=LineageOrigin.PARENT_MINTED,
        acceptance=LineageAcceptance.ACCEPTED,
        work_state=WorkState.CLOSED,
        session_health=SessionHealth.ENDED,
        child_frontier=_FRONTIER,
        child_check_id=None,
        child_check_subject_frontier=None,
        child_receipt_id=None,
        coverage=coverage_for_channel(PublicationChannel.ENGINE_DERIVED),
        findings=(),
        lineage_authority_revision=1,
    )
    record = _record(_MANIFEST_1, 1, (domain_child,))
    manifest = lineage_manifest_from_records((record,))
    assert manifest.children[0].lineage_authority_revision == "1"


def test_later_recorded_manifest_is_uncovered_and_replay_bound() -> None:
    newer_frontier = Frontier(4, "sha256:" + "2" * 64)
    newer = replace(
        _child(),
        child_frontier=newer_frontier,
        child_check_subject_frontier=newer_frontier,
        manifest_event_id=_MANIFEST_2,
    )
    first = _record(_MANIFEST_1, 1, (_child(),))
    second = _record(_MANIFEST_2, 2, (newer,))
    evaluation = evaluate_recorded_lineage((first, second), tested_through_sequence=1)
    assert evaluation.children[0].state is LineageRollupState.CLEAN
    assert evaluation.children[0].tested_manifest_ref == _MANIFEST_1
    assert evaluation.children[0].later_manifest_ref == _MANIFEST_2
    assert "lineage_manifest_uncovered" in evaluation.coverage.known_gaps
    assert evaluation.blocks_clean_completion


def test_later_unreadable_manifest_keeps_its_authority_gap_visible() -> None:
    later = _record(
        _MANIFEST_2,
        2,
        (
            _child(
                frontier=None,
                check=False,
                receipt=False,
                read_gaps=("unreadable",),
                manifest=_MANIFEST_2,
            ),
        ),
    )
    first = _record(_MANIFEST_1, 1, (_child(),))

    evaluation = evaluate_recorded_lineage((first, later), tested_through_sequence=1)

    assert evaluation.children[0].state is LineageRollupState.CLEAN
    assert evaluation.children[0].later_manifest_ref == _MANIFEST_2
    assert "lineage_manifest_uncovered" in evaluation.coverage.known_gaps
    assert "lineage_child_unavailable" in {gap.code for gap in evaluation.gaps}


def test_pending_child_gaps_are_annotation_only() -> None:
    child = _child(
        acceptance=LineageAcceptance.PENDING,
        frontier=None,
        check=False,
        receipt=False,
        read_gaps=("missing",),
    )
    evaluation = evaluate_lineage(LineageManifest((child,)))
    assert evaluation.children[0].state is LineageRollupState.ANNOTATION
    assert evaluation.children[0].freshness == "unknown"
    assert evaluation.blocks_clean_completion is False
    assert evaluation.coverage.known_gaps == ()
    assert {gap.code for gap in evaluation.gaps} == {
        "lineage_child_unavailable",
        "lineage_child_frontier_unknown",
        "lineage_child_verification_unknown",
    }


def test_rejected_child_with_actionable_finding_cannot_escape_blocking_rollup() -> None:
    child = _child(
        acceptance=LineageAcceptance.REJECTED,
        findings=(_finding(FindingKind.COMPLETION_WITH_OPEN_OBLIGATIONS),),
    )
    evaluation = evaluate_lineage(LineageManifest((child,)))
    assert evaluation.children[0].state is LineageRollupState.BLOCKED
    assert evaluation.blocks_clean_completion is True
    assert evaluation.actionable_finding_ids == (_FINDING,)
    assert "lineage_invalid_acceptance_transition" in evaluation.coverage.known_gaps
