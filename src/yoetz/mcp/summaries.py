"""Privacy-safe bounded text projections of structured operation results."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Final, cast

from pydantic import BaseModel

from yoetz.domain.findings import FINDING_KIND_TRAITS, FindingKind
from yoetz.domain.receipts import check_time_change_gap_sentence
from yoetz.domain.review_input_render import render_review_input_manifest_compact
from yoetz.mcp.errors import VALIDATION_REASON_TOKENS
from yoetz.protocol.canonical import JsonValue, ensure_canonical_value
from yoetz.protocol.errors import PublicErrorCode, normalize_safe_details
from yoetz.protocol.ids import IdKind, is_valid_id
from yoetz.protocol.readiness_text import READINESS_STATES, readiness_directive
from yoetz.protocol.recovery import (
    RecoveryDirective,
    continuation_for_semantic_outcome,
    correction_for_invariant,
    directive_for,
)

__all__ = [
    "render_safe_compact_summary",
    "summary_for_check",
    "summary_for_closure_prepare",
    "summary_for_public_error",
    "summary_for_read_guidance",
    "summary_for_receipt",
    "summary_for_status",
]

_MAX_SUMMARY_BYTES: Final = 512
# A validation location pointer as ``yoetz.mcp.errors`` builds it: at most eight frozen
# presentation-schema segments or bounded indexes. Re-gated here so a list member that is not that
# exact shape is never rendered, whatever put it on the envelope.
_VALIDATION_POINTER: Final = re.compile(
    r"^(?:/(?:[a-z][a-z0-9_]{0,63}|0|[1-9][0-9]?)){1,8}$", re.ASCII
)
_MAX_NAMED_VALIDATION_LOCATIONS: Final = 2
_SAFE_TOKEN: Final = re.compile(r"^[A-Za-z0-9_+.-]{1,128}$", re.ASCII)
_GAP_CODE: Final = re.compile(r"^[a-z][a-z0-9_]{0,127}$", re.ASCII)
_READINESS_ITEM: Final = re.compile(r"^(?:unclassified_gap:)?[a-z][a-z0-9_]{0,127}$", re.ASCII)
# Closed shape for the frozen field and family tokens the repair clause may carry (issue #266).
_FIELD_NAME: Final = re.compile(r"^[a-z][a-z0-9_]{0,63}$", re.ASCII)
_SAFE_COUNT: Final = re.compile(r"^(?:0|[1-9][0-9]{0,18})$", re.ASCII)
_CORRELATION_ID: Final = re.compile(
    r"^err_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.ASCII,
)
# Closed shape for the frontier head: either the genesis sentinel or a canonical digest.
_HEAD_DIGEST: Final = re.compile(r"^(?:genesis|sha256:[0-9a-f]{64})$", re.ASCII)
# Closed shape of a packaged guidance URI, the only location a guidance pointer may name.
_GUIDANCE_URI: Final = re.compile(r"^yoetz://guidance/[a-z0-9-]{1,64}\.md$", re.ASCII)


def _failure_class_from_mapping(value: object) -> object | None:
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value).get("failure_class")
    return None


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


_TODO_STATES: Final = ("open", "verified_resolved", "acknowledged_not_done", "rejection_accepted")
_CHECKLIST_NEXT: Final = frozenset({"decide_at_budget", "request_receipt", "work_open_findings"})
_BUDGET: Final = re.compile(r"^(?:[1-9]|[1-4][0-9]|50)$", re.ASCII)
_ROUNDS: Final = re.compile(r"^(?:0|[1-9][0-9]{0,17})$", re.ASCII)


def _actionable_kind(kind: object) -> bool:
    """Whether a row's kind is a to-do; unknown or malformed kinds never count."""

    if type(kind) is not str:
        return False
    try:
        return FINDING_KIND_TRAITS[FindingKind(kind)][1]
    except ValueError:
        return False


def _checklist_clause(rows: object, budget: object, next_step: object | None) -> str:
    """Count findings by to-do state with closed tokens only (issue #905).

    Rows are re-read from the structured result; anything that is not an allowlisted state,
    a canonical count or a canonical budget is ignored, so no caller text reaches the summary.
    Coverage-limitation kinds (and unknown kinds) are not to-dos and are not counted, matching the
    check's own checklist counts.
    """

    if not isinstance(rows, list | tuple):
        return ""
    counts = dict.fromkeys(_TODO_STATES, 0)
    at_budget = 0
    budget_value = int(budget) if type(budget) is str and _BUDGET.fullmatch(budget) else None
    for raw in cast(Sequence[JsonValue], rows):
        if not isinstance(raw, Mapping):
            continue
        row = cast(Mapping[str, JsonValue], raw)
        if not _actionable_kind(row.get("kind")):
            continue
        state = row.get("todo_state")
        if type(state) is not str or state not in counts:
            continue
        counts[state] += 1
        rounds = row.get("review_rounds")
        if (
            state == "open"
            and budget_value is not None
            and type(rounds) is str
            and _ROUNDS.fullmatch(rounds)
            and int(rounds) >= budget_value
        ):
            at_budget += 1
    if not any(counts.values()):
        return ""
    clause = (
        f"to-do: open {counts['open']}"
        + (f" ({at_budget} at budget {budget_value})" if at_budget else "")
        + f", verified {counts['verified_resolved']}"
        + f", not done {counts['acknowledged_not_done']}"
        + f", rejection accepted {counts['rejection_accepted']}; "
    )
    if type(next_step) is str and next_step in _CHECKLIST_NEXT:
        clause += f"next: {next_step}; "
    return clause


