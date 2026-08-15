"""Pure receipt-capacity admission checks for proposed ledger states."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from yoetz.domain.events import CheckRecordedPayload, LedgerRecord
from yoetz.domain.findings import Finding, rank_key
from yoetz.domain.receipts import (
    CHECK_CURRENT_AS_OF_EARLIER_FRONTIER_GAP,
    semantic_coverage_gap_code,
)
from yoetz.kernel.deterministic_checks import (
    build_deterministic_case,
    healthy_storage_availability,
)
from yoetz.kernel.projections import ProjectionState
from yoetz.kernel.reducers import invalidates_recorded_check, is_material_event_family
from yoetz.protocol.coverage import MAX_KNOWN_GAPS

__all__ = [
    "ReceiptCoverageCapacityExceeded",
    "current_receipt_findings",
    "receipt_gap_codes",
    "validate_receipt_coverage_capacity",
]

_CHECK_NOT_RECORDED: Final = "check_not_recorded"
_CHECK_NOT_APPLICABLE: Final = "check_not_applicable"
_CHECK_PAYLOAD_UNAVAILABLE: Final = "check_payload_unavailable"


@dataclass(frozen=True, slots=True)
class ReceiptCoverageCapacityExceeded(ValueError):
    """The exact proposed receipt gap union exceeds the public coverage bound."""

    count: int
    limit: int = MAX_KNOWN_GAPS

    def __post_init__(self) -> None:
        if type(self.count) is not int or self.count <= self.limit:
            raise ValueError("invalid_receipt_coverage_capacity")
        ValueError.__init__(self, "receipt_coverage_capacity_exceeded")


def _issue_key(finding: Finding) -> tuple[object, ...]:
    return (
        finding.origin,
        finding.policy_id,
        finding.policy_version,
        finding.kind,
        finding.subject_refs,
    )


def current_receipt_findings(projection: ProjectionState) -> tuple[Finding, ...]:
    """Select the newest readable row per receipt issue key in canonical rank order."""

    if type(projection) is not ProjectionState:
        raise ValueError("receipt_coverage_capacity_invalid")
    newest: dict[tuple[object, ...], tuple[int, Finding]] = {}
    for record in projection.findings.values():
        if record.payload is None:
            continue
        key = _issue_key(record.payload)
        candidate = (record.source_frontier, record.payload)
        prior = newest.get(key)
        if prior is None or candidate[0] > prior[0]:
            newest[key] = candidate
    return tuple(sorted((item[1] for item in newest.values()), key=rank_key))


def receipt_gap_codes(
    projection: ProjectionState,
    records: tuple[LedgerRecord, ...],
) -> tuple[str, ...]:
    """Return the exact healthy-storage gap union a receipt would need for this state."""

    if type(projection) is not ProjectionState or type(records) is not tuple:
        raise ValueError("receipt_coverage_capacity_invalid")
    case = build_deterministic_case(
        projection, records, healthy_storage_availability(projection, records)
    )
    codes = {gap.code for gap in case.gaps}
    for coverage in case.coverage_by_ref.values():
        codes.update(coverage.known_gaps)

    latest = projection.latest_tested_state
    check_record = None
    if latest is not None:
        check_record = next(
            (record for record in records if record.event_id == latest.source_check_event_id),
            None,
        )
    if latest is None:
        codes.add(_CHECK_NOT_RECORDED)
    elif check_record is not None and any(
        invalidates_recorded_check(
            record,
            check_record.ledger.ingestion_sequence,
            latest.returned_finding_ids,
        )
        for record in records
    ):
        codes.add(_CHECK_NOT_APPLICABLE)
    elif check_record is not None and type(check_record.payload) is CheckRecordedPayload:
        codes.update(check_record.payload.coverage.known_gaps)
        # The receipt builder folds the semantic outcome's structural gap separately from the
        # recorded coverage. A legacy payload can carry a terminal semantic status whose code is
        # absent from ``known_gaps``; omitting it here would admit a state at exactly the bound
        # that receipt construction then pushes one code over.
        semantic_gap = semantic_coverage_gap_code(
            check_record.payload.semantic_status, check_record.payload.semantic_reason
        )
        if semantic_gap is not None:
            codes.add(semantic_gap)
        if any(
            is_material_event_family(record.schema.name)
            and record.ledger.ingestion_sequence > check_record.ledger.ingestion_sequence
            for record in records
        ):
            codes.add(CHECK_CURRENT_AS_OF_EARLIER_FRONTIER_GAP)
    else:
        codes.add(_CHECK_PAYLOAD_UNAVAILABLE)

    for finding in current_receipt_findings(projection):
        codes.update(finding.coverage.known_gaps)
    return tuple(sorted(codes, key=str.encode))


def validate_receipt_coverage_capacity(
    projection: ProjectionState,
    records: tuple[LedgerRecord, ...],
) -> None:
    """Reject a proposed state whose exact healthy receipt union exceeds the wire bound."""

    codes = receipt_gap_codes(projection, records)
    if len(codes) > MAX_KNOWN_GAPS:
        raise ReceiptCoverageCapacityExceeded(len(codes))
