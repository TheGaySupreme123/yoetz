"""Plan an AI-powered review case below the channel ceiling (issue #907 Phase 1b).

Privacy policy 1.2.0 lets the Expanded preset carry up to 64 excerpts, so a long session can build
a case whose prepared packet is larger than the channel's ``max_bytes`` or estimated
``max_tokens``. Egress refuses such a packet whole. Before that happens, the case is rebuilt with a
smaller excerpt byte budget. That is a narrowing of the approved selection, so it needs no consent.
The dropped excerpts are disclosed as ``content_unselected``, and the egress ceiling still decides
every dispatch.

The reduction is a pure function of the case inputs, so a recovered review rebuilds the same case
and keeps its digest.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Final

from yoetz.adapters.privacy.local_enforcer import EGRESS_BYTES_PER_TOKEN_ESTIMATE
from yoetz.application.semantic_case import (
    SemanticCaseTooLarge,
    semantic_case_to_prepared_payload,
)
from yoetz.domain.privacy import EgressChannel, PrivacyPolicy, ReviewSelectionPolicy
from yoetz.ports.privacy import MAX_MINIMIZED_DISCLOSURE_BYTES
from yoetz.ports.semantic import SemanticCase

__all__ = [
    "CEILING_PLANNING_GAP",
    "MAX_CEILING_PLANNING_ROUNDS",
    "channel_prepared_limit",
    "plan_under_channel_ceiling",
    "with_ceiling_planning_gap",
]

# At most this many rebuilds; a case still over the ceiling afterwards is left for egress to deny.
MAX_CEILING_PLANNING_ROUNDS: Final = 4
# Each later round aims this much further below the ceiling, so a round that lands just over it
# does not repeat the same budget.
_ROUND_MARGIN_BYTES: Final = 2_048
CEILING_PLANNING_GAP: Final = "content_unselected"


def channel_prepared_limit(policy: PrivacyPolicy) -> int | None:
    """The prepared-packet byte size the AI-powered review channel admits.

    It is the narrowest of ``max_bytes``, ``max_tokens`` at the estimate egress uses, and the
    largest disclosure the privacy port carries at all, so an unset (zero) or high Custom ceiling
    still plans below what egress can prepare. ``None`` only without an LLM channel.
    """

    llm = next(
        (
            channel
            for channel in policy.channel_policies
            if channel.channel is EgressChannel.LLM_INFERENCE
        ),
        None,
    )
    if llm is None:
        return None
    limits = [
        limit
        for limit in (llm.max_bytes, llm.max_tokens * EGRESS_BYTES_PER_TOKEN_ESTIMATE)
        if limit > 0
    ]
    return min([MAX_MINIMIZED_DISCLOSURE_BYTES, *limits])


def with_ceiling_planning_gap(gaps: Sequence[str]) -> tuple[str, ...]:
    """``gaps`` plus the disclosure a planned reduction adds, without duplicating it."""

    return tuple(sorted({*gaps, CEILING_PLANNING_GAP}, key=str.encode))


def _prepared_size(case: SemanticCase) -> int | None:
    try:
        return len(semantic_case_to_prepared_payload(case, {item.item_id for item in case.items}))
    except SemanticCaseTooLarge:
        return None


def _with_excerpt_budget(selection: ReviewSelectionPolicy, budget: int) -> ReviewSelectionPolicy:
    if budget < 1:
        return replace(
            selection,
            excerpt_kinds=(),
            max_excerpts=0,
            max_excerpt_bytes=0,
            max_total_excerpt_bytes=0,
        )
    return replace(
        selection,
        max_excerpt_bytes=min(selection.max_excerpt_bytes, budget),
        max_total_excerpt_bytes=min(selection.max_total_excerpt_bytes, budget),
    )


def plan_under_channel_ceiling(
    case: SemanticCase,
    selection: ReviewSelectionPolicy,
    limit: int | None,
    rebuild: Callable[[ReviewSelectionPolicy], SemanticCase],
) -> tuple[SemanticCase, ReviewSelectionPolicy, int]:
    """Return the case to review, the selection it was built with, and the rebuilds used.

    ``rebuild`` builds the same case under a narrower selection and adds the planning gap. The
    first rebuild carries no excerpts, which measures the fixed part of the packet. Later rebuilds
    size the excerpt budget from how many prepared bytes each excerpt byte actually cost, since
    JSON escaping can multiply it. A case whose envelope cannot be bounded at all, or that carries
    no excerpt to reduce, is returned unchanged, and egress decides it as before.
    """

    if limit is None:
        return case, selection, 0
    size = _prepared_size(case)
    excerpt_bytes = _excerpt_bytes(case)
    if size is None or size <= limit or excerpt_bytes == 0:
        return case, selection, 0
    approved = selection
    rounds = 1
    base = rebuild(_with_excerpt_budget(approved, 0))
    base_size = _prepared_size(base)
    if base_size is None or base_size >= limit:
        # Even without excerpts the packet is over the ceiling: egress refuses it as before.
        return base, _with_excerpt_budget(approved, 0), rounds
    planned, planned_selection = base, _with_excerpt_budget(approved, 0)
    while rounds < MAX_CEILING_PLANNING_ROUNDS and size > base_size and excerpt_bytes:
        rounds += 1
        room = limit - base_size - _ROUND_MARGIN_BYTES * (rounds - 1)
        budget = room * excerpt_bytes // (size - base_size)
        candidate_selection = _with_excerpt_budget(approved, budget)
        candidate = rebuild(candidate_selection)
        candidate_size = _prepared_size(candidate)
        if candidate_size is None:
            break
        if candidate_size <= limit:
            planned, planned_selection = candidate, candidate_selection
            break
        size, excerpt_bytes = candidate_size, _excerpt_bytes(candidate)
    return planned, planned_selection, rounds


def _excerpt_bytes(case: SemanticCase) -> int:
    return sum(item.content_bytes for item in case.items if item.section == "excerpt")