def _checklist_counts_clause(counts: object, budget: object, next_step: object) -> str:
    """The check's to-do counts, taken from its whole-list ``counts`` object (issue #905)."""

    if not isinstance(counts, Mapping):
        return ""
    source = cast(Mapping[str, JsonValue], counts)
    values: dict[str, int] = {}
    for key in (
        "open",
        "open_at_budget",
        "verified_resolved",
        "acknowledged_not_done",
        "rejection_accepted",
    ):
        raw = source.get(key)
        if type(raw) is not str or _ROUNDS.fullmatch(raw) is None:
            return ""
        values[key] = int(raw)
    budget_text = budget if type(budget) is str and _BUDGET.fullmatch(budget) else None
    clause = (
        f"to-do: open {values['open']}"
        + (
            f" ({values['open_at_budget']} at budget {budget_text})"
            if values["open_at_budget"] and budget_text is not None
            else ""
        )
        + f", verified {values['verified_resolved']}"
        + f", not done {values['acknowledged_not_done']}"
        + f", rejection accepted {values['rejection_accepted']}; "
    )
    if type(next_step) is str and next_step in _CHECKLIST_NEXT:
        clause += f"next: {next_step}; "
    return clause


# Room the fixed identity, frontier and recovery clauses still need after an optional clause.
_OPTIONAL_CLAUSE_RESERVE: Final = 240


def _with_room(prefix: str, clause: str) -> str:
    """Append an optional clause only while the fixed summary still fits beside it."""

    if len((prefix + clause).encode("ascii")) > _MAX_SUMMARY_BYTES - _OPTIONAL_CLAUSE_RESERVE:
        return prefix
    return prefix + clause


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

    return _bounded_list_clause("finding IDs: ", finding_ids, byte_budget=byte_budget)


_MISSING_KINDS: Final = frozenset(
    {
        "command_identity",
        "current_diff_for_path",
        "other",
        "plan_or_claim_text",
        "prior_finding_context",
        "task_statement",
        "verification_output",
    }
)
_MISSING_AVAILABILITIES: Final = frozenset(
    {"agent_suppliable", "structurally_unavailable_on_this_host"}
)


def _missing_items_clause(source: Mapping[str, JsonValue], *, byte_budget: int) -> str:
    """Name each item an ``insufficient_packet`` review needed, from closed tokens only (#907).

    Kinds and availability classes are re-gated against their closed vocabularies; target refs
    stay in the structured result. The clause is a check limitation, never a finding.
    """

    raw = source.get("missing_for_assessment")
    if not isinstance(raw, list | tuple) or byte_budget <= 0:
        return ""
    tokens: list[str] = []
    suppliable = 0
    for item in cast(Sequence[JsonValue], raw):
        if not isinstance(item, Mapping):
            continue
        kind = cast(Mapping[str, JsonValue], item).get("kind")
        availability = cast(Mapping[str, JsonValue], item).get("availability")
        if kind not in _MISSING_KINDS or availability not in _MISSING_AVAILABILITIES:
            continue
        suppliable += availability == "agent_suppliable"
        tokens.append(f"{kind}={availability}")
    if not tokens:
        return ""
    return _bounded_list_clause(
        f"missing for assessment: {len(tokens)} (agent-suppliable: {suppliable}): ",
        tokens,
        byte_budget=byte_budget,
    )


def _bounded_list_clause(prefix: str, values: Sequence[str], *, byte_budget: int) -> str:
    """Render a bounded structural-token list with an exact omitted-item count."""

    if not values or byte_budget <= 0:
        return ""
    selected: list[str] = []
    for value in values:
        selected.append(value)
        remaining = len(values) - len(selected)
        suffix = f"; +{remaining} more" if remaining else ""
        clause = f"{prefix}{', '.join(selected)}{suffix}; "
        if len(clause.encode("ascii")) > byte_budget:
            selected.pop()
            break
    if not selected:
        return ""
    remaining = len(values) - len(selected)
    suffix = f"; +{remaining} more" if remaining else ""
    return f"{prefix}{', '.join(selected)}{suffix}; "


