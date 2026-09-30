"""The service-captured check-time change in the pure case builder (ADR-031, issue #883)."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast

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
    CheckTimeChangeShownFile,
    build_semantic_case,
    check_time_change_parts_carried,
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
    assert "check_time_change_unavailable_no_linked_subject" in (
        semantic.packet.coverage.known_gaps
    )
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


def _tight_custom_selection() -> ReviewSelectionPolicy:
    """A legal custom recipe whose half-byte share (3000) is smaller than one 4 KiB part."""

    expanded = ReviewSelectionPolicy.for_profile(ReviewContextProfile.EXPANDED)
    return replace(expanded, max_excerpts=4, max_excerpt_bytes=4_096, max_total_excerpt_bytes=6_000)


@pytest.mark.parametrize("parts", (1, 3))
def test_reservation_admits_one_whole_part_even_when_half_the_bytes_is_smaller(
    parts: int,
) -> None:
    # R945-01: the reserved share must hold at least one part, or an ordinary excerpt consumes
    # the budget first and a captured change is reported unavailable.
    line = b"+" + b"z" * 99 + b"\n"
    text = line * (39 * parts)
    case, captured, scope = _captured_case_values(b"competing-output " + b"q" * 3_880)
    selection = _tight_custom_selection()

    semantic = _build(
        case,
        ReviewContextProfile.CUSTOM,
        change=_change(text),
        selection=selection,
        captured=(captured,),
        scope=scope,
    )

    change_ids = _change_items(semantic)
    assert change_ids, "a captured change that fits the recipe must reach the packet"
    assert semantic.packet.targeted_excerpts[0].excerpt_item_id == change_ids[0]
    gaps = set(semantic.packet.coverage.known_gaps)
    assert CHECK_TIME_CHANGE_UNAVAILABLE_GAP not in gaps
    assert (CHECK_TIME_CHANGE_TRUNCATED_GAP in gaps) is (parts > 1)
    used = sum(item.content_bytes for item in semantic.packet.targeted_excerpts)
    assert used <= selection.max_total_excerpt_bytes


def test_parts_never_exceed_a_total_excerpt_budget_smaller_than_one_excerpt() -> None:
    expanded = ReviewSelectionPolicy.for_profile(ReviewContextProfile.EXPANDED)
    selection = replace(
        expanded, max_excerpts=4, max_excerpt_bytes=16_384, max_total_excerpt_bytes=1_500
    )

    semantic = _build(
        _case_with_material(),
        ReviewContextProfile.CUSTOM,
        change=_change(_large_change(1)),
        selection=selection,
    )

    change_ids = _change_items(semantic)
    assert change_ids
    assert CHECK_TIME_CHANGE_UNAVAILABLE_GAP not in semantic.packet.coverage.known_gaps
    used = sum(item.content_bytes for item in semantic.packet.targeted_excerpts)
    assert used <= selection.max_total_excerpt_bytes


def test_parts_carried_are_counted_from_the_bounded_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # R945-06: the shown-file record must describe what the provider-facing envelope carries,
    # not the case before envelope minimization dropped catalog rows.
    import yoetz.application.semantic_case as semantic_case_module

    semantic = _build(_case_with_material(), change=_change(_large_change(12)))
    admitted = len(_change_items(semantic))
    assert admitted == 12
    assert check_time_change_parts_carried(semantic) == admitted
    assert check_time_change_parts_carried(semantic, withheld_categories=("claim_text",)) == 12

    monkeypatch.setattr(semantic_case_module, "MAX_EGRESS_ENVELOPE_BYTES", 8_000)
    carried = check_time_change_parts_carried(semantic)
    assert 0 < carried < admitted
    envelope = strict_json_parse(semantic_case_module.bounded_case_envelope(semantic))
    assert isinstance(envelope, dict)
    catalog = {
        cast(dict[str, object], row)["item_id"]
        for row in cast(list[object], envelope["item_catalog"])
    }
    assert {f"{CHECK_TIME_CHANGE_ITEM_PREFIX}{index:03d}" for index in range(1, carried + 1)} <= (
        catalog
    )
    assert f"{CHECK_TIME_CHANGE_ITEM_PREFIX}{carried + 1:03d}" not in catalog


def test_parts_carried_is_zero_when_the_channel_withholds_repository_excerpts() -> None:
    semantic = _build(_case_with_material(), change=_change())

    assert check_time_change_parts_carried(semantic) == 1
    assert (
        check_time_change_parts_carried(semantic, withheld_categories=("repository_excerpt",)) == 0
    )


@pytest.mark.parametrize(
    "reason", ("unsafe_root", "unsupported_repository", "changed_during_capture", "git_failed")
)
def test_unavailable_reason_is_disclosed_beside_the_generic_code_and_bound_to_the_case(
    reason: str,
) -> None:
    case = _case_with_material()
    generic = _build(case, unavailable=True)

    semantic = build_semantic_case(
        case_id="cas_10000000-0000-4000-8000-000000000001",
        frozen_case=case,
        dependency_digest="sha256:" + "b" * 64,
        findings=(),
        review_context_profile=ReviewContextProfile.EXPANDED,
        review_selection=ReviewSelectionPolicy.for_profile(ReviewContextProfile.EXPANDED),
        policy_id="pvy_10000000-0000-4000-8000-000000000001",
        policy_version="1",
        check_time_change_unavailable=True,
        check_time_change_unavailable_reason=reason,
    )

    gaps = set(semantic.packet.coverage.known_gaps)
    assert {CHECK_TIME_CHANGE_UNAVAILABLE_GAP, f"check_time_change_unavailable_{reason}"} <= gaps
    assert semantic.case_digest != generic.case_digest


def test_every_adapter_reason_has_a_closed_sentence() -> None:
    from yoetz.domain.receipts import (
        CHECK_TIME_CHANGE_UNAVAILABLE_REASONS,
        check_time_change_gap_sentence,
        check_time_change_unavailable_reason_gap,
    )
    from yoetz.ports.change_capture import CHANGE_CAPTURE_UNAVAILABLE_REASONS

    assert CHANGE_CAPTURE_UNAVAILABLE_REASONS <= set(CHECK_TIME_CHANGE_UNAVAILABLE_REASONS)
    for reason in CHECK_TIME_CHANGE_UNAVAILABLE_REASONS:
        sentence = check_time_change_gap_sentence(check_time_change_unavailable_reason_gap(reason))
        assert sentence is not None and sentence.isascii() and sentence.endswith(".")
    assert check_time_change_gap_sentence(CHECK_TIME_CHANGE_UNAVAILABLE_GAP) is None
    with pytest.raises(ValueError):
        check_time_change_unavailable_reason_gap("not_a_reason")


def _view_of(text: bytes, parts: int = 1) -> CheckTimeChangeShownFile:
    change = _change(text).capture
    selection = ReviewSelectionPolicy.for_profile(ReviewContextProfile.EXPANDED)
    (file,) = check_time_change_shown_files(change, selection, parts)
    return file


_SECTION = (
    b"diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n"
    b"@@ -1,3 +1,3 @@ def handler():\n"
    b" keep = 1\n-token = [REDACTED]\n+value = 2\n"
    b"@@ -20,2 +20,2 @@ def other():\n"
    b"-old = 3\n+new = 4\n"
)


def test_view_binds_where_redactions_and_hunks_lie_not_only_their_counts() -> None:
    """R945-02: equal lengths and counts, different positions, different views."""

    base = _view_of(_SECTION)
    moved_marker = _view_of(
        _SECTION.replace(b"-token = [REDACTED]\n+value = 2\n", b"-token = 1\n+value = [REDACTED]\n")
    )
    moved_hunk = _view_of(_SECTION.replace(b"@@ -20,2 +20,2 @@", b"@@ -40,2 +40,2 @@"))

    assert base.redactions == moved_marker.redactions == 1
    assert base.shown_bytes == moved_marker.shown_bytes == moved_hunk.shown_bytes
    assert base.view != moved_marker.view
    assert base.view != moved_hunk.view
    assert _view_of(_SECTION).view == base.view
    assert b"token" not in base.view and b"value" not in base.view  # structure, not lines


@pytest.mark.anyio
async def test_partial_files_record_a_keyed_view_commitment_never_the_view() -> None:
    from yoetz.application.check_change import check_change_shown_files

    commitments: list[bytes] = []

    class _Objects:
        async def commitment_for(self, data: bytes, kind: object) -> str:
            commitments.append(data)
            return "hmac-sha256:" + hashlib.sha256(data).hexdigest()

    class _Runtime:
        objects = _Objects()

    selection = ReviewSelectionPolicy.for_profile(ReviewContextProfile.EXPANDED)
    change = _change(_SECTION)
    change = CheckTimeChange(
        change.object_ref, replace(change.capture, base_commit="a" * 40, redacted=True)
    )

    files = await check_change_shown_files(cast(Any, _Runtime()), change, selection, 1)

    (partial,) = files.partially_shown
    assert partial.view_commitment is not None
    assert partial.view_commitment != partial.commitment
    assert any(data.startswith(b"yoetz/check-change-shown-view/v1\x00") for data in commitments)


def test_shown_file_views_come_from_exactly_the_carried_envelope_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R945-02/R945-06: views and accounting share one source, the parts the envelope kept."""

    import yoetz.application.semantic_case as semantic_case_module

    text = b"".join(
        _SECTION.replace(b"app.py", f"app{index:03d}.py".encode()) for index in range(300)
    )
    change = _change(text)
    semantic = _build(_case_with_material(), change=change)
    monkeypatch.setattr(semantic_case_module, "MAX_EGRESS_ENVELOPE_BYTES", 8_000)
    carried = check_time_change_parts_carried(semantic)
    assert 0 < carried < len(_change_items(semantic))
    envelope = strict_json_parse(semantic_case_module.bounded_case_envelope(semantic))
    assert isinstance(envelope, dict)
    catalog = {
        cast(dict[str, object], row)["item_id"]
        for row in cast(list[object], envelope["item_catalog"])
    }
    carried_text = b"".join(
        item.content.partition(b"\n")[2]
        for item in sorted(semantic.items, key=lambda item: item.item_id)
        if item.item_id in catalog and item.item_id.startswith(CHECK_TIME_CHANGE_ITEM_PREFIX)
    )
    selection = ReviewSelectionPolicy.for_profile(ReviewContextProfile.EXPANDED)

    shown = check_time_change_shown_files(change.capture, selection, carried)

    assert carried_text == text[: len(carried_text)]
    assert sum(file.shown_bytes for file in shown) == len(carried_text)
