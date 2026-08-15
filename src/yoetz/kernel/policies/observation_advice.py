"""Deterministic observation-advice policy pack over live observation envelopes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from yoetz.domain.findings import FINDING_KIND_TRAITS, FindingKind
from yoetz.domain.observation import ObservationEnvelope, ObservationGapCode, ObservationLifecycle
from yoetz.protocol.canonical import JsonValue, canonical_digest

__all__ = [
    "STANDING_MACHINE_ACTIONS",
    "OBSERVATION_ADVICE_FACT_CODES",
    "OBSERVATION_ADVICE_POLICY_ID",
    "OBSERVATION_ADVICE_POLICY_VERSION",
    "ObservationAdviceCandidate",
    "ObservationAdviceContext",
    "ObservationCheckFact",
    "ObservationCompositionFact",
    "ObservationInspectFact",
    "advice_candidate_digest",
    "evidence_basis_digest",
    "observation_advice_findings",
]

OBSERVATION_ADVICE_POLICY_ID: Final = "observation-advice"
OBSERVATION_ADVICE_POLICY_VERSION: Final = "0.1.2"

OBSERVATION_ADVICE_FACT_CODES: Final = frozenset(
    {
        "failed_command_unresolved",
        "edit_after_successful_check",
        "completion_without_verification",
        "static_test_for_live_claim",
        "subagent_finding_unaddressed",
        "change_outside_plan",
        "observation_gap_or_stale",
        "provider_not_ready",
        "semantic_claim_without_attempt",
    }
)

type AdviceNextAction = Literal[
    "resolve_failed_command",
    "rerun_approved_check",
    "provide_verification",
    "disclose_limitation",
    "address_subagent_finding",
    "revise_plan_scope",
    "refresh_observation",
    "connect_provider",
    "attempt_semantic_dispatch",
    "reground_status",
]

# Next-action tokens naming conditions of the machine/installation, not of the
# work: the agent cannot discharge them, so evidence-sensitivity is the wrong
# redelivery trigger and they get a bounded cadence instead (#241).
# refresh_observation is deliberately excluded: it reflects live coverage
# degradation the agent should hear about once per distinct occurrence.
STANDING_MACHINE_ACTIONS: Final = frozenset({"connect_provider"})

_EDIT_TOOLS: Final = frozenset(
    {
        "apply_patch",
        "ApplyPatch",
        "write_file",
        "edit_file",
        "MultiEdit",
        "Write",
        "Edit",
        "shell_write",
    }
)
_CHECK_TOOLS: Final = frozenset(
    {
        "shell",
        "Bash",
        "test",
        "pytest",
        "cargo_test",
        "npm_test",
        "uv_run_pytest",
    }
)
_STATIC_CHECK_HINTS: Final = frozenset(
    {
        "pytest",
        "unittest",
        "static",
        "typecheck",
        "pyright",
        "mypy",
        "ruff",
    }
)
_LIVE_CLAIM_HINTS: Final = frozenset(
    {
        "live",
        "wire",
        "network",
        "http",
        "mcp",
        "socket",
        "dispatch",
        "provider",
    }
)


@dataclass(frozen=True, slots=True)
class ObservationCheckFact:
    approval_commitment: str
    subject_state_digest: str
    status: str
    cursor_event_position: int
    is_current: bool = True

    def __post_init__(self) -> None:
        if (
            type(self.approval_commitment) is not str
            or not self.approval_commitment.startswith("sha256:")
            or type(self.subject_state_digest) is not str
            or not self.subject_state_digest.startswith("sha256:")
            or type(self.status) is not str
            or not self.status
            or type(self.cursor_event_position) is not int
            or self.cursor_event_position < 0
            or type(self.is_current) is not bool
        ):
            raise ValueError("observation_advice_invalid")


@dataclass(frozen=True, slots=True)
class ObservationInspectFact:
    selection_digest: str
    relative_paths: tuple[str, ...]
    changed_paths_digest: str | None = None


@dataclass(frozen=True, slots=True)
class ObservationCompositionFact:
    # semantic_ready is the structural usability of the configured semantic
    # path — endpoint bound, factory available, configured credential present.
    # It claims nothing about repository-scoped dispatch authority, which is
    # resolved per check (#265).
    semantic_configured: bool
    semantic_ready: bool
    provider_factory_ids: tuple[str, ...]
    connected_provider_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ObservationAdviceContext:
    envelopes: tuple[ObservationEnvelope, ...]
    lifecycle: ObservationLifecycle
    gaps: tuple[str, ...]
    check_facts: tuple[ObservationCheckFact, ...] = ()
    inspect_fact: ObservationInspectFact | None = None
    composition: ObservationCompositionFact | None = None
    plan_path_digests: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ObservationAdviceCandidate:
    kind: FindingKind
    rule_code: str
    next_action: AdviceNextAction
    evidence_refs: tuple[str, ...]
    priority: int
    detail_token: str

    def __post_init__(self) -> None:
        if type(self.kind) is not FindingKind:
            raise ValueError("observation_advice_invalid")
        if self.rule_code not in OBSERVATION_ADVICE_FACT_CODES:
            raise ValueError("observation_advice_invalid")
        expected, _ = FINDING_KIND_TRAITS[self.kind]
        if self.priority != expected:
            raise ValueError("observation_advice_invalid")


# Structural payload fields the rules below read, named once so a rule and its
# accessor cannot drift apart.
_FIELD_TOOL_NAME: Final = "tool_name"
_FIELD_EXIT_STATUS: Final = "exit_status"
_FIELD_SUCCESS: Final = "success"
_FIELD_CLAIM_KIND: Final = "claim_kind"
_FIELD_RESULT_STATUS: Final = "result_status"
_FIELD_CHANGED_PATHS_DIGEST: Final = "changed_paths_digest"
_FIELD_MAPPING_HINT: Final = "mapping_hint"
_FIELD_SUBAGENT_ID: Final = "subagent_id"
_FIELD_ACTION: Final = "action"
_FIELD_ATTEMPT: Final = "attempt"
# Read only as correlation keys.
_FIELD_CORRELATION_ID: Final = "correlation_id"
_FIELD_TOOL_CALL_ID: Final = "tool_call_id"


def _ascii(value: str) -> bytes:
    return value.encode("ascii", errors="strict")


def _tool(envelope: ObservationEnvelope) -> str | None:
    value = envelope.structural_payload.get(_FIELD_TOOL_NAME)
    return value if type(value) is str else None


def _exit_status(envelope: ObservationEnvelope) -> int | None:
    value = envelope.structural_payload.get(_FIELD_EXIT_STATUS)
    return value if type(value) is int else None


def _success(envelope: ObservationEnvelope) -> bool | None:
    value = envelope.structural_payload.get(_FIELD_SUCCESS)
    return value if type(value) is bool else None


def _claim_kind(envelope: ObservationEnvelope) -> str | None:
    value = envelope.structural_payload.get(_FIELD_CLAIM_KIND)
    return value if type(value) is str else None


def _result_status(envelope: ObservationEnvelope) -> str | None:
    value = envelope.structural_payload.get(_FIELD_RESULT_STATUS)
    return value if type(value) is str else None


def _changed_paths_digest(envelope: ObservationEnvelope) -> str | None:
    value = envelope.structural_payload.get(_FIELD_CHANGED_PATHS_DIGEST)
    return value if type(value) is str else None


def _mapping_hint(envelope: ObservationEnvelope) -> str | None:
    value = envelope.structural_payload.get(_FIELD_MAPPING_HINT)
    return value if type(value) is str else None


def _subagent_id(envelope: ObservationEnvelope) -> str | None:
    value = envelope.structural_payload.get(_FIELD_SUBAGENT_ID)
    return value if type(value) is str else None


def _envelope_ref(envelope: ObservationEnvelope) -> str:
    return envelope.source_identity


def _candidate(
    kind: FindingKind,
    rule_code: str,
    next_action: AdviceNextAction,
    refs: Sequence[str],
    detail_token: str,
) -> ObservationAdviceCandidate:
    priority, _ = FINDING_KIND_TRAITS[kind]
    ordered = tuple(sorted(set(refs), key=_ascii))
    return ObservationAdviceCandidate(
        kind=kind,
        rule_code=rule_code,
        next_action=next_action,
        evidence_refs=ordered,
        priority=priority,
        detail_token=detail_token,
    )


def _failed_commands(envelopes: Sequence[ObservationEnvelope]) -> list[ObservationAdviceCandidate]:
    results: list[ObservationAdviceCandidate] = []
    unresolved: dict[str, ObservationEnvelope] = {}
    shell_tools = _CHECK_TOOLS | {"shell", "Bash", "bash"}
    for envelope in envelopes:
        tool = _tool(envelope)
        if tool is None or tool not in shell_tools:
            continue
        key = (
            envelope.structural_payload.get(_FIELD_CORRELATION_ID)
            or envelope.structural_payload.get(_FIELD_TOOL_CALL_ID)
            or envelope.source_identity
        )
        if type(key) is not str:
            continue
        if envelope.event_kind in {"PreToolUse"}:
            continue
        exit_status = _exit_status(envelope)
        success = _success(envelope)
        failed = (exit_status is not None and exit_status != 0) or success is False
        if failed:
            unresolved[key] = envelope
        elif exit_status == 0 or success is True:
            unresolved.pop(key, None)
    for key, envelope in unresolved.items():
        cause_digest = canonical_digest(
            {
                "correlation_key": key,
                "source_identity": envelope.source_identity,
            }
        )
        results.append(
            _candidate(
                FindingKind.FAILED_WORK_OMITTED,
                "failed_command_unresolved",
                "resolve_failed_command",
                (_envelope_ref(envelope),),
                f"failed:{cause_digest.removeprefix('sha256:')}",
            )
        )
    return results


def _edits_after_check(
    envelopes: Sequence[ObservationEnvelope],
    checks: Sequence[ObservationCheckFact],
) -> list[ObservationAdviceCandidate]:
    last_success_pos: int | None = None
    for envelope in envelopes:
        tool = _tool(envelope)
        exit_status = _exit_status(envelope)
        if (
            tool is not None
            and tool in _CHECK_TOOLS | {"shell", "Bash", "bash"}
            and (exit_status == 0 or _success(envelope) is True)
        ):
            last_success_pos = envelope.cursor.event_position
    for check in checks:
        if check.status == "passed" and check.is_current:
            last_success_pos = max(last_success_pos or 0, check.cursor_event_position)
    if last_success_pos is None:
        return []
    results: list[ObservationAdviceCandidate] = []
    for envelope in envelopes:
        tool = _tool(envelope)
        if tool is None or tool not in _EDIT_TOOLS:
            # Also treat apply_patch action writes.
            action = envelope.structural_payload.get(_FIELD_ACTION)
            if (
                action not in {"write", "edit", "delete"}
                and _changed_paths_digest(envelope) is None
            ):
                continue
        if envelope.cursor.event_position > last_success_pos:
            results.append(
                _candidate(
                    FindingKind.STALE_EVIDENCE_FOR_CHANGED_STATE,
                    "edit_after_successful_check",
                    "rerun_approved_check",
                    (_envelope_ref(envelope),),
                    f"edit-after-check:{envelope.cursor.event_position}",
                )
            )
    return results


def _completion_without_verification(
    envelopes: Sequence[ObservationEnvelope],
    checks: Sequence[ObservationCheckFact],
) -> list[ObservationAdviceCandidate]:
    completion_refs: list[str] = []
    for envelope in envelopes:
        claim = _claim_kind(envelope)
        result = _result_status(envelope)
        if claim in {"completion", "done", "finished"} or result in {"completed", "done"}:
            completion_refs.append(_envelope_ref(envelope))
    if not completion_refs:
        return []
    has_pass = any(check.status == "passed" and check.is_current for check in checks)
    has_obs_pass = any(
        (_exit_status(envelope) == 0 or _success(envelope) is True)
        and (_tool(envelope) in _CHECK_TOOLS | {"shell", "Bash", "bash"})
        for envelope in envelopes
    )
    if has_pass or has_obs_pass:
        return []
    return [
        _candidate(
            FindingKind.CLAIM_WITHOUT_ADMISSIBLE_EVIDENCE,
            "completion_without_verification",
            "provide_verification",
            completion_refs,
            "completion-unverified",
        )
    ]


def _static_for_live(envelopes: Sequence[ObservationEnvelope]) -> list[ObservationAdviceCandidate]:
    live_claims: list[str] = []
    static_support: list[str] = []
    for envelope in envelopes:
        claim = (_claim_kind(envelope) or "").lower()
        hint = (_mapping_hint(envelope) or "").lower()
        tool = (_tool(envelope) or "").lower()
        blob = f"{claim}:{hint}:{tool}"
        if any(token in blob for token in _LIVE_CLAIM_HINTS):
            live_claims.append(_envelope_ref(envelope))
        if any(token in blob for token in _STATIC_CHECK_HINTS) and (
            _exit_status(envelope) == 0 or _success(envelope) is True
        ):
            static_support.append(_envelope_ref(envelope))
    if (
        live_claims
        and static_support
        and not any(
            "live" in ((_mapping_hint(envelope) or "").lower())
            and (_exit_status(envelope) == 0 or _success(envelope) is True)
            for envelope in envelopes
            if _tool(envelope) in _CHECK_TOOLS | {"shell", "Bash", "bash"}
        )
    ):
        # Heuristic: live/wire claim present, only static verification observed.
        return [
            _candidate(
                FindingKind.EVIDENCE_DOES_NOT_SUPPORT_CLAIM,
                "static_test_for_live_claim",
                "disclose_limitation",
                live_claims + static_support,
                "static-for-live",
            )
        ]
    return []


def _subagent_unaddressed(
    envelopes: Sequence[ObservationEnvelope],
) -> list[ObservationAdviceCandidate]:
    findings: dict[str, str] = {}
    addressed: set[str] = set()
    for envelope in envelopes:
        sub = _subagent_id(envelope)
        if envelope.event_kind == "SubagentStop" and sub is not None:
            if (
                _result_status(envelope) in {"finding", "failed", "issue"}
                or _success(envelope) is False
            ):
                findings[sub] = _envelope_ref(envelope)
        if sub is not None and envelope.event_kind in {"PostToolUse", "UserPromptSubmit"}:
            if _result_status(envelope) in {"resolved", "addressed", "fixed"}:
                addressed.add(sub)
        # Parent claim acknowledging the subagent id also counts.
        claim = _claim_kind(envelope)
        if claim is not None and sub is not None and claim in {"resolved", "addressed"}:
            addressed.add(sub)
    results: list[ObservationAdviceCandidate] = []
    for sub, ref in findings.items():
        if sub not in addressed:
            results.append(
                _candidate(
                    FindingKind.FAILED_WORK_OMITTED,
                    "subagent_finding_unaddressed",
                    "address_subagent_finding",
                    (ref,),
                    f"subagent:{sub[:48]}",
                )
            )
    return results


def _outside_plan(
    envelopes: Sequence[ObservationEnvelope],
    inspect: ObservationInspectFact | None,
    plan_path_digests: Sequence[str],
) -> list[ObservationAdviceCandidate]:
    if not plan_path_digests:
        return []
    plan_set = set(plan_path_digests)
    change_refs: list[str] = []
    for envelope in envelopes:
        digest = _changed_paths_digest(envelope)
        if digest is not None and digest not in plan_set:
            change_refs.append(_envelope_ref(envelope))
    if inspect is not None and inspect.changed_paths_digest is not None:
        if inspect.changed_paths_digest not in plan_set:
            change_refs.append(inspect.selection_digest)
    if not change_refs:
        return []
    return [
        _candidate(
            FindingKind.DIFF_DOES_NOT_MATCH_ACCOUNT,
            "change_outside_plan",
            "revise_plan_scope",
            change_refs,
            "outside-plan",
        )
    ]


def _observation_gaps(
    lifecycle: ObservationLifecycle,
    gaps: Sequence[str],
    envelopes: Sequence[ObservationEnvelope],
) -> list[ObservationAdviceCandidate]:
    interesting = {
        ObservationGapCode.SOURCE_LAG.value,
        ObservationGapCode.CURSOR_STALE.value,
        ObservationGapCode.SERVICE_UNAVAILABLE.value,
        ObservationGapCode.VAULT_LOCKED.value,
        ObservationGapCode.UNPAIRED_EVENT.value,
        ObservationGapCode.UNSUPPORTED_EVENT.value,
    }
    present = [gap for gap in gaps if gap in interesting]
    if lifecycle in {ObservationLifecycle.STALE, ObservationLifecycle.DEGRADED} or present:
        refs = [_envelope_ref(item) for item in envelopes[-3:]] or ("observation:gap",)
        return [
            _candidate(
                FindingKind.LEDGER_STALE_OR_INCOMPLETE,
                "observation_gap_or_stale",
                "refresh_observation",
                refs,
                "observation-gap",
            )
        ]
    if not envelopes and lifecycle is not ObservationLifecycle.ACTIVE:
        return [
            _candidate(
                FindingKind.LEDGER_STALE_OR_INCOMPLETE,
                "observation_gap_or_stale",
                "refresh_observation",
                ("observation:empty",),
                "observation-empty",
            )
        ]
    return []


def _provider_not_ready(
    composition: ObservationCompositionFact | None,
) -> list[ObservationAdviceCandidate]:
    # connect_provider is a standing machine action, so it must rest on facts
    # that establish the operator has something to connect: semantic wanted
    # but the configured path is structurally unusable. Absence from the
    # connected registry is not such a fact — activation is lazy and
    # repository-scoped, re-established automatically on dispatch (#265) —
    # so registry lag only names evidence, it never triggers the advice.
    if composition is None:
        return []
    if not composition.semantic_configured or composition.semantic_ready:
        return []
    configured = set(composition.provider_factory_ids)
    connected = set(composition.connected_provider_ids)
    missing = configured - connected
    return [
        _candidate(
            FindingKind.MATERIAL_LIMITATION_OMITTED,
            "provider_not_ready",
            "connect_provider",
            tuple(sorted(missing or {"semantic:not_ready"}, key=_ascii)),
            "provider-not-ready",
        )
    ]


def _semantic_without_attempt(
    envelopes: Sequence[ObservationEnvelope],
) -> list[ObservationAdviceCandidate]:
    claims: list[str] = []
    attempts: list[str] = []
    for envelope in envelopes:
        claim = (_claim_kind(envelope) or "").lower()
        hint = (_mapping_hint(envelope) or "").lower()
        if "semantic" in claim or "live-dispatch" in claim or "live_dispatch" in claim:
            claims.append(_envelope_ref(envelope))
        if "semantic" in hint or envelope.structural_payload.get(_FIELD_ATTEMPT) is not None:
            if "semantic" in hint or claim in {"semantic_attempt", "dispatch_attempt"}:
                attempts.append(_envelope_ref(envelope))
        action = envelope.structural_payload.get(_FIELD_ACTION)
        if type(action) is str and action in {"semantic_dispatch", "live_dispatch"}:
            attempts.append(_envelope_ref(envelope))
    if claims and not attempts:
        return [
            _candidate(
                FindingKind.REQUESTED_ITEM_NEVER_ATTEMPTED,
                "semantic_claim_without_attempt",
                "attempt_semantic_dispatch",
                claims,
                "semantic-unattempted",
            )
        ]
    return []


def observation_advice_findings(
    context: ObservationAdviceContext,
) -> tuple[ObservationAdviceCandidate, ...]:
    """Emit ranked observation-advice candidates from envelopes and optional facts."""

    if type(context) is not ObservationAdviceContext:
        raise ValueError("observation_advice_invalid")
    envelopes = context.envelopes
    collected: list[ObservationAdviceCandidate] = []
    collected.extend(_failed_commands(envelopes))
    collected.extend(_edits_after_check(envelopes, context.check_facts))
    collected.extend(_completion_without_verification(envelopes, context.check_facts))
    collected.extend(_static_for_live(envelopes))
    collected.extend(_subagent_unaddressed(envelopes))
    collected.extend(_outside_plan(envelopes, context.inspect_fact, context.plan_path_digests))
    collected.extend(_observation_gaps(context.lifecycle, context.gaps, envelopes))
    collected.extend(_provider_not_ready(context.composition))
    collected.extend(_semantic_without_attempt(envelopes))

    # Deduplicate by rule_code + detail_token; keep first occurrence.
    seen: set[tuple[str, str]] = set()
    unique: list[ObservationAdviceCandidate] = []
    for item in collected:
        key = (item.rule_code, item.detail_token)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)

    ordered = tuple(
        sorted(
            unique,
            key=lambda item: (
                item.priority,
                _ascii(item.rule_code),
                _ascii(item.detail_token),
            ),
        )
    )
    return ordered


def evidence_basis_digest(
    candidates: Sequence[ObservationAdviceCandidate],
    envelopes: Sequence[ObservationEnvelope],
    *,
    extra: Mapping[str, JsonValue] | None = None,
) -> str:
    """Digest the exact evidence frontier used for an advice snapshot."""

    payload: dict[str, JsonValue] = {
        "policy": f"{OBSERVATION_ADVICE_POLICY_ID}/{OBSERVATION_ADVICE_POLICY_VERSION}",
        "candidates": tuple(
            {
                "kind": item.kind.value,
                "rule_code": item.rule_code,
                "evidence_refs": item.evidence_refs,
                "detail_token": item.detail_token,
            }
            for item in candidates
        ),
        "envelope_identities": tuple(item.source_identity for item in envelopes),
    }
    if extra:
        payload["extra"] = dict(sorted(extra.items(), key=lambda pair: pair[0].encode("ascii")))
    return canonical_digest(payload)


def advice_candidate_digest(candidate: ObservationAdviceCandidate) -> str:
    """Return the stable identity of one advice condition.

    Evidence references prove the condition but do not identify it. Several
    rules intentionally retain rolling or accumulating reference windows, so
    including them here would mint a new finding for every routine envelope.
    ``detail_token`` carries the rule-specific cause when multiple independent
    instances of one rule must remain distinct.
    """

    if type(candidate) is not ObservationAdviceCandidate:
        raise ValueError("observation_advice_invalid")
    return canonical_digest(
        {
            "policy": f"{OBSERVATION_ADVICE_POLICY_ID}/{OBSERVATION_ADVICE_POLICY_VERSION}",
            "kind": candidate.kind.value,
            "rule_code": candidate.rule_code,
            "detail_token": candidate.detail_token,
        }
    )