def _obligation_ids_from_status(source: Mapping[str, JsonValue], view: str) -> tuple[str, ...]:
    page = source.get("page")
    if not isinstance(page, Mapping):
        return ()
    typed_page = cast(Mapping[str, JsonValue], page)
    raw_items: object = typed_page.get("items")
    if view == "compact":
        item = _first_page_item(source)
        raw_items = item.get("open_obligations") if item is not None else None
    if not isinstance(raw_items, list | tuple):
        return ()
    return tuple(
        cast(str, item.get("obligation_id"))
        for item in raw_items
        if isinstance(item, Mapping) and is_valid_id(IdKind.OBLIGATION, item.get("obligation_id"))
    )


def _finding_frontiers_from_status(source: Mapping[str, JsonValue]) -> tuple[str, ...]:
    """Pair each revalidated finding ID with the frontier ``respond`` accepts for it (#917).

    Only an allowlisted finding ID, a canonical sequence and a digest-shaped head leave this
    projector, so the text fallback can author ``respond`` without a frontier hunt.
    """

    page = source.get("page")
    if not isinstance(page, Mapping):
        return ()
    raw_items: object = cast(Mapping[str, JsonValue], page).get("items")
    if not isinstance(raw_items, list | tuple):
        return ()
    values: list[str] = []
    for raw in cast(Sequence[JsonValue], raw_items):
        if not isinstance(raw, Mapping):
            continue
        item = cast(Mapping[str, JsonValue], raw)
        finding = item.get("finding_id")
        frontier = item.get("finding_frontier")
        if not is_valid_id(IdKind.FINDING, finding) or not isinstance(frontier, Mapping):
            continue
        typed_frontier = cast(Mapping[str, JsonValue], frontier)
        sequence = _safe_count(typed_frontier.get("sequence"))
        head = typed_frontier.get("head_digest")
        if sequence == "unavailable" or type(head) is not str or not _HEAD_DIGEST.fullmatch(head):
            continue
        values.append(f"{cast(str, finding)} at {sequence} {head}")
    return tuple(values)


def _open_obligation_ids_from_receipt(source: Mapping[str, JsonValue]) -> tuple[str, ...]:
    raw_items = source.get("obligations")
    if not isinstance(raw_items, list | tuple):
        return ()
    return tuple(
        cast(str, item.get("obligation_id"))
        for item in raw_items
        if isinstance(item, Mapping)
        and item.get("status") == "open"
        and is_valid_id(IdKind.OBLIGATION, item.get("obligation_id"))
    )


def _safe_gap_codes(source: Mapping[str, JsonValue]) -> tuple[str, ...]:
    coverage = source.get("coverage")
    if not isinstance(coverage, Mapping):
        return ()
    raw_gaps = cast(Mapping[str, JsonValue], coverage).get("known_gaps")
    if not isinstance(raw_gaps, list | tuple):
        return ()
    return tuple(
        gap for gap in raw_gaps if type(gap) is str and _GAP_CODE.fullmatch(gap) is not None
    )


def _safe_status_gap_codes(source: Mapping[str, JsonValue]) -> tuple[str, ...]:
    raw_gaps = source.get("gaps")
    if not isinstance(raw_gaps, list | tuple):
        return ()
    return tuple(
        gap for gap in raw_gaps if type(gap) is str and _GAP_CODE.fullmatch(gap) is not None
    )


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


# Frozen command keys whose values are repository literals gated by ``_TOKEN_DETAIL_VALUES``.
# Rendered in this fixed order so the same continuation always reads the same way.
_CONTINUATION_COMMAND_KEYS: Final = ("prepare_command", "review_command", "authorize_command")


def _continuation_directive(error: Mapping[str, JsonValue]) -> RecoveryDirective | None:
    """Return the registered directive for this error's continuation token, or None."""

    details = error.get("safe_details")
    if not isinstance(details, Mapping):
        return None
    typed = cast(Mapping[str, JsonValue], details)
    # Re-gate through the protocol normalizer rather than trusting the envelope: the token must
    # still be a member of the closed continuation set before anything is rendered from it.
    gated = normalize_safe_details({"continuation": typed.get("continuation")})
    return directive_for(gated.get("continuation"))


def _continuation_command_clause(error: Mapping[str, JsonValue], *, byte_budget: int) -> str:
    """Render whichever frozen commands this continuation carries, within the byte budget.

    Which commands travel is decided upstream (a Cursor bridge carries no authorize command, for
    instance), so the clause reports what is present rather than restating the directive's own
    wording. Every value was admitted by the closed command token sets, so none is caller-derived.
    """

    details = error.get("safe_details")
    if not isinstance(details, Mapping) or byte_budget <= 0:
        return ""
    typed = cast(Mapping[str, JsonValue], details)
    candidate = {key: typed.get(key) for key in _CONTINUATION_COMMAND_KEYS}
    gated = normalize_safe_details(candidate)
    commands = [
        str(gated[key]) for key in _CONTINUATION_COMMAND_KEYS if type(gated.get(key)) is str
    ]
    if not commands:
        return ""
    clause = " Commands: " + "; ".join(commands) + "."
    if len(clause.encode("ascii", errors="replace")) > byte_budget:
        return ""
    return clause


