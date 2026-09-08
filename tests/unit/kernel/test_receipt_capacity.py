"""Append-time receipt-capacity admission unions."""

from __future__ import annotations

from typing import cast

from builders.replay import replay_records
from yoetz.domain.events import AcceptedEvent, CheckRecordedPayload, LedgerRecord
from yoetz.domain.receipts import semantic_coverage_gap_code
from yoetz.kernel.deterministic_checks import healthy_storage_availability
from yoetz.kernel.receipt_capacity import receipt_gap_codes
from yoetz.kernel.reducers import replay


def _prefix_through_check() -> tuple[tuple[LedgerRecord, ...], CheckRecordedPayload]:
    """Return the accepted prefix ending at the recorded check, so that check stays applicable."""

    records = replay_records("all-event-families")
    index = next(
        position
        for position, record in enumerate(records)
        if type(record) is AcceptedEvent and type(record.payload) is CheckRecordedPayload
    )
    prefix = records[: index + 1]
    payload = cast(CheckRecordedPayload, cast(AcceptedEvent, prefix[-1]).payload)
    return prefix, payload


def test_admission_union_folds_the_semantic_outcome_gap() -> None:
    """Admission counts the semantic gap the receipt builder adds outside recorded coverage.

    ``execute_receipt`` folds ``semantic_coverage_gap_code`` separately from the applicable
    check payload's own ``known_gaps``. While admission skipped it, a state could be accepted
    holding exactly the 64 codes admission could see and then fail receipt construction on a
    65th the builder still needed.
    """

    prefix, payload = _prefix_through_check()
    projection = replay(prefix)
    assert projection.latest_tested_state is not None

    semantic_gap = semantic_coverage_gap_code(payload.semantic_status, payload.semantic_reason)
    assert semantic_gap is not None
    # The interesting shape: a gap the receipt needs that recorded coverage does not carry.
    assert semantic_gap not in payload.coverage.known_gaps
    assert semantic_gap in receipt_gap_codes(projection, prefix)


def test_admission_derives_availability_instead_of_asserting_none() -> None:
    """Admission derives its availability facts rather than asserting there are none.

    ``build_deterministic_case`` requires ``unavailable_event_ids`` to equal the rows the
    projection already records as unreadable, so admission cannot assert empty facts: every
    later append on such a task would raise ``deterministic_case_invalid`` out of the ledger.
    """

    prefix, _payload = _prefix_through_check()
    projection = replay(prefix)
    facts = healthy_storage_availability(projection, prefix)
    # A healthy store resolves every reference it holds, so only the rows the projection itself
    # records as unreadable are declared and no captured object is reported missing.
    assert facts.unavailable_captured_objects == ()
    assert receipt_gap_codes(projection, prefix)
