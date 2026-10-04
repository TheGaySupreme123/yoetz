"""Eligibility for the bounded local verdict used by deterministic-only checks (#971).

The local verdict is a statement about the deterministic scope, not a claim that the work is
correct or that a provider reviewed it.  Every coverage gap remains on the result and receipt.
Only the small set of advisory/standing gaps may accompany the scoped result; counters from the
frozen case keep missing work from being mistaken for a clean local check.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final, cast

from yoetz.domain.check_totals import validate_check_totals
from yoetz.domain.findings import FINDING_KIND_TRAITS, Finding
from yoetz.domain.receipts import (
    PREEXISTING_TEST_INFORMATIONAL_GAPS,
    SEMANTIC_REVIEW_NOT_REQUESTED_GAP,
)
from yoetz.kernel.plan_drift import PLAN_DRIFT_GAPS
from yoetz.protocol.coverage import Coverage

__all__ = [
    "DETERMINISTIC_SCOPED_STANDING_GAPS",
    "deterministic_scope_is_clean",
]


# These codes describe bounded standing/advisory facts.  Unknown, actionable, or material
# coverage loss must keep the ordinary insufficient-coverage verdict.
DETERMINISTIC_SCOPED_STANDING_GAPS: Final = frozenset(
    {
        SEMANTIC_REVIEW_NOT_REQUESTED_GAP,
        *PLAN_DRIFT_GAPS,
        *PREEXISTING_TEST_INFORMATIONAL_GAPS,
    }
)


def _counter(totals: Mapping[str, object], group: str, key: str) -> int | None:
    values = totals.get(group)
    if not isinstance(values, Mapping):
        return None
    value = cast(Mapping[str, object], values).get(key)
    if type(value) is not str or not value.isascii() or not value.isdecimal():
        return None
    return int(value)


def deterministic_scope_is_clean(
    *,
    coverage: Coverage,
    totals: Mapping[str, object],
    findings: Sequence[Finding],
) -> bool:
    """Return whether a deterministic-only check may use its scoped local verdict.

    The caller supplies totals built from the same frozen case and all candidate findings before
    the public finding cap.  Thus a suppressed actionable finding, an unattempted requested item,
    or a relevant live failure cannot disappear behind the returned finding list.
    """

    if type(coverage) is not Coverage:
        return False
    try:
        checked = validate_check_totals(totals)
    except ValueError:
        return False
    if not set(coverage.known_gaps) <= DETERMINISTIC_SCOPED_STANDING_GAPS:
        return False
    if any(type(finding) is not Finding for finding in findings):
        return False
    if any(FINDING_KIND_TRAITS[finding.kind][1] for finding in findings):
        return False
    values = checked
    if _counter(values, "obligations", "scope_known") != 1:
        return False
    if any(_counter(values, "obligations", key) not in {0} for key in ("open", "unreadable")):
        return False
    if _counter(values, "requested_items", "unattempted") != 0:
        return False
    # Unknown and live outcomes cannot establish that the local command set is complete.  In
    # normal operation the corresponding host-outcome gap also fails the standing-gap allowlist;
    # keeping this counter check makes the predicate fail closed for older or partial records.
    if any(_counter(values, "commands", key) not in {0} for key in ("unknown", "live_failed")):
        return False
    # A pre-existing test edit is a standing informational fact only when its disposition is
    # justified. Unknown or unjustified edits remain actionable even if the public finding cap
    # does not return their integrity finding.
    if any(_counter(values, "test_edits", key) not in {0} for key in ("unjustified", "unknown")):
        return False
    return True
