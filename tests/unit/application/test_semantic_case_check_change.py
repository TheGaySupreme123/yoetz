"""The service-captured check-time change in the pure case builder (ADR-031, issue #883)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast

import pytest

from builders.policy_cases import (
    clm,
    evd,
    make_case,
    obl,
    obligation_record,
    plan_record,
    record,
)
from unit.application.test_semantic_case import (
    _captured_case_values,  # pyright: ignore[reportPrivateUsage]
    _case_with_material,  # pyright: ignore[reportPrivateUsage]
)
from yoetz.application.semantic_case import (
    CHECK_TIME_CHANGE_ITEM_PREFIX,
    CapturedContentScope,
    CapturedSemanticContent,
    CheckTimeChange,
    build_semantic_case,
    check_time_change_shown_files,
    semantic_case_to_prepared_payload,
)
from yoetz.domain.events import (
    ClaimKind,
    ClaimRecordedPayload,
    ObligationPublishedPayload,
    ObligationStatus,
    PlanPublishedPayload,
)
from yoetz.domain.privacy import ReviewContextProfile, ReviewSelectionPolicy
from yoetz.domain.receipts import (
    CHECK_TIME_CHANGE_BASE_UNAVAILABLE_GAP,
    CHECK_TIME_CHANGE_REDACTED_GAP,
    CHECK_TIME_CHANGE_TRUNCATED_GAP,
    CHECK_TIME_CHANGE_UNAVAILABLE_GAP,
)
from yoetz.kernel.deterministic_checks import DeterministicCase
from yoetz.ports.change_capture import (
    CHECK_CHANGE_MEDIA_TYPE,
    ChangeBaseKind,
    CheckChangeCapture,
)
from yoetz.ports.objects import ObjectKind, ObjectMetadata, ObjectRef
from yoetz.ports.semantic import SemanticCase
from yoetz.protocol.canonical import strict_json_parse
from yoetz.protocol.coverage import LedgerFreshness

_TASK = "tsk_10000000-0000-4000-8000-000000000001"


def _change(
    text: bytes = b"diff --git a/selectors.ts b/selectors.ts\n+kea-repair-marker\n",
    *,
    base: ChangeBaseKind = "task_start",
    truncated: bool = False,
    redacted: bool = False,
    object_suffix: str = "901",
) -> CheckTimeChange:
    return CheckTimeChange(
        ObjectRef(
            object_id=f"obj_00000000-0000-4000-8000-000000000{object_suffix}",
            plaintext_size=len(text) + 100,
            commitment="hmac-sha256:" + "9" * 64,
            envelope_digest="sha256:" + "a" * 64,
            encryption_format="yoetz-object/1",
            key_slot="task",
            metadata=ObjectMetadata(
                ObjectKind.CHANGE_CAPTURE,
                CHECK_CHANGE_MEDIA_TYPE,
                _TASK,
                datetime(2026, 9, 29, tzinfo=UTC),
            ),
        ),
        CheckChangeCapture(
            base=base,
            text=text,
            tracked_files=1,
            untracked_files=0,
            omitted_files=1 if truncated else 0,
            truncated=truncated,
            redacted=redacted,
        ),
    )


def _build(
    case: DeterministicCase,
    profile: ReviewContextProfile = ReviewContextProfile.EXPANDED,
    *,
    change: CheckTimeChange | None = None,
    unavailable: bool = False,
    selection: ReviewSelectionPolicy | None = None,
    captured: Sequence[CapturedSemanticContent] = (),
    scope: CapturedContentScope | None = None,
) -> SemanticCase:
    return build_semantic_case(
        case_id="cas_10000000-0000-4000-8000-000000000001",
        frozen_case=case,
        dependency_digest="sha256:" + "b" * 64,
        findings=(),
        review_context_profile=profile,
        review_selection=selection or ReviewSelectionPolicy.for_profile(profile),
        policy_id="pvy_10000000-0000-4000-8000-000000000001",
        policy_version="1",
        captured_content=captured,
        captured_content_scope=scope,
        check_time_change=change,
        check_time_change_unavailable=unavailable,
    )


def _change_items(case: SemanticCase) -> list[str]:
    return [
        item.excerpt_item_id
        for item in case.packet.targeted_excerpts
        if item.excerpt_item_id.startswith(CHECK_TIME_CHANGE_ITEM_PREFIX)
    ]


def _large_change(parts: int) -> bytes:
    line = b"+" + b"x" * 99 + b"\n"
    return line * (40 * parts)


def test_change_is_the_first_excerpt_linked_to_claims_and_obligations() -> None:
    case = _case_with_material(with_evidence=True)

    semantic = _build(case, change=_change())

    first = semantic.packet.targeted_excerpts[0]
    assert first.excerpt_item_id == f"{CHECK_TIME_CHANGE_ITEM_PREFIX}001"
    assert first.source_kind == "diff"
    assert first.linked_subject_refs == (str(clm(1)), str(obl(1)))
    item = next(item for item in semantic.items if item.item_id == first.excerpt_item_id)
    content = item.content.decode("utf-8")
    assert content.startswith("[Yoetz check-time change, part 1 of 1]\n")
    assert "+kea-repair-marker" in content
    assert not {
        CHECK_TIME_CHANGE_BASE_UNAVAILABLE_GAP,
        CHECK_TIME_CHANGE_TRUNCATED_GAP,
        CHECK_TIME_CHANGE_UNAVAILABLE_GAP,
    } & set(semantic.packet.coverage.known_gaps)
    # The provider document orders items by id, and the change's ids sort first.
    prepared = strict_json_parse(
        semantic_case_to_prepared_payload(semantic, {item.item_id for item in semantic.items})
    )
    assert isinstance(prepared, dict)
    rows = cast(list[dict[str, object]], prepared["items"])
    assert rows[0]["item_id"] == first.excerpt_item_id


def test_reserved_share_holds_against_competing_captures_and_backfills_free_room() -> None:
    case, captured, scope = _captured_case_values(b"competing-tool-output-marker")
    change = _change(_large_change(20))

    semantic = _build(case, change=change, captured=(captured,), scope=scope)

    change_ids = _change_items(semantic)
    excerpt_ids = [item.excerpt_item_id for item in semantic.packet.targeted_excerpts]
    # Eight parts are reserved first, the capture still gets its slot, and the rest of the
    # budget backfills more parts afterwards.
    assert excerpt_ids[:8] == change_ids[:8]
    assert f"excerpt-{evd(1)}" in excerpt_ids
    assert len(excerpt_ids) == 16
    assert len(change_ids) == 15
    assert CHECK_TIME_CHANGE_TRUNCATED_GAP in semantic.packet.coverage.known_gaps
    assert semantic.packet.coverage.ledger_freshness is LedgerFreshness.PARTIAL
    last = next(item for item in semantic.items if item.item_id == change_ids[-1])
    assert last.content.startswith(b"[Yoetz check-time change, part 15 of ")


def test_small_change_leaves_its_unused_reservation_to_other_excerpts() -> None:
    case, captured, scope = _captured_case_values(b"competing-tool-output-marker")

    semantic = _build(case, change=_change(), captured=(captured,), scope=scope)

    assert [item.excerpt_item_id for item in semantic.packet.targeted_excerpts] == [
        f"{CHECK_TIME_CHANGE_ITEM_PREFIX}001",
        f"excerpt-{evd(1)}",
    ]
    assert CHECK_TIME_CHANGE_TRUNCATED_GAP not in semantic.packet.coverage.known_gaps


@pytest.mark.parametrize(
    ("change", "gap"),
    (
        (_change(base="head"), CHECK_TIME_CHANGE_BASE_UNAVAILABLE_GAP),
        (_change(truncated=True), CHECK_TIME_CHANGE_TRUNCATED_GAP),
        (_change(redacted=True), CHECK_TIME_CHANGE_REDACTED_GAP),
    ),
)
def test_capture_limits_are_disclosed_on_packet_coverage(change: CheckTimeChange, gap: str) -> None:
    semantic = _build(_case_with_material(), change=change)

    assert gap in semantic.packet.coverage.known_gaps
    assert semantic.packet.coverage.ledger_freshness is LedgerFreshness.PARTIAL
    assert _change_items(semantic)


def test_unavailable_capture_is_disclosed_without_any_item() -> None:
    semantic = _build(_case_with_material(), unavailable=True)

    assert CHECK_TIME_CHANGE_UNAVAILABLE_GAP in semantic.packet.coverage.known_gaps
    assert not _change_items(semantic)


def test_change_without_a_linkable_subject_is_disclosed_unavailable() -> None:
    semantic = _build(make_case(), change=_change())

    assert CHECK_TIME_CHANGE_UNAVAILABLE_GAP in semantic.packet.coverage.known_gaps
    assert not _change_items(semantic)


def test_plan_is_the_fallback_link_when_no_claim_or_obligation_exists() -> None:
    plan = plan_record(PlanPublishedPayload(1, "Repair selectors", ()), 1)
    case = make_case(plans={1: plan}, extra_refs=(plan.source_event_id,))

    semantic = _build(case, change=_change())

    assert semantic.packet.targeted_excerpts[0].linked_subject_refs == (str(plan.source_event_id),)


@pytest.mark.parametrize(
    "profile", (ReviewContextProfile.STRUCTURAL, ReviewContextProfile.GOAL_AWARE)
)
def test_recipe_without_diff_excerpts_carries_nothing_and_reports_nothing(
    profile: ReviewContextProfile,
) -> None:
    case = _case_with_material()
    baseline = _build(case, profile)

    for semantic in (
        _build(case, profile, change=_change()),
        _build(case, profile, unavailable=True),
    ):
        assert not _change_items(semantic)
        assert semantic.packet.coverage == baseline.packet.coverage
        assert semantic.case_digest == baseline.case_digest


def test_custom_recipe_without_the_diff_kind_carries_nothing() -> None:
    expanded = ReviewSelectionPolicy.for_profile(ReviewContextProfile.EXPANDED)
    selection = replace(
        expanded, excerpt_kinds=tuple(kind for kind in expanded.excerpt_kinds if kind != "diff")
    )
    case = _case_with_material()

    semantic = _build(case, ReviewContextProfile.CUSTOM, change=_change(), selection=selection)

    assert not _change_items(semantic)
    assert CHECK_TIME_CHANGE_UNAVAILABLE_GAP not in semantic.packet.coverage.known_gaps


def test_case_digest_binds_the_change_and_is_unchanged_without_one() -> None:
    case = _case_with_material()
    without = _build(case)

    first = _build(case, change=_change(b"+first\n"))
    second = _build(case, change=_change(b"+second\n"))

    assert without.case_digest != first.case_digest != second.case_digest
    assert _build(case, change=_change(b"+first\n")).case_digest == first.case_digest
    assert _build(case).case_digest == without.case_digest


def test_parts_split_on_lines_and_stay_within_one_item() -> None:
    text = b"".join(f"+line {index:05d} {'y' * 40}\n".encode() for index in range(300))

    semantic = _build(_case_with_material(), change=_change(text))

    items = [
        item for item in semantic.items if item.item_id.startswith(CHECK_TIME_CHANGE_ITEM_PREFIX)
    ]
    assert len(items) > 1
    joined = b""
    for index, item in enumerate(items, start=1):
        assert item.content_bytes <= 4_096
        header, _, body = item.content.partition(b"\n")
        assert header == f"[Yoetz check-time change, part {index} of {len(items)}]".encode()
        assert body.endswith(b"\n")
        joined += body
    assert joined == text


def test_obligation_only_case_links_the_change_to_the_obligation() -> None:
    obligation = obligation_record(
        ObligationPublishedPayload(obl(1), "Repair", "tests pass", ObligationStatus.OPEN), 1
    )
    claim = record(ClaimRecordedPayload(clm(2), ClaimKind.MATERIAL, "Working", ()), 2)
    case = make_case(
        obligations={obl(1): obligation},
        claims={clm(2): claim},
        extra_refs=(obl(1), clm(2)),
    )

    semantic = _build(case, change=_change())

    assert semantic.packet.targeted_excerpts[0].linked_subject_refs == (str(clm(2)), str(obl(1)))


def test_value_rejects_objects_that_are_not_check_change_captures() -> None:
    valid = _change()
    wrong_kind = replace(
        valid.object_ref,
        metadata=replace(valid.object_ref.metadata, kind=ObjectKind.CAPTURED_CONTENT),
    )
    with pytest.raises(ValueError, match="semantic_case_check_change_invalid"):
        CheckTimeChange(wrong_kind, valid.capture)
    with pytest.raises(ValueError, match="semantic_case_check_change_invalid"):
        _build(_case_with_material(), change=valid, unavailable=True)


def _section(path: str, lines: int, *, fill: str = "x") -> bytes:
    head = f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1,{lines} @@\n"
    return head.encode() + b"".join(f"+{fill * 60} {index}\n".encode() for index in range(lines))


def test_shown_files_follow_the_admitted_parts_and_redaction() -> None:
    """Whole, cut at the packet boundary, redacted, and never reached (ADR-031 resolution)."""

    header = b"Yoetz check-time change: header\nFiles:\n  M listed-only.ts\nEnd of header.\n"
    text = (
        header
        + _section("whole.ts", 3)
        + _section("redacted.ts", 2).replace(b"x" * 60, b"[REDACTED]" + b"x" * 50, 1)
        + _section("cut.ts", 1_500)
        + _section("never.ts", 1_500)
    )
    change = _change(text)
    case = _build(_case_with_material(), change=change)
    admitted = len(_change_items(case))
    selection = ReviewSelectionPolicy.for_profile(ReviewContextProfile.EXPANDED)

    files = check_time_change_shown_files(change.capture, selection, admitted)

    shown_bytes = sum(len(chunk) for chunk in _check_time_change_chunks(text)[:admitted])
    whole_len = len(_section("whole.ts", 3))
    redacted_len = len(_section("redacted.ts", 2))
    cut_start = len(header) + whole_len + redacted_len
    redacted_marker = text.index(b"[REDACTED]") - (len(header) + whole_len)
    assert [
        (
            file.identity.decode(),
            file.whole,
            file.shown_bytes,
            file.redactions,
            file.section_admitted,
            file.clean_bytes,
        )
        for file in files
    ][:3] == [
        ("diff --git a/whole.ts b/whole.ts", True, whole_len, 0, True, whole_len),
        # Wholly admitted but redacted: everything shown counts, and so does the span.
        (
            "diff --git a/redacted.ts b/redacted.ts",
            False,
            redacted_len,
            1,
            True,
            redacted_marker,
        ),
        (
            "diff --git a/cut.ts b/cut.ts",
            False,
            shown_bytes - cut_start,
            0,
            False,
            shown_bytes - cut_start,
        ),
    ]
    assert all(b"never.ts" not in identity for identity, *_ in files)
    assert all(b"listed-only" not in identity for identity, *_ in files)
    # With every part admitted the whole unredacted change is fully shown.
    everything = check_time_change_shown_files(
        change.capture, selection, len(_check_time_change_chunks(text))
    )
    assert {identity: whole for identity, whole, *_ in everything}[
        b"diff --git a/never.ts b/never.ts"
    ] is True
    assert check_time_change_shown_files(change.capture, selection, 0) == ()


def test_many_changed_files_record_only_the_few_the_packet_showed() -> None:
    """More than 128 changed files: only shown files count toward the record bound."""

    header = b"Yoetz check-time change: header\nEnd of header.\n"
    text = header + b"".join(_section(f"generated/{index:03d}.ts", 20) for index in range(140))
    change = _change(text)
    case = _build(_case_with_material(), change=change)
    selection = ReviewSelectionPolicy.for_profile(ReviewContextProfile.EXPANDED)

    files = check_time_change_shown_files(change.capture, selection, len(_change_items(case)))

    assert 0 < len(files) < 128
    assert sum(whole for _, whole, *_ in files) == len(files) - 1  # one straddles the edge


def _check_time_change_chunks(text: bytes) -> tuple[bytes, ...]:
    from yoetz.application import semantic_case as module

    selection = ReviewSelectionPolicy.for_profile(ReviewContextProfile.EXPANDED)
    chunks = getattr(module, "_check_time_change_chunks")
    limit = getattr(module, "_check_time_change_part_limit")(selection)
    return cast(tuple[bytes, ...], chunks(text, limit))
