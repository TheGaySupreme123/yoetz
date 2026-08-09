"""Bounded, non-strengthening human renderers for public CLI results."""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum

from yoetz.protocol.models import (
    CheckAwaitingHumanModel,
    CheckProjectedFindingModel,
    CheckSuccessModel,
    OmittedContentModel,
    PublicErrorModel,
    ReceiptSuccessModel,
    StatusCompactPageModel,
    StatusFindingsPageModel,
    StatusOperationPageModel,
    StatusSuccessModel,
)

__all__ = [
    "render_human_awaiting_human",
    "render_human_check",
    "render_human_error",
    "render_human_findings",
    "render_human_receipt",
    "render_human_status",
]


def _token(value: object) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _projected_text(value: str | OmittedContentModel | None) -> str:
    if value is None:
        return "none"
    if isinstance(value, OmittedContentModel):
        return f"[omitted: {_token(value.category)}; {value.reason}]"
    return value


def render_human_findings(
    findings: Sequence[CheckProjectedFindingModel] | Sequence[object],
) -> str:
    """Render at most the first three already-ordered findings."""

    if not findings:
        return "Findings: none"
    lines = ["Findings:"]
    for finding in findings[:3]:
        kind = _token(getattr(finding, "kind", "unknown"))
        priority = getattr(finding, "priority", "unknown")
        origin = _token(getattr(finding, "origin", "unknown"))
        summary = _projected_text(getattr(finding, "summary", None))
        lines.append(f"- P{priority} {kind} ({origin}): {summary}")
        detail = getattr(finding, "detail", None)
        if detail is not None:
            lines.append(f"  {_projected_text(detail)}")
    remaining = len(findings) - 3
    if remaining > 0:
        lines.append(f"Additional findings not shown: {remaining}")
    return "\n".join(lines)


def render_human_check(result: CheckSuccessModel) -> str:
    """Render an exact check verdict and bounded semantic status."""

    if type(result) is not CheckSuccessModel:
        raise TypeError("check_result_invalid")
    lines = [
        f"Verdict: {result.verdict}",
        f"Semantic review: {_token(result.semantic_status)} ({_token(result.semantic_reason)})",
        render_human_findings(result.findings),
    ]
    suppressed = int(result.suppressed_count)
    if suppressed:
        lines.append(f"Suppressed findings: {suppressed}")
    if result.coverage.known_gaps:
        lines.append("Coverage gaps: " + ", ".join(result.coverage.known_gaps))
    return "\n".join(lines)


def render_human_status(result: StatusSuccessModel) -> str:
    """Render current structural status without dumping the ledger."""

    if type(result) is not StatusSuccessModel:
        raise TypeError("status_result_invalid")
    lines = [
        f"Frontier: {result.head_frontier.sequence}",
        f"Freshness: {_token(result.coverage.ledger_freshness)}",
    ]
    if isinstance(result.page, StatusCompactPageModel) and result.page.items:
        item = result.page.items[0]
        lines.extend(
            (
                f"Open obligations: {item.open_obligation_count}",
                f"Unresolved findings: {item.unresolved_finding_count}",
            )
        )
    elif isinstance(result.page, StatusFindingsPageModel):
        unresolved = sum(not item.resolved for item in result.page.items)
        lines.extend(
            ("Open obligations: unavailable in this view", f"Unresolved findings: {unresolved}")
        )
    elif isinstance(result.page, StatusOperationPageModel):
        lines.extend(
            (
                "Open obligations: unavailable in this view",
                "Unresolved findings: unavailable in this view",
                f"Operation: {result.page.operation_request_id} ({result.page.state})",
            )
        )
        if result.page.continuation is not None:
            lines.extend(
                (
                    f"Continuation: {result.page.continuation.kind}",
                    "Trusted command: " + " ".join(result.page.continuation.command),
                    f"Replay request ID: {result.page.continuation.replay_request_id}",
                )
            )
    else:
        lines.extend(
            (
                "Open obligations: unavailable in this view",
                "Unresolved findings: unavailable in this view",
            )
        )
    gaps = tuple(result.gaps) + tuple(result.coverage.known_gaps)
    lines.append("Gaps: " + (", ".join(dict.fromkeys(gaps)) if gaps else "none"))
    return "\n".join(lines)


def render_human_receipt(result: ReceiptSuccessModel) -> str:
    """Render the receipt conclusion without claiming stronger assurance."""

    if type(result) is not ReceiptSuccessModel:
        raise TypeError("receipt_result_invalid")
    lines = [f"Conclusion: {result.conclusion}"]
    if result.human_text is not None:
        lines.append(_projected_text(result.human_text))
    limitations = tuple(result.coverage.known_gaps)
    lines.append("Limitations: " + (", ".join(limitations) if limitations else "none declared"))
    if result.suppressed_finding_count:
        lines.append(f"Suppressed findings: {result.suppressed_finding_count}")
    return "\n".join(lines)


def render_human_error(error: PublicErrorModel) -> str:
    """Render only the bounded public error fields."""

    if type(error) is not PublicErrorModel:
        raise TypeError("public_error_invalid")
    suffix = " (retryable)" if error.retryable else ""
    return f"{_token(error.code)}: {error.message}{suffix}"


def render_human_awaiting_human(result: CheckAwaitingHumanModel) -> str:
    """Render the nonterminal check branch as an instruction, never as a conclusion.

    No verdict or coverage appears here because none exists yet. Printing this like an ordinary
    check result is how a suspended check gets mistaken for a completed one.
    """

    if type(result) is not CheckAwaitingHumanModel:
        raise TypeError("check_result_invalid")
    continuation = result.continuation
    lines = [
        "Semantic review: awaiting_human (human_approval_required)",
        "",
        "This check is paused for trusted local privacy authority. No verdict yet.",
        "",
        f"  {' '.join(continuation.command)}",
        "",
    ]
    if continuation.pending_id is not None:
        lines.append(f"Pending decision: {continuation.pending_id}")
    if continuation.expires_at is not None:
        lines.append(f"Expires at: {continuation.expires_at}")
    lines.append(f"Then replay the same check with request_id {continuation.replay_request_id}.")
    return "\n".join(lines)
