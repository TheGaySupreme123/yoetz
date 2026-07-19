"""Trusted foreground privacy preview and decision helper."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from yoetz.cli.unlock import (
    HumanCeremonyCliError,
    _cancel_quietly,  # pyright: ignore[reportPrivateUsage]
    _drive_session,  # pyright: ignore[reportPrivateUsage]
    _ForegroundTerminal,  # pyright: ignore[reportPrivateUsage]
    _render_preview,  # pyright: ignore[reportPrivateUsage]
    _verify_preview,  # pyright: ignore[reportPrivateUsage]
)
from yoetz.service.confidential_client import HumanControlClient
from yoetz.service.confidential_protocol import (
    DecisionAction,
    HumanCeremonyKind,
    PrivacyDecisionResult,
    PrivacyDisclosureDecisionPreview,
    PrivacyPendingTarget,
    PrivacyPolicyDecisionPreview,
)

__all__ = [
    "PrivacyLocalEditResult",
    "decide_disclosure",
    "decide_policy",
]


@dataclass(frozen=True, slots=True)
class PrivacyLocalEditResult:
    outcome: Literal["edit"] = "edit"

    def __post_init__(self) -> None:
        if self.outcome != "edit":
            raise ValueError("privacy_local_edit_result_invalid")


async def _decide(
    decision_kind: Literal["policy", "disclosure"],
    pending_id: str,
) -> PrivacyDecisionResult | PrivacyLocalEditResult:
    target = PrivacyPendingTarget(decision_kind, pending_id)
    kind = (
        HumanCeremonyKind.PRIVACY_POLICY_DECISION
        if decision_kind == "policy"
        else HumanCeremonyKind.PRIVACY_DISCLOSURE_DECISION
    )
    client = HumanControlClient()
    with _ForegroundTerminal() as terminal:
        try:
            session = await client.open(kind, target)
            async with session:
                try:
                    preview = _verify_preview(kind, target, session)
                    if decision_kind == "policy":
                        if type(preview) is not PrivacyPolicyDecisionPreview:
                            raise HumanCeremonyCliError("preview_invalid")
                    elif (
                        type(preview) is not PrivacyDisclosureDecisionPreview
                        or preview.authorization_change != "none"
                    ):
                        raise HumanCeremonyCliError("preview_invalid")
                    _render_preview(terminal, preview)
                    selected = terminal.read_choice(
                        "Decision [approve/deny/edit]: ",
                        (b"approve", b"deny", b"edit"),
                    )
                    if selected == b"edit":
                        await session.cancel()
                        terminal.write(
                            "No decision was sent. Edit locally and create a new proposal.\n"
                        )
                        return PrivacyLocalEditResult()
                    decision: Literal["approve", "deny"] = (
                        "approve" if selected == b"approve" else "deny"
                    )
                    await session.send_action(DecisionAction(decision))
                    current = await session.wait_phase_or_result()
                    if decision_kind == "disclosure":
                        if type(current) is not PrivacyDecisionResult:
                            raise HumanCeremonyCliError("result_invalid")
                        return current
                    result, observed_decision = await _drive_session(
                        session,
                        terminal,
                        kind,
                        target,
                        current,
                    )
                    if observed_decision is not None:
                        raise HumanCeremonyCliError("result_invalid")
                    return cast(PrivacyDecisionResult, result)
                except BaseException:
                    await _cancel_quietly(session)
                    raise
        finally:
            await client.close()


async def decide_policy(
    pending_id: str,
) -> PrivacyDecisionResult | PrivacyLocalEditResult:
    """Decide one exact pending durable-policy proposal on the controlling TTY."""

    return await _decide("policy", pending_id)


async def decide_disclosure(
    pending_id: str,
) -> PrivacyDecisionResult | PrivacyLocalEditResult:
    """Decide one exact within-policy disclosure on the controlling TTY."""

    return await _decide("disclosure", pending_id)
