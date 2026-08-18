"""Privacy-safe bounded text projections of structured operation results."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Final, cast

from pydantic import BaseModel

from yoetz.protocol.canonical import JsonValue, ensure_canonical_value
from yoetz.protocol.errors import PublicErrorCode
from yoetz.protocol.ids import IdKind, is_valid_id

__all__ = [
    "render_safe_compact_summary",
    "summary_for_check",
    "summary_for_public_error",
    "summary_for_receipt",
    "summary_for_status",
]

_MAX_SUMMARY_BYTES: Final = 512
_SAFE_TOKEN: Final = re.compile(r"^[A-Za-z0-9_+.-]{1,128}$", re.ASCII)
# Closed shape for the frozen field and family tokens the repair clause may carry (issue #266).
_FIELD_NAME: Final = re.compile(r"^[a-z][a-z0-9_]{0,63}$", re.ASCII)
_SAFE_COUNT: Final = re.compile(r"^(?:0|[1-9][0-9]{0,18})$", re.ASCII)
_CORRELATION_ID: Final = re.compile(
    r"^err_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.ASCII,
)
# Closed shape for the frontier head: either the genesis sentinel or a canonical digest.
_HEAD_DIGEST: Final = re.compile(r"^(?:genesis|sha256:[0-9a-f]{64})$", re.ASCII)


def _mapping(value: object) -> Mapping[str, JsonValue]:
    if isinstance(value, BaseModel):
        dumped = cast(JsonValue, value.model_dump(mode="json", by_alias=True, exclude_unset=True))
        ensure_canonical_value(dumped)
        return cast(Mapping[str, JsonValue], dumped)
    if isinstance(value, Mapping):
        candidate = cast(JsonValue, value)
        ensure_canonical_value(candidate)
        return cast(Mapping[str, JsonValue], candidate)
    raise TypeError("summary_envelope_wrong_type")


def _safe_token(value: object, *, fallback: str = "unavailable") -> str:
    if isinstance(value, Enum):
        value = value.value
    if type(value) is str and _SAFE_TOKEN.fullmatch(value) is not None:
        return value
    return fallback


def _safe_count(value: object) -> str:
    if type(value) is int and 0 <= value <= 9_223_372_036_854_775_807:
        return str(value)
    if type(value) is str and _SAFE_COUNT.fullmatch(value) is not None:
        return value
    return "unavailable"


def _frontier_clause(envelope: Mapping[str, JsonValue]) -> str:
    """Render sequence plus head digest so the text channel alone can seed the next frontier.

    The text summary is the documented authoring fallback when a host drops structured
    content, and ``publish_work`` needs ``expected_frontier.head_digest``, not only the
    sequence, so a summary that carried the sequence alone could not construct the next
    request (issue #279).
    """

    for key in ("result_frontier", "subject_frontier", "head_frontier", "frontier"):
        frontier = envelope.get(key)
        if isinstance(frontier, Mapping):
            typed_frontier = cast(Mapping[str, JsonValue], frontier)
            sequence = _safe_count(typed_frontier.get("sequence"))
            if sequence != "unavailable":
                head = typed_frontier.get("head_digest")
                if type(head) is str and _HEAD_DIGEST.fullmatch(head) is not None:
                    return f"frontier: {sequence}; head_digest: {head}"
                return f"frontier: {sequence}"
    return "frontier: unavailable"


def _identity_clause(envelope: Mapping[str, JsonValue]) -> str:
    """Render the returned identifiers the next request must echo, when present (issue #279).

    Each value is re-gated against the strict public-id validator, so this projector admits
    the operation's own minted identifiers and nothing else.
    """

    parts: list[str] = []
    for label, key, kind in (
        ("task", "task_id", IdKind.TASK),
        ("session", "session_id", IdKind.SESSION),
        ("writer", "writer_id", IdKind.WRITER),
    ):
        value = envelope.get(key)
        if is_valid_id(kind, value):
            parts.append(f"{label}: {cast(str, value)}")
    if not parts:
        return ""
    return "; ".join(parts) + "; "


def _item_count(value: object) -> str:
    if isinstance(value, list | tuple):
        items = cast(Sequence[JsonValue], value)
        return str(min(len(items), 9_223_372_036_854_775_807))
    return "unavailable"


def _finding_identity_clause(source: Mapping[str, JsonValue], *, byte_budget: int) -> str:
    """Render as many revalidated finding IDs as the check summary can safely carry.

    A check's text projection is an authoring fallback.  When it reports actionable
    findings, the returned IDs are required to author ``respond`` (issue #324).
    """

    raw_findings = source.get("findings")
    if not isinstance(raw_findings, list | tuple) or byte_budget <= 0:
        return ""
    finding_ids = tuple(
        cast(str, raw.get("finding_id"))
        for raw in raw_findings
        if isinstance(raw, Mapping) and is_valid_id(IdKind.FINDING, raw.get("finding_id"))
    )
    if not finding_ids:
        return ""

    prefix = "finding IDs: "
    selected: list[str] = []
    for finding_id in finding_ids:
        selected.append(finding_id)
        remaining = len(finding_ids) - len(selected)
        suffix = f"; +{remaining} more" if remaining else ""
        clause = f"{prefix}{', '.join(selected)}{suffix}; "
        if len(clause.encode("ascii")) > byte_budget:
            selected.pop()
            break
    if not selected:
        return ""
    remaining = len(finding_ids) - len(selected)
    suffix = f"; +{remaining} more" if remaining else ""
    return f"{prefix}{', '.join(selected)}{suffix}; "


def _bounded(summary: str) -> str:
    try:
        encoded = summary.encode("ascii", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError("summary_not_english_ascii") from exc
    if len(encoded) > _MAX_SUMMARY_BYTES:
        raise ValueError("summary_too_large")
    return summary


def _repair_clause(error: Mapping[str, JsonValue]) -> str:
    """Render the bounded field-ownership repair fact, or "" when none travels on the error.

    Hosts are not required to surface structured content, so the one schema-derived repair
    sentence is repeated on the text channel (issue #266). Every token the sentence carries was
    drawn from frozen schema or registry content upstream, and each is re-gated here against the
    closed field-name shape so this projector admits nothing else.
    """

    details = error.get("safe_details")
    if not isinstance(details, Mapping):
        return ""
    typed = cast(Mapping[str, JsonValue], details)
    if typed.get("repair_kind") != "field_ownership":
        return ""
    field = typed.get("repair_field")
    owner = typed.get("repair_owning_family")
    selected = typed.get("repair_selected_family")
    for token in (field, owner, selected):
        if type(token) is not str or _FIELD_NAME.fullmatch(token) is None:
            return ""
    return f" Repair: {field} is admitted only by the {owner} payload, not {selected}."


def summary_for_public_error(envelope: object) -> str:
    """Render only the stable public error identity, never message or rejected input.

    The single exception is the bounded field-ownership repair fact: its tokens are frozen
    schema and registry content, never caller input, and a host that drops structured content
    would otherwise lose the only correction that stops a caller deleting a required record
    (issue #266).
    """

    source = _mapping(envelope)
    nested = source.get("error")
    error = _mapping(nested) if nested is not None else source
    code = _safe_token(error.get("code"), fallback="INTERNAL_ERROR")
    if code not in {item.value for item in PublicErrorCode}:
        code = "INTERNAL_ERROR"
    retryable = error.get("retryable")
    retry_text = "yes" if retryable is True else "no"
    correlation = error.get("correlation_id")
    correlation_text = (
        correlation
        if type(correlation) is str and _CORRELATION_ID.fullmatch(correlation) is not None
        else "unavailable"
    )
    return _bounded(
        f"Error {code}; retryable: {retry_text}; correlation: {correlation_text}."
        f"{_repair_clause(error)}"
    )


def summary_for_check(envelope: object) -> str:
    source = _mapping(envelope)
    verdict = _safe_token(source.get("verdict"))
    findings = _item_count(source.get("findings"))
    suppressed = _safe_count(source.get("suppressed_count"))
    status = _safe_token(source.get("semantic_status"))
    reason = _safe_token(source.get("semantic_reason"))
    if status == "not_requested":
        prefix = (
            f"Semantic review not requested; deterministic-only check verdict: {verdict}; "
            f"findings returned: {findings}; suppressed: {suppressed}; "
        )
    else:
        prefix = f"Check verdict: {verdict}; findings returned: {findings}; suppressed: {suppressed}; "
    suffix = f"semantic status/reason: {status}/{reason}; {_frontier_clause(source)}."
    clause = _finding_identity_clause(
        source,
        byte_budget=_MAX_SUMMARY_BYTES - len((prefix + suffix).encode("ascii")),
    )
    return _bounded(prefix + clause + suffix)


def _first_page_item(source: Mapping[str, JsonValue]) -> Mapping[str, JsonValue] | None:
    page = source.get("page")
    if not isinstance(page, Mapping):
        return None
    items = cast(Mapping[str, JsonValue], page).get("items")
    if not isinstance(items, list | tuple) or not items:
        return None
    item = items[0]
    if not isinstance(item, Mapping):
        return None
    return cast(Mapping[str, JsonValue], item)


def _status_freshness(
    source: Mapping[str, JsonValue], view: str, item: Mapping[str, JsonValue] | None
) -> str:
    """Report the compact singleton's own freshness, and the envelope's coverage otherwise.

    The compact item carries the projection's ledger freshness (``partial`` or
    ``stale_after_material_change`` after material change); the result's ``coverage`` carries the
    newest record envelope's, which is routinely ``current`` at exactly that frontier. This summary
    is the documented fallback when a host drops structured content, so it must never read stronger
    than the view it stands in for. Only ``compact`` items carry a ledger freshness — an
    ``evidence`` row's ``freshness`` describes that evidence, not the ledger.
    """

    if view == "compact" and item is not None:
        freshness = _safe_token(item.get("freshness"))
        if freshness != "unavailable":
            return freshness
    coverage = source.get("coverage")
    if isinstance(coverage, Mapping):
        return _safe_token(cast(Mapping[str, JsonValue], coverage).get("ledger_freshness"))
    return "unavailable"


def _compact_status_fields(source: Mapping[str, JsonValue], view: str) -> tuple[str, str, str, str]:
    item = _first_page_item(source)
    readiness = source.get("closure_readiness")
    if isinstance(readiness, Mapping):
        typed_readiness = cast(Mapping[str, JsonValue], readiness)
        return (
            _status_freshness(source, view, item),
            _safe_count(typed_readiness.get("open_obligation_count")),
            _safe_count(typed_readiness.get("unanswered_finding_count")),
            _safe_count(typed_readiness.get("receipt_blocking_finding_count")),
        )
    if item is None:
        return "unavailable", "unavailable", "unavailable", "unavailable"
    return (
        _safe_token(item.get("freshness")),
        _safe_count(item.get("open_obligation_count")),
        _safe_count(item.get("unanswered_finding_count")),
        _safe_count(item.get("receipt_blocking_finding_count")),
    )


def summary_for_status(envelope: object) -> str:
    source = _mapping(envelope)
    view = _safe_token(source.get("view"))
    freshness, obligations, unanswered, receipt_blocking = _compact_status_fields(source, view)
    gaps = _item_count(source.get("gaps"))
    return _bounded(
        f"Status view: {view}; {_frontier_clause(source)}; freshness: {freshness}; "
        f"open obligations: {obligations}; unanswered findings: {unanswered}; "
        f"receipt-blocking findings: {receipt_blocking}; reported gaps: {gaps}."
    )


def summary_for_receipt(envelope: object) -> str:
    source = _mapping(envelope)
    conclusion = _safe_token(source.get("conclusion"))
    coverage = source.get("coverage")
    limitations = "unavailable"
    if isinstance(coverage, Mapping):
        typed_coverage = cast(Mapping[str, JsonValue], coverage)
        limitations = _item_count(typed_coverage.get("known_gaps"))
    suppressed = _safe_count(source.get("suppressed_finding_count"))
    return _bounded(
        f"Receipt conclusion: {conclusion}; {_frontier_clause(source)}; "
        f"coverage limitations: {limitations}; suppressed findings: {suppressed}."
    )


def _summary_for_other_success(source: Mapping[str, JsonValue]) -> str:
    outcome = _safe_token(source.get("outcome"), fallback="recorded")
    identity = _identity_clause(source)
    accepted = source.get("accepted_events")
    if accepted is not None:
        return _bounded(
            f"Operation outcome: {outcome}; accepted events: {_item_count(accepted)}; "
            f"{identity}{_frontier_clause(source)}."
        )
    return _bounded(f"Operation outcome: {outcome}; {identity}{_frontier_clause(source)}.")


def render_safe_compact_summary(envelope: object) -> str:
    """Render a bounded projection using only allowlisted structural result fields."""

    source = _mapping(envelope)
    if source.get("ok") is False or "error" in source:
        return summary_for_public_error(source)
    if "verdict" in source:
        return summary_for_check(source)
    if "view" in source:
        return summary_for_status(source)
    if "receipt_id" in source or "conclusion" in source:
        return summary_for_receipt(source)
    return _summary_for_other_success(source)
