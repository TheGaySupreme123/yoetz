"""Bounded, non-strengthening human renderers for public CLI results."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import Enum
from typing import cast

from yoetz.protocol.canonical import JsonValue
from yoetz.protocol.errors import normalize_safe_details
from yoetz.protocol.models import (
    CheckAwaitingHumanModel,
    CheckProjectedFindingModel,
    CheckSuccessModel,
    OmittedContentModel,
    PublicErrorModel,
    ReceiptSuccessModel,
    StatusAdvicePageModel,
    StatusFindingsPageModel,
    StatusLineagePageModel,
    StatusObligationsPageModel,
    StatusOperationPageModel,
    StatusProjectPageModel,
    StatusSemanticProgressModel,
    StatusSuccessModel,
)
from yoetz.protocol.recovery import (
    RecoveryDirective,
    TimeoutOperationKind,
    continuation_for_local_reason,
    continuation_for_reason,
    continuation_for_semantic_outcome,
    correction_for_invariant,
    directive_for,
)

__all__ = [
    "bounded_failure_line",
    "ceremony_refusal_line",
    "error_recovery_json",
    "local_recovery_json",
    "recovery_directive_json",
    "render_error_recovery_lines",
    "render_hook_recovery_suffix",
    "render_human_awaiting_human",
    "render_human_check",
    "render_human_error",
    "render_human_findings",
    "render_human_receipt",
    "render_human_status",
    "render_local_recovery_lines",
    "render_recovery_directive_lines",
    "render_semantic_outcome_recovery_lines",
    "render_semantic_progress_lines",
]


def _token(value: object) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _count(value: str | None) -> str:
    """Render an unknown readiness count as unknown, never as a bare ``None`` or a zero."""

    return "unavailable" if value is None else value


def _failure_class_from_provenance(provenance: object) -> object | None:
    if provenance is None:
        return None
    if isinstance(provenance, Mapping):
        return cast(Mapping[str, object], provenance).get("failure_class")
    return getattr(provenance, "failure_class", None)


def render_semantic_outcome_recovery_lines(
    *,
    status: object,
    reason: object,
    provenance: object = None,
) -> list[str]:
    """Resolve provider-review recovery from recorded status, never from findings."""

    token = continuation_for_semantic_outcome(
        status=status,
        reason=reason,
        failure_class=_failure_class_from_provenance(provenance),
    )
    directive = directive_for(token)
    if directive is None:
        return []
    return render_recovery_directive_lines(directive)


def _semantic_outcome_recovery_lines(
    *,
    status: object,
    reason: object,
    provenance: object = None,
) -> list[str]:
    return render_semantic_outcome_recovery_lines(
        status=status, reason=reason, provenance=provenance
    )


def render_hook_recovery_suffix(
    reason: object,
    *,
    operation_kind: TimeoutOperationKind | None = None,
) -> str:
    """Return the hook-budget suffix for a typed reason, or an empty string.

    Hook intake is bounded at 512 ASCII bytes. The suffix therefore carries the
    continuation token only; each host hook keeps its own framing, and the
    directive text is reconstructed from the same registry on CLI and MCP.
    """

    token = continuation_for_local_reason(reason)
    if token is None:
        token = continuation_for_reason(reason, operation_kind=operation_kind)
    if token is None:
        return ""
    return f" Continuation: {token}."


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
    """Render an exact check verdict and bounded AI-powered review status."""

    if type(result) is not CheckSuccessModel:
        raise TypeError("check_result_invalid")
    lines = [
        f"Verdict: {result.verdict}",
        f"AI-powered review: {_token(result.semantic_status)} ({_token(result.semantic_reason)})",
        render_human_findings(result.findings),
    ]
    recovery = _semantic_outcome_recovery_lines(
        status=result.semantic_status,
        reason=result.semantic_reason,
        provenance=result.semantic_provenance,
    )
    if recovery:
        lines.extend(recovery)
    suppressed = int(result.suppressed_count)
    if suppressed:
        lines.append(f"Suppressed findings: {suppressed}")
    if result.children is not None:
        children = result.children
        lines.append(f"Child dependencies ({children.label}):")
        if children.tested_manifest_frontier is not None:
            lines.append(
                "Tested manifest frontier: "
                f"{children.tested_manifest_frontier.sequence} "
                f"({children.tested_manifest_frontier.head_digest})"
            )
        if children.label == "preview":
            lines.append("Preview facts do not change the recorded check.")
        for child in children.items:
            lines.append(
                f"- {child.child_task_id}: {_token(child.origin)}, {_token(child.acceptance)}; "
                f"work {_token(child.work_state)}, session {_token(child.session_health)}; "
                f"rollup {_token(child.rollup_state)}"
            )
            if child.blocking_conditions:
                lines.append("  Completion gaps: " + ", ".join(child.blocking_conditions))
    if result.advisory_notes:
        lines.append("Project advice (does not affect the verdict):")
        for note in result.advisory_notes:
            lines.append(
                f"- {note.kind}: {note.count}; project {note.project_id}; "
                "tasks " + ", ".join(note.task_ids)
            )
    if result.coverage.known_gaps:
        lines.append("Coverage gaps: " + ", ".join(result.coverage.known_gaps))
        # The strict ceiling blocked this process while the last install applied the policy
        # route (issue #537). A ceiling with no applied-policy record keeps today's terminal
        # wording with no recovery line.
        if "optional_semantic_review_registration_drift" in tuple(result.coverage.known_gaps):
            lines.append(
                "The last install applied the policy route. If this strict route was not "
                "intended, re-run `yoetz integrate codex mcp preview` and "
                "`yoetz integrate codex mcp install --route-profile policy`, then start "
                "a fresh Codex process."
            )
    return "\n".join(lines)


def render_semantic_progress_lines(progress: StatusSemanticProgressModel) -> tuple[str, ...]:
    """Render structural AI-powered review progress exactly as the JSON page states it.

    Only closed phase, outcome, and reason tokens plus service timestamps and derived whole
    seconds appear. Progress is a service observation, not evidence that the review is correct.
    """

    if type(progress) is not StatusSemanticProgressModel:
        raise TypeError("status_semantic_progress_invalid")
    elapsed = int(progress.elapsed_ms) // 1000
    lines = [
        f"Semantic review phase: {_token(progress.phase)} "
        f"(attempt {progress.attempt_ordinal}, {progress.condition})"
    ]
    if progress.condition == "terminal":
        lines.append(
            f"Semantic review outcome: {progress.terminal_outcome} "
            f"({_token(progress.terminal_reason)}); elapsed {elapsed}s"
        )
        return tuple(lines)
    remaining = int(progress.remaining_ms or "0") // 1000
    lines.append(
        f"Semantic review elapsed: {elapsed}s; remaining {remaining}s; "
        f"deadline {progress.deadline_at}"
    )
    if progress.condition == "overdue":
        lines.append(
            "Semantic review deadline passed without a terminal record. Retry the same "
            "request_id to recover the terminal result."
        )
    return tuple(lines)


def render_human_status(result: StatusSuccessModel) -> str:
    """Render current structural status without dumping the ledger."""

    if type(result) is not StatusSuccessModel:
        raise TypeError("status_result_invalid")
    lines = [
        f"Frontier: {result.head_frontier.sequence}",
        f"Freshness: {_token(result.coverage.ledger_freshness)}",
        f"Open obligations: {_count(result.closure_readiness.open_obligation_count)}",
        f"Unanswered findings: {_count(result.closure_readiness.unanswered_finding_count)}",
        (
            "Receipt-blocking findings: "
            f"{_count(result.closure_readiness.receipt_blocking_finding_count)}"
        ),
    ]
    if isinstance(result.page, StatusOperationPageModel):
        lines.extend((f"Operation: {result.page.operation_request_id} ({result.page.state})",))
        if result.page.continuation is not None:
            lines.extend(
                (
                    f"Continuation: {result.page.continuation.kind}",
                    "Trusted command: " + " ".join(result.page.continuation.command),
                    f"Replay request ID: {result.page.continuation.replay_request_id}",
                )
            )
        if result.page.semantic_progress is not None:
            lines.extend(render_semantic_progress_lines(result.page.semantic_progress))
    elif isinstance(result.page, StatusLineagePageModel):
        lines.extend(_render_lineage(result.page))
    elif isinstance(result.page, StatusAdvicePageModel):
        lines.append("Advice:")
        for item in result.page.items:
            if item.coordination_detection_id is not None:
                lines.append(
                    f"- Coordination overlap {item.coordination_detection_id}: "
                    f"counterpart {item.coordination_counterpart_task_id}; "
                    f"project {item.coordination_project_id}"
                )
                paths = item.coordination_resource_paths
                if paths is not None:
                    if isinstance(paths, OmittedContentModel):
                        lines.append("  Resources: " + _projected_text(paths))
                    else:
                        lines.append("  Resources: " + ", ".join(paths))
            else:
                lines.append(
                    f"- {item.rule_code}: priority {item.priority}; "
                    f"next {item.recommended_next_action}"
                )
        if result.page.next_cursor is not None:
            lines.append(f"Next page: {result.page.next_cursor}")
    elif isinstance(result.page, StatusProjectPageModel):
        page = result.page
        lines.extend(
            (
                f"Project: {page.project_id} ({_token(page.kind)})",
                f"Membership generation: {page.membership_generation}",
                f"Coordination grant: {_token(page.grant_state)}",
                "Members:",
            )
        )
        if page.title is not None:
            lines.insert(len(lines) - 1, "Title: " + _projected_text(page.title))
        if page.description is not None:
            lines.insert(len(lines) - 1, "Description: " + _projected_text(page.description))
        for member in page.members:
            lines.append(
                f"- {member.task_id}: {_token(member.work_state)}, "
                f"session {_token(member.session_health)} ({member.actor_id or 'unknown actor'})"
            )
        lines.extend(_render_lineage(page.lineage))
        lines.append("Coordination detections:")
        for detection in page.detections:
            lines.append(
                f"- {detection.detection_id}: {detection.resource_count} resources; "
                f"{'open' if detection.open else 'addressed'}"
            )
            if detection.resource_paths is not None:
                if isinstance(detection.resource_paths, OmittedContentModel):
                    lines.append("  Resources: " + _projected_text(detection.resource_paths))
                else:
                    lines.append("  Resources: " + ", ".join(detection.resource_paths))
        lines.append("Coordination coverage:")
        for coverage in page.coverage:
            lines.append(f"- {coverage.task_id}: {coverage.coverage} ({coverage.gap_code})")
        lines.append("Member receipts:")
        for receipt in page.receipts:
            lines.append(f"- {receipt.task_id}: {receipt.conclusion} ({receipt.receipt_id})")
        if page.next_cursor is not None:
            lines.append(f"Next page: {page.next_cursor}")
    if isinstance(result.page, StatusFindingsPageModel):
        for finding in result.page.items:
            lines.append(
                f"{finding.finding_id} resolved={finding.resolved}: "
                + _projected_text(finding.detail)
            )
    if isinstance(result.page, StatusObligationsPageModel):
        for obligation in result.page.items:
            for attempt in obligation.command_attempts[:3]:
                lines.append(
                    f"{obligation.obligation_id} command item {attempt.requested_item_index}: {attempt.relation} (attempt only, not success)"
                )
            remaining = len(obligation.command_attempts) - 3
            if remaining > 0:
                lines.append(
                    f"{obligation.obligation_id}: {remaining} more command attempts; use JSON status for all items"
                )
    gaps = tuple(result.gaps) + tuple(result.coverage.known_gaps)
    lines.append("Gaps: " + (", ".join(dict.fromkeys(gaps)) if gaps else "none"))
    return "\n".join(lines)


def _render_lineage(page: StatusLineagePageModel) -> list[str]:
    lines = [f"Parent task: {page.parent_task_id or 'none'}", "Child tasks:"]
    if not page.children:
        lines.append("- none in this page")
    for child in page.children:
        lines.append(
            f"- {child.task_id}: {_token(child.origin)}, {_token(child.acceptance)}; "
            f"work {_token(child.work_state)}, session {_token(child.session_health)}; "
            f"rollup {_token(child.rollup_state)}"
        )
        if child.blocking_conditions:
            lines.append("  Completion gaps: " + ", ".join(child.blocking_conditions))
    for annotation in page.annotations:
        lines.append(f"- Observed subagent {annotation.correlation_id}: pending child binding")
    if page.next_cursor is not None:
        lines.append(f"Next page: {page.next_cursor}")
    return lines


def render_human_receipt(result: ReceiptSuccessModel) -> str:
    """Render the receipt conclusion without claiming stronger assurance."""

    if type(result) is not ReceiptSuccessModel:
        raise TypeError("receipt_result_invalid")
    lines = [f"Conclusion: {result.conclusion}"]
    if result.human_text is not None:
        lines.append(_projected_text(result.human_text))
    limitations = tuple(result.coverage.known_gaps)
    lines.append("Limitations: " + (", ".join(limitations) if limitations else "none declared"))
    document = result.document
    if isinstance(document, Mapping):
        provenance = document.get("semantic_provenance")
        if isinstance(provenance, Mapping):
            lines.extend(
                render_semantic_outcome_recovery_lines(
                    status=provenance.get("status"),
                    reason=provenance.get("reason"),
                    provenance=provenance,
                )
            )
    if result.suppressed_finding_count:
        lines.append(f"Suppressed findings: {result.suppressed_finding_count}")
    return "\n".join(lines)


def render_recovery_directive_lines(
    directive: RecoveryDirective, *, commands: Sequence[str] = ()
) -> list[str]:
    """Render one frozen directive the way every CLI error surface shows it.

    The CLI has no 512-byte ceiling, so unlike the MCP text projection it renders the whole
    directive, any carried commands, its guidance pointer, and its nudge on separate lines.
    """

    lines = [f"Continuation: {directive.token}", f"Next: {directive.directive}"]
    if commands:
        lines.append("Commands: " + "; ".join(commands))
    if directive.guidance_uri is not None:
        lines.append(f"Guidance: {directive.guidance_uri}")
    if directive.nudge is not None:
        lines.append(directive.nudge)
    return lines


def render_local_recovery_lines(reason: object) -> list[str]:
    """Return the directive lines for a CLI lifecycle, instance, or ceremony reason, or [].

    These reasons never become a public error envelope: the CLI prints a bounded token line and
    exits. Before ADR-030 the line carried a remediation sentence and nothing else, so the same
    condition that gets a typed directive over MCP got none here. The lookup is the local-reason
    vocabulary only; a protocol reason code deliberately resolves to nothing through it.
    """

    directive = directive_for(continuation_for_local_reason(reason))
    if directive is None:
        return []
    return render_recovery_directive_lines(directive)


def bounded_failure_line(reason: str, *, prefix: str | None = None) -> str:
    """Render one bounded local reason with its remediation and its recovery directive.

    The single shape every human-rendered CLI refusal uses (issue #741): the bounded token stays
    first so machine-readable expectations hold, the per-reason remediation follows it on the same
    line, and the registry directive follows on its own lines. A reason with neither is returned
    unchanged, so a caller can tell "nothing is known about this token" from the result.
    """

    from yoetz.cli.exits import remediation_message

    head = reason if prefix is None else f"{prefix}: {reason}"
    remediation = remediation_message(reason)
    line = head if remediation is None else f"{head}: {remediation}"
    return "\n".join([line, *render_local_recovery_lines(reason)])


def ceremony_refusal_line(reason: str) -> str | None:
    """Render a structural ceremony refusal with its directive, or None when unmapped.

    A declined confidential ceremony already carries its own operator-facing sentence, token
    first. Only the directive lines are added, so the refusal reads the same way it did while
    gaining the continuation an agent needs to stop rather than restart a healthy service.
    """

    from yoetz.cli.exits import ceremony_refusal_message

    message = ceremony_refusal_message(reason)
    if message is None:
        return None
    return "\n".join([message, *render_local_recovery_lines(reason)])


def _resolve_error_recovery(
    safe_details: object,
) -> tuple[tuple[str, str] | None, RecoveryDirective | None, list[str]]:
    """Resolve the recovery facts of a public error from its gated safe details only.

    Returns the claim-revision invariant and correction, the continuation directive, and the
    carried frozen commands. Nothing is read from the error message, and a token the protocol
    normalizer does not admit resolves to nothing. The human and JSON renderings both read this,
    so they cannot disagree about what an error says to do.
    """

    if not isinstance(safe_details, Mapping):
        return None, None, []
    source = cast(Mapping[str, object], safe_details)
    # A claim-revision rejection carries its correction on the invariant rather than a
    # continuation token. The CLI rendered nothing for it before ADR-030 moved the corrective
    # phrases into the shared registry, so the same rejection read as bare prose here while the
    # MCP text channel explained it.
    revision_pair: tuple[str, str] | None = None
    revision = normalize_safe_details(
        {"invariant": source.get("invariant"), "reason_code": source.get("reason_code")}
    )
    if revision.get("reason_code") == "claim_revision_mismatch":
        correction = correction_for_invariant(revision.get("invariant"))
        if correction is not None:
            revision_pair = (str(revision["invariant"]), correction)
    gated = normalize_safe_details({"continuation": source.get("continuation")})
    directive = directive_for(gated.get("continuation"))
    if directive is None:
        return revision_pair, None, []
    gated_commands = normalize_safe_details(
        {key: source.get(key) for key in ("prepare_command", "review_command", "authorize_command")}
    )
    commands = [
        str(gated_commands[key])
        for key in ("prepare_command", "review_command", "authorize_command")
        if type(gated_commands.get(key)) is str
    ]
    return revision_pair, directive, commands


def render_error_recovery_lines(safe_details: object) -> list[str]:
    """Return the frozen recovery directive lines for a typed continuation, or an empty list.

    The directive is reconstructed locally from the continuation token (issue #739); nothing is
    read from the error message, and no line is rendered for a token the protocol normalizer does
    not admit. The CLI has no 512-byte ceiling, so unlike the MCP text projection it renders the
    whole directive, its guidance pointer, and its nudge on separate lines.
    """

    revision, directive, commands = _resolve_error_recovery(safe_details)
    lines: list[str] = []
    if revision is not None:
        lines.append(f"Invariant: {revision[0]}")
        lines.append(f"Correction: {revision[1]}")
    if directive is not None:
        lines.extend(render_recovery_directive_lines(directive, commands=commands))
    return lines


def recovery_directive_json(
    directive: RecoveryDirective, *, commands: Sequence[str] = ()
) -> dict[str, JsonValue]:
    """Render one frozen directive as the ``recovery`` object of a CLI-owned JSON body.

    The same facts, in the same order, as ``render_recovery_directive_lines`` (ADR-030, issue
    #741). The text is resolved here, by this renderer, from the checked-in registry; it is never
    read from the wire. ``continuation`` is the stable key a consumer should branch on; the prose
    fields are advisory and may be reworded in any release without a schema change.
    """

    body: dict[str, JsonValue] = {
        "continuation": directive.token,
        "directive": directive.directive,
    }
    if commands:
        body["commands"] = list(commands)
    if directive.guidance_uri is not None:
        body["guidance_uri"] = directive.guidance_uri
    if directive.nudge is not None:
        body["nudge"] = directive.nudge
    return body


def error_recovery_json(safe_details: object) -> dict[str, JsonValue] | None:
    """Return the ``recovery`` object for a public error's safe details, or None.

    The JSON twin of ``render_error_recovery_lines``: a claim-revision invariant and correction,
    then the continuation directive with its carried commands, pointer, and nudge.
    """

    revision, directive, commands = _resolve_error_recovery(safe_details)
    body: dict[str, JsonValue] = {}
    if revision is not None:
        body["invariant"] = revision[0]
        body["correction"] = revision[1]
    if directive is not None:
        body.update(recovery_directive_json(directive, commands=commands))
    return body or None


def local_recovery_json(reason: object) -> dict[str, JsonValue] | None:
    """Return the ``recovery`` object for a CLI lifecycle, instance, or ceremony reason, or None.

    The JSON twin of ``render_local_recovery_lines``; the lookup is the local-reason vocabulary
    only, so a protocol reason code resolves to nothing through it.
    """

    directive = directive_for(continuation_for_local_reason(reason))
    if directive is None:
        return None
    return recovery_directive_json(directive)


def render_human_error(error: PublicErrorModel) -> str:
    """Render the bounded public error fields plus any typed recovery directive."""

    if type(error) is not PublicErrorModel:
        raise TypeError("public_error_invalid")
    suffix = " (retryable)" if error.retryable else ""
    head = f"{_token(error.code)}: {error.message}{suffix}"
    return "\n".join([head, *render_error_recovery_lines(error.safe_details)])


def render_human_awaiting_human(result: CheckAwaitingHumanModel) -> str:
    """Render the nonterminal check branch as an instruction, never as a conclusion.

    No verdict or coverage appears here because none exists yet. Printing this like an ordinary
    check result is how a suspended check gets mistaken for a completed one.
    """

    if type(result) is not CheckAwaitingHumanModel:
        raise TypeError("check_result_invalid")
    continuation = result.continuation
    lines = [
        "AI-powered review: awaiting_human (human_approval_required)",
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
