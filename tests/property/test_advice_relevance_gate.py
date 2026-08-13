"""Safety proof for the observation-advice relevance gate (#242-3b).

The gate lets a PostToolUse hook skip the advice recompute. Under-approximating
it means advice silently stops updating, so the gate does not ship without a
property proving the one direction that matters: whenever it answers "provably
irrelevant", the deterministic candidate set really is unchanged.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from hypothesis import given
from hypothesis import strategies as st

import yoetz.kernel.policies.observation_advice as policy
from yoetz.cli.observe_hooks import (
    _STRUCTURAL_ALLOW,  # pyright: ignore[reportPrivateUsage]
)
from yoetz.domain.observation import (
    ObservationCursor,
    ObservationEnvelope,
    ObservationLifecycle,
    ObservationSource,
)
from yoetz.domain.values import JsonObject, Timestamp
from yoetz.kernel.policies.observation_advice import (
    ADVICE_TRIGGER_FIELDS,
    ADVICE_TRIGGER_TOOLS,
    ObservationAdviceContext,
    advice_relevant,
    observation_advice_findings,
)

_COMMITMENT: Final = "hmac-sha256:" + "b" * 64
_TIME: Final = Timestamp("2026-08-13T14:00:00.000Z")
_EVENT_KINDS: Final = ("PostToolUse", "PreToolUse", "SubagentStop", "UserPromptSubmit", "Stop")
# Fields read only as correlation keys, inside a branch already gated on a
# trigger tool, so their presence alone can never change a verdict.
_KEY_ONLY_FIELDS: Final = frozenset({"correlation_id", "tool_call_id"})

_TOKENS: Final = tuple(
    sorted({*ADVICE_TRIGGER_TOOLS, "Read", "Grep", "pytest", "semantic", "live"})
)


def _payloads() -> st.SearchStrategy[dict[str, str | int | bool]]:
    """Structural payloads drawn from the exact allowlist a hook can persist."""

    scalars = st.one_of(
        st.sampled_from(_TOKENS),
        st.integers(min_value=0, max_value=8),
        st.booleans(),
    )
    quiet = sorted(_STRUCTURAL_ALLOW - ADVICE_TRIGGER_FIELDS)
    return st.one_of(
        st.dictionaries(
            keys=st.sampled_from(sorted(_STRUCTURAL_ALLOW)), values=scalars, max_size=6
        ),
        # Bias hard toward payloads the gate is expected to skip; otherwise most
        # examples exercise the uninteresting "relevant" direction.
        st.dictionaries(keys=st.sampled_from(quiet), values=scalars, max_size=6),
    )


def _envelope(
    payload: Mapping[str, str | int | bool], *, kind: str, position: int
) -> ObservationEnvelope:
    return ObservationEnvelope(
        session_commitment=_COMMITMENT,
        event_kind=kind,
        source_identity=f"hook:{position:04d}",
        source=ObservationSource.CODEX_HOOK,
        cursor=ObservationCursor(
            source_generation=1,
            byte_position=position * 8,
            event_position=position,
            last_source_commitment=_COMMITMENT,
            mapping_version="codex-obs-hook/1.0.0",
        ),
        receipt_time=_TIME,
        structural_payload=JsonObject({key: value for key, value in payload.items()}),
        content_object_refs=(),
        gap_codes=(),
    )


def _context(envelopes: tuple[ObservationEnvelope, ...]) -> ObservationAdviceContext:
    # No gaps and an ACTIVE lifecycle: the observation_gap_or_stale rule digests
    # the last three envelope identities, so while it fires *every* new envelope
    # moves the candidate set. That is why the caller-side guard in
    # observe_hooks additionally refuses to skip whenever gap-driven advice is
    # live, and why this property quantifies over quiet contexts.
    return ObservationAdviceContext(
        envelopes=envelopes,
        lifecycle=ObservationLifecycle.ACTIVE,
        gaps=(),
    )


@given(
    prior=st.lists(_payloads(), max_size=4),
    candidate=_payloads(),
    prior_kinds=st.lists(st.sampled_from(_EVENT_KINDS), max_size=4),
    candidate_kind=st.sampled_from(_EVENT_KINDS),
)
def test_gate_never_skips_an_envelope_that_changes_the_candidate_set(
    prior: list[dict[str, str | int | bool]],
    candidate: dict[str, str | int | bool],
    prior_kinds: list[str],
    candidate_kind: str,
) -> None:
    envelopes = tuple(
        _envelope(
            payload,
            kind=prior_kinds[index] if index < len(prior_kinds) else "PostToolUse",
            position=index + 1,
        )
        for index, payload in enumerate(prior)
    )
    extra = _envelope(candidate, kind=candidate_kind, position=len(envelopes) + 1)
    if advice_relevant(extra):
        return
    before = observation_advice_findings(_context(envelopes))
    after = observation_advice_findings(_context((*envelopes, extra)))
    assert before == after, (
        "the relevance gate declared an envelope irrelevant while it changed the "
        f"candidate set: {candidate}"
    )


def test_every_policy_read_field_appears_in_the_exported_trigger_set() -> None:
    """A future rule cannot silently read a field the gate does not know about."""

    source = Path(policy.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    read: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "get" or not node.args:
            continue
        target = node.func.value
        if not isinstance(target, ast.Attribute) or target.attr != "structural_payload":
            continue
        argument = node.args[0]
        if isinstance(argument, ast.Constant) and type(argument.value) is str:
            read.add(argument.value)
        elif isinstance(argument, ast.Name):
            resolved = getattr(policy, argument.id, None)
            assert type(resolved) is str, (
                f"structural_payload.get({argument.id}) does not resolve to a literal field name"
            )
            read.add(resolved)
        else:
            raise AssertionError("structural_payload.get with a non-literal field name")

    assert read, "found no structural payload reads; the scan is not seeing the policy rules"
    escaped = read - ADVICE_TRIGGER_FIELDS - _KEY_ONLY_FIELDS
    assert not escaped, (
        f"policy rules read {sorted(escaped)}, which the relevance gate does not "
        "treat as a trigger; add them to ADVICE_TRIGGER_FIELDS or the gate will "
        "skip envelopes that change advice"
    )