def _continuation_clause(error: Mapping[str, JsonValue], *, byte_budget: int) -> str:
    """Render the frozen recovery directive for a typed continuation (issues #669, #739, #740).

    Nothing here is copied from the public error message. The token is re-gated, the text is
    looked up from the checked-in registry, and each optional part is added only when it still
    fits. Parts are dropped from the least load-bearing end -- nudge, then guidance pointer, then
    commands -- so a tight budget costs advice rather than the instruction itself.
    """

    directive = _continuation_directive(error)
    if directive is None or byte_budget <= 0:
        return ""
    clause = f" Continuation: {directive.token}. {directive.directive}"
    if len(clause.encode("ascii", errors="replace")) > byte_budget:
        return ""
    remaining = byte_budget - len(clause.encode("ascii", errors="replace"))
    commands = _continuation_command_clause(error, byte_budget=remaining)
    clause += commands
    remaining -= len(commands.encode("ascii", errors="replace"))
    if directive.guidance_uri is not None:
        guidance = f" Guidance: {directive.guidance_uri}."
        if len(guidance.encode("ascii", errors="replace")) <= remaining:
            clause += guidance
            remaining -= len(guidance.encode("ascii", errors="replace"))
    if directive.nudge is not None:
        nudge = f" {directive.nudge}"
        if len(nudge.encode("ascii", errors="replace")) <= remaining:
            clause += nudge
    return clause


def _reason_location_clause(error: Mapping[str, JsonValue]) -> str:
    """Render frozen reason_code and field pointer tokens, or "" when none travel on the error.

    Hosts that deliver only the text channel for ``isError`` results otherwise lose the draft
    index and kernel reason (issue #579). Both tokens are already allowlisted structural content
    on ``safe_details``; this projector re-gates them and never copies caller prose.
    """

    details = error.get("safe_details")
    if not isinstance(details, Mapping):
        return ""
    typed = cast(Mapping[str, JsonValue], details)
    candidate: dict[str, object] = {}
    reason = typed.get("reason_code")
    field = typed.get("field")
    if type(reason) is str:
        candidate["reason_code"] = reason
    if type(field) is str:
        candidate["field"] = field
    gated = normalize_safe_details(candidate)
    gated_reason = gated.get("reason_code")
    gated_field = gated.get("field")
    if type(gated_reason) is not str:
        return ""
    if type(gated_field) is str:
        return f" Reason: {gated_reason} at {gated_field}."
    return f" Reason: {gated_reason}."


def _validation_location_clause(error: Mapping[str, JsonValue]) -> str:
    """Render the frozen location tokens of a schema rejection, or "" when none travel.

    A tool-argument rejection carries ``fields`` and ``reasons`` lists rather than a single
    ``reason_code``/``field`` pair, and those lists are not members of the protocol allowlist, so
    the text channel previously dropped them entirely: an agent that sent a malformed actor id was
    told only ``Error INVALID_REQUEST; retryable: no`` (issue #739). Every rendered token is
    re-gated against the closed reason set and the pointer shape; the two lists must agree in
    length, and at most two locations are named with the remainder counted.
    """

    details = error.get("safe_details")
    if not isinstance(details, Mapping):
        return ""
    typed = cast(Mapping[str, JsonValue], details)
    fields = typed.get("fields")
    reasons = typed.get("reasons")
    if not isinstance(fields, Sequence) or not isinstance(reasons, Sequence):
        return ""
    if isinstance(fields, str) or isinstance(reasons, str) or len(fields) != len(reasons):
        return ""
    named: list[str] = []
    for field, reason in zip(
        cast(Sequence[JsonValue], fields), cast(Sequence[JsonValue], reasons), strict=True
    ):
        if type(reason) is not str or reason not in VALIDATION_REASON_TOKENS:
            return ""
        if type(field) is not str or _VALIDATION_POINTER.fullmatch(field) is None:
            return ""
        named.append(f"{reason} at {field}")
    if not named:
        return ""
    shown = "; ".join(named[:_MAX_NAMED_VALIDATION_LOCATIONS])
    remainder = len(named) - _MAX_NAMED_VALIDATION_LOCATIONS
    if remainder > 0:
        shown += f" (+{remainder} more)"
    return f" Rejected: {shown}."


def _claim_revision_clause(error: Mapping[str, JsonValue]) -> str:
    """Render the closed claim-revision invariant and its correction from typed details.

    Until ADR-030 the invariant reached this projector only inside the public error message, so
    this clause matched that whole sentence with a regex and re-derived a token the domain had
    already validated. ``invariant`` is now an allowlisted safe detail: it is re-gated through the
    protocol normalizer here, and the corrective phrase comes from the shared recovery registry
    that the CLI renders from too.
    """

    details = error.get("safe_details")
    if not isinstance(details, Mapping):
        return ""
    typed = cast(Mapping[str, JsonValue], details)
    gated = normalize_safe_details(
        {
            "invariant": typed.get("invariant"),
            "reason_code": typed.get("reason_code"),
        }
    )
    if gated.get("reason_code") != "claim_revision_mismatch":
        return ""
    correction = correction_for_invariant(gated.get("invariant"))
    if correction is None:
        return ""
    return f" Invariant: {gated['invariant']}. Correction: {correction}."


def summary_for_public_error(envelope: object) -> str:
    """Render only the stable public error identity, never message or rejected input.

    Bounded exceptions are the field-ownership repair fact (issue #266), the frozen
    ``reason_code``/``field`` location clause (issue #579), and the exact-shape claim-revision
    invariant clause. All projected values come from schema or registry tokens, never caller
    input. A host that drops structured content on ``isError`` would otherwise lose the only
    correction that names which draft and which kernel rule to fix.
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
    prefix = f"Error {code}; retryable: {retry_text}; correlation: {correlation_text}."
    extra = (
        f"{_repair_clause(error)}{_reason_location_clause(error)}"
        f"{_validation_location_clause(error)}{_claim_revision_clause(error)}"
    )
    # Priority order (issue #739): identity, then what was wrong, then what to do about it. The
    # continuation is budgeted against what the identity and location clauses already spent, so a
    # long field pointer costs advice rather than silently dropping the whole projection to bare
    # identity through the except branch below.
    spent = len((prefix + extra).encode("ascii", errors="replace"))
    extra += _continuation_clause(error, byte_budget=_MAX_SUMMARY_BYTES - spent)
    try:
        return _bounded(prefix + extra)
    except ValueError:
        return _bounded(prefix)


def summary_for_check(envelope: object) -> str:
    source = _mapping(envelope)
    verdict = _safe_token(source.get("verdict"))
    findings = _item_count(source.get("findings"))
    suppressed = _safe_count(source.get("suppressed_count"))
    status = _safe_token(source.get("semantic_status"))
    reason = _safe_token(source.get("semantic_reason"))
    if status == "not_requested":
        prefix = (
            f"AI-powered review not requested; local-only check verdict: {verdict}; "
            f"findings returned: {findings}; suppressed: {suppressed}; "
        )
    else:
        prefix = (
            f"Check verdict: {verdict}; findings returned: {findings}; suppressed: {suppressed}; "
        )
    children = source.get("children")
    if (
        isinstance(children, Mapping)
        and type(children.get("label")) is str
        and children.get("label") in {"recorded", "preview"}
    ):
        prefix += f"children ({children['label']}): {_item_count(children.get('items'))}; "
    notes = source.get("advisory_notes")
    if isinstance(notes, (list, tuple)) and notes:
        prefix += f"project advice (non-verdict): {len(notes)}; "
    checklist = source.get("finding_checklist")
    if isinstance(checklist, Mapping):
        checklist_source = cast(Mapping[str, JsonValue], checklist)
        prefix = _with_room(
            prefix,
            _checklist_counts_clause(
                checklist_source.get("counts"),
                checklist_source.get("attempt_budget"),
                checklist_source.get("next"),
            ),
        )
    manifest_clause = render_review_input_manifest_compact(source.get("review_input_manifest"))
    if manifest_clause:
        prefix = _with_room(prefix, manifest_clause + " ")
    suffix = (
        f"AI-powered review status/reason: {status}/{reason}; "
        f"{_frontier_clause(source)}."
    )
    recovery = continuation_for_semantic_outcome(
        status=status,
        reason=reason,
        failure_class=_failure_class_from_mapping(source.get("semantic_provenance")),
    )
    recovered = directive_for(recovery)
    if recovered is not None:
        extra = f" Continuation: {recovered.token}."
        reserved = 80
        used = len((prefix + suffix + extra).encode("ascii")) + reserved
        leftover = _MAX_SUMMARY_BYTES - used
        directive_text = recovered.directive
        if leftover > 8 and len(directive_text.encode("ascii")) + 1 <= leftover:
            extra += f" {directive_text}"
        suffix += extra
    # Why the check-time change was unavailable (ADR-031): a fixed sentence per closed code,
    # kept only when it fits the summary bound.
    for code in _safe_gap_codes(source):
        sentence = check_time_change_gap_sentence(code)
        if sentence is not None:
            extra = " " + sentence
            if len((prefix + suffix + extra).encode("ascii")) <= _MAX_SUMMARY_BYTES:
                suffix += extra
            break
    clause = _finding_identity_clause(
        source,
        byte_budget=_MAX_SUMMARY_BYTES - len((prefix + suffix).encode("ascii")),
    )
    clause += _missing_items_clause(
        source,
        byte_budget=_MAX_SUMMARY_BYTES - len((prefix + clause + suffix).encode("ascii")),
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
    if view in {"lineage", "project"}:
        return _summary_for_multi_agent_status(source, view)
    if view == "advice":
        page = source.get("page")
        count = _item_count(page.get("items")) if isinstance(page, Mapping) else "unavailable"
        text = (
            f"Status view: advice; {_frontier_clause(source)}; advice items: {count}; "
            "Read the structured page for coordination selectors and bounded resource details."
        )
        # Every view carries the closure checklist, so every view names its state (#913).
        return _bounded(
            text
            + _closure_clause(source, byte_budget=_MAX_SUMMARY_BYTES - len(text.encode("ascii")))
        )
    freshness, obligations, unanswered, receipt_blocking = _compact_status_fields(source, view)
    gaps = _item_count(source.get("gaps"))
    prefix = (
        f"Status view: {view}; {_frontier_clause(source)}; freshness: {freshness}; "
        f"open obligations: {obligations}; "
    )
    if view == "evidence":
        prefix += _evidence_channel_clause(source)
    if view == "operation":
        operation_clause = _operation_progress_clause(source)
        if len((prefix + operation_clause).encode("ascii")) > _MAX_SUMMARY_BYTES - 128:
            # Pathological counts cannot push the fixed suffix out of the bounded summary.
            operation_clause = "semantic progress: see structured page; "
        prefix += operation_clause
    if view == "findings":
        page = source.get("page")
        if isinstance(page, Mapping):
            page_source = cast(Mapping[str, JsonValue], page)
            prefix = _with_room(
                prefix,
                _checklist_clause(
                    page_source.get("items"), page_source.get("attempt_budget"), None
                ),
            )
    suffix = (
        f"unanswered findings: {unanswered}; "
        f"receipt-blocking findings: {receipt_blocking}; reported gaps: {gaps}."
    )
    suffix += _closure_clause(
        source, byte_budget=_MAX_SUMMARY_BYTES - len((prefix + suffix).encode("ascii"))
    )
    obligation_ids = _obligation_ids_from_status(source, view)
    clause = _bounded_list_clause(
        "obligation IDs: ",
        obligation_ids,
        byte_budget=_MAX_SUMMARY_BYTES - len((prefix + suffix).encode("ascii")),
    )
    if view == "findings":
        clause += _bounded_list_clause(
            "finding frontiers: ",
            _finding_frontiers_from_status(source),
            byte_budget=_MAX_SUMMARY_BYTES - len((prefix + clause + suffix).encode("ascii")),
        )
    return _bounded(prefix + clause + suffix)


_PUBLICATION_CHANNELS: Final = (
    "codex_jsonl_import",
    "cooperative_mcp",
    "engine_derived",
    "hook_observed",
    "human_import",
    "local_cli",
)


def _evidence_channel_clause(source: Mapping[str, JsonValue]) -> str:
    """Count this page's evidence rows per closed publication channel (issue #914).

    The channel is the only structural way to tell the requester's own cooperative evidence from
    host-observed captures when a host drops the structured page; no row prose is rendered.
    """

    page = source.get("page")
    items = cast(Mapping[str, JsonValue], page).get("items") if isinstance(page, Mapping) else None
    if not isinstance(items, list | tuple):
        return "evidence rows: unavailable; "
    rows = cast(Sequence[JsonValue], items)
    counts = {channel: 0 for channel in _PUBLICATION_CHANNELS}
    for row in rows:
        channel = (
            cast(Mapping[str, JsonValue], row).get("publication_channel")
            if isinstance(row, Mapping)
            else None
        )
        if type(channel) is str and channel in counts:
            counts[channel] += 1
    parts = [f"{channel} {count}" for channel, count in counts.items() if count]
    # Rows from earlier 0.3 builds carry no channel; an unknown value is caller text and is never
    # rendered. Both are counted, not named.
    unrecorded = len(rows) - sum(counts.values())
    if unrecorded:
        parts.append(f"unrecorded {unrecorded}")
    return f"evidence rows: {_item_count(rows)} ({', '.join(parts) or 'none'}); "


def _readiness_tokens(readiness: Mapping[str, JsonValue], key: str) -> tuple[str, ...]:
    raw = readiness.get(key)
    if not isinstance(raw, list | tuple):
        return ()
    values = cast(Sequence[object], raw)
    return tuple(
        value
        for value in values
        if type(value) is str and _READINESS_ITEM.fullmatch(value) is not None
    )


def _closure_clause(source: Mapping[str, JsonValue], *, byte_budget: int) -> str:
    """Name the closure-readiness state and its frozen directive (issue #913, ADR-032).

    Only the closed state token, service counts and classified gap or condition tokens appear.
    The first variant that fits the remaining budget wins. ``ready_with_limitations`` always keeps
    the owner-approved stop sentence whole and shrinks the named limitations first; for
    ``action_required`` the named items are the instruction, so they outrank the generic sentence.
    """

    readiness = source.get("closure_readiness")
    if not isinstance(readiness, Mapping):
        return ""
    typed = cast(Mapping[str, JsonValue], readiness)
    state = typed.get("state")
    if type(state) is not str or state not in READINESS_STATES:
        return ""
    standing = _readiness_tokens(typed, "standing_limitations")
    acknowledged = _safe_count(typed.get("acknowledged_not_done_count"))
    if acknowledged == "unavailable":
        return ""
    head = f" Closure: {state}."
    directive = (
        head + " " + readiness_directive(state, standing=len(standing), acknowledged=acknowledged)
    )

    def listed(base: str, label: str, values: tuple[str, ...]) -> str:
        budget = byte_budget - len(base.encode("ascii")) - 1
        clause = _bounded_list_clause(label, values, byte_budget=budget)
        return base + " " + clause.removesuffix("; ") + "." if clause else ""

    variants: list[str] = []
    if state == "action_required":
        actionable = _readiness_tokens(typed, "agent_actionable")
        complete = listed(directive, "Agent-actionable: ", actionable)
        if complete and "more." not in complete:
            variants.append(complete)
        variants.extend((listed(head, "Agent-actionable: ", actionable), directive, head))
    elif state == "ready_with_limitations":
        variants.extend(
            (
                listed(directive, "Standing limitations: ", standing),
                directive,
                head + " Nothing further to do. Request the receipt.",
                head,
            )
        )
    else:
        variants.extend((directive, head))
    for variant in variants:
        if variant and len(variant.encode("ascii")) <= byte_budget:
            return variant
    return ""


def _operation_progress_clause(source: Mapping[str, JsonValue]) -> str:
    """Name the operation state and structural review progress with allowlisted values only."""

    page = source.get("page")
    if not isinstance(page, Mapping):
        return "operation: unavailable; "
    typed = cast(Mapping[str, JsonValue], page)
    clause = (
        f"operation state: {_safe_token(typed.get('state'))}; "
        f"kind: {_safe_token(typed.get('operation_kind'), fallback='none')}; "
    )
    admission = typed.get("admission")
    if isinstance(admission, Mapping):
        # Issue #838: an absent page with an admission stage is a refused or in-flight admission,
        # not an unknown request; the exact replay after the named wait is the recovery.
        admitted = cast(Mapping[str, JsonValue], admission)
        return clause + (
            f"admission stage: {_safe_token(admitted.get('stage'))}; "
            f"refusals: {_safe_count(admitted.get('refusal_count'))}; "
            f"elapsed ms: {_safe_count(admitted.get('elapsed_ms'))}; "
            f"retry after ms: {_safe_count(admitted.get('retry_after_ms'))}; "
        )
    progress = typed.get("semantic_progress")
    if not isinstance(progress, Mapping):
        return clause + "semantic progress: none; "
    fields = cast(Mapping[str, JsonValue], progress)
    clause += (
        f"semantic phase: {_safe_token(fields.get('phase'))}; "
        f"attempt: {_safe_count(fields.get('attempt_ordinal'))}; "
        f"condition: {_safe_token(fields.get('condition'))}; "
        f"elapsed ms: {_safe_count(fields.get('elapsed_ms'))}; "
    )
    if fields.get("condition") == "terminal":
        return clause + (
            f"outcome: {_safe_token(fields.get('terminal_outcome'))} "
            f"({_safe_token(fields.get('terminal_reason'))}); "
        )
    return clause + f"remaining ms: {_safe_count(fields.get('remaining_ms'))}; "


def _summary_for_multi_agent_status(source: Mapping[str, JsonValue], view: str) -> str:
    page = source.get("page")
    prefix = f"Status view: {view}; {_frontier_clause(source)}; "
    if not isinstance(page, Mapping):
        text = prefix + "page unavailable."
        return _bounded(
            text
            + _closure_clause(source, byte_budget=_MAX_SUMMARY_BYTES - len(text.encode("ascii")))
        )
    lineage = page if view == "lineage" else page.get("lineage")
    if view == "project":
        project = page.get("project_id")
        project_id = project if is_valid_id(IdKind.PROJECT, project) else "unavailable"
        grant = page.get("grant_state")
        grant_state = (
            grant
            if type(grant) is str and grant in {"active", "revoked"}
            else "none"
            if grant is None
            else "unavailable"
        )
        prefix += (
            f"project: {project_id}; generation: {_safe_count(page.get('membership_generation'))}; "
            f"grant: {grant_state or 'none'}; members: {_item_count(page.get('members'))}; "
            f"detections: {_item_count(page.get('detections'))}; "
            f"receipts: {_item_count(page.get('receipts'))}; "
        )
    if isinstance(lineage, Mapping):
        parent = lineage.get("parent_task_id")
        if view == "lineage":
            parent_id = (
                parent
                if is_valid_id(IdKind.TASK, parent)
                else "none"
                if parent is None
                else "unavailable"
            )
            prefix += f"parent: {parent_id}; "
        prefix += (
            f"children: {_item_count(lineage.get('children'))}; "
            f"host annotations: {_item_count(lineage.get('annotations'))}; "
        )
    if view == "project":
        prefix += f"coverage: {_item_count(page.get('coverage'))}; "
    suffix = "Read the structured page for child states and row identities."
    if page.get("next_cursor") is not None:
        suffix = "More pages available. " + suffix
    # The parent's own closure checklist travels on these views too (#913).
    suffix += _closure_clause(
        source, byte_budget=_MAX_SUMMARY_BYTES - len((prefix + suffix).encode("ascii"))
    )
    gap_clause = _bounded_list_clause(
        "gap codes: ",
        _safe_status_gap_codes(source),
        byte_budget=_MAX_SUMMARY_BYTES - len((prefix + suffix).encode("ascii")),
    )
    return _bounded(prefix + gap_clause + suffix)


def summary_for_receipt(envelope: object) -> str:
    source = _mapping(envelope)
    conclusion = _safe_token(source.get("conclusion"))
    coverage = source.get("coverage")
    limitations = "unavailable"
    if isinstance(coverage, Mapping):
        typed_coverage = cast(Mapping[str, JsonValue], coverage)
        limitations = _item_count(typed_coverage.get("known_gaps"))
    suppressed = _safe_count(source.get("suppressed_finding_count"))
    prefix = (
        f"Receipt conclusion: {conclusion}; {_frontier_clause(source)}; "
        f"coverage limitations: {limitations}; "
    )
    suffix = f"suppressed findings: {suppressed}."
    remaining = _MAX_SUMMARY_BYTES - len((prefix + suffix).encode("ascii"))
    gap_codes = _safe_gap_codes(source)
    obligation_budget = remaining // 2 if gap_codes else remaining
    obligation_clause = _bounded_list_clause(
        "open obligation IDs: ",
        _open_obligation_ids_from_receipt(source),
        byte_budget=obligation_budget,
    )
    remaining -= len(obligation_clause.encode("ascii"))
    gap_clause = _bounded_list_clause("gap codes: ", gap_codes, byte_budget=remaining)
    return _bounded(prefix + obligation_clause + gap_clause + suffix)


def summary_for_read_guidance(envelope: object) -> str:
    """Name where a guidance result's full text is, without repeating it (issue #918).

    The URI is re-gated against the closed packaged-guidance shape and the byte count against the
    count shape, so the pointer carries only registry facts, never document text.
    """

    source = _mapping(envelope)
    uri = source.get("uri")
    if type(uri) is not str or _GUIDANCE_URI.fullmatch(uri) is None:
        uri = "unavailable"
    if source.get("complete") is not None:
        page = _safe_count(source.get("page"))
        page_count = _safe_count(source.get("page_count"))
        page_bytes = _safe_count(source.get("page_byte_count"))
        total_bytes = _safe_count(source.get("total_byte_count"))
        digest = source.get("digest")
        digest_text = (
            digest
            if type(digest) is str and re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
            else "unavailable"
        )
        complete = "yes" if source.get("complete") is True else "no"
        continuation = "yes" if isinstance(source.get("continuation"), Mapping) else "no"
        return _bounded(
            f"Guidance page {page}/{page_count} for {uri}: {page_bytes} bytes of {total_bytes}; "
            f"complete: {complete}; continuation: {continuation}; revision: {digest_text}; "
            "full page in structuredContent.text."
        )
    return _bounded(
        f"Guidance {uri}: {_safe_count(source.get('byte_count'))} bytes; "
        "full text in structuredContent.text."
    )


def summary_for_closure_prepare(envelope: object) -> str:
    """Project the bounded, preparatory-only closure result onto the text channel."""

    source = _mapping(envelope)
    inventory = source.get("inventory")
    counts: list[str] = []
    if isinstance(inventory, Mapping):
        typed_inventory = cast(Mapping[str, JsonValue], inventory)
        for view in ("obligations", "results", "evidence", "findings", "history"):
            counts.append(f"{view} {_item_count(typed_inventory.get(view))}")
    rows = ", ".join(counts) if counts else "unavailable"
    operation = _safe_token(source.get("operation"), fallback="inventory")
    return _bounded(
        f"Closure preparation only; {_frontier_clause(source)}; inventory rows: {rows}; "
        f"next operation: {operation}; review structuredContent before submitting any request."
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
    if source.get("preparatory_only") is True:
        return summary_for_closure_prepare(source)
    if "verdict" in source:
        return summary_for_check(source)
    if "view" in source:
        return summary_for_status(source)
    if "receipt_id" in source or "conclusion" in source:
        return summary_for_receipt(source)
    return _summary_for_other_success(source)
