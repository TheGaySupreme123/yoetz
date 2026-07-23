"""Optional additive semantic advice over minimized observation evidence packets."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final, cast

from yoetz.application.observation_advice import (
    ObservationAdviceSemanticAddon,
    SemanticAdvicePort,
    minimized_semantic_evidence_packet,
    stable_advice_finding_id,
)
from yoetz.kernel.policies.observation_advice import ObservationAdviceCandidate
from yoetz.protocol.canonical import JsonValue, canonical_digest

__all__ = [
    "NullSemanticAdvice",
    "OptionalSemanticAdvice",
    "PrivacyGatedSemanticAdvice",
]

_SEMANTIC_RULE: Final = "semantic_additive_review"


@dataclass(frozen=True, slots=True)
class NullSemanticAdvice:
    """Deterministic-only path: semantic review is never invoked."""

    def review(
        self, *, evidence_packet: Mapping[str, object]
    ) -> ObservationAdviceSemanticAddon | None:
        _ = evidence_packet
        return None


class OptionalSemanticAdvice:
    """Invoke a configured evaluator only when ready; never required for correctness."""

    def __init__(
        self,
        *,
        configured: bool,
        ready: bool,
        evaluator: Callable[[Mapping[str, object]], Mapping[str, object] | None] | None = None,
    ) -> None:
        self._configured = configured
        self._ready = ready
        self._evaluator = evaluator

    def review(
        self, *, evidence_packet: Mapping[str, object]
    ) -> ObservationAdviceSemanticAddon | None:
        if not self._configured or not self._ready or self._evaluator is None:
            return None
        # Packet must already be minimized — reject ambient transcript/path keys.
        forbidden = {
            "transcript",
            "stdout",
            "stderr",
            "path",
            "cwd",
            "command",
            "raw_text",
            "reasoning",
        }
        if any(key in evidence_packet for key in forbidden):
            return None
        raw = self._evaluator(evidence_packet)
        if raw is None:
            return None
        detail = str(raw.get("detail_token") or "semantic-addon")
        digest = canonical_digest(
            cast(JsonValue, {"packet": dict(evidence_packet), "detail": detail})
        )
        finding = stable_advice_finding_id(_SEMANTIC_RULE, detail, digest)
        next_action = raw.get("next_action")
        return ObservationAdviceSemanticAddon(
            finding_ids=(finding,),
            evidence_digest=digest,
            next_action=str(next_action) if type(next_action) is str else None,
        )


@dataclass(frozen=True, slots=True)
class PrivacyGatedSemanticAdvice:
    """Wrap a SemanticAdvicePort and only forward minimized approved packets."""

    inner: SemanticAdvicePort
    candidates: tuple[ObservationAdviceCandidate, ...]
    basis_digest: str

    def review(
        self, *, evidence_packet: Mapping[str, object] | None = None
    ) -> ObservationAdviceSemanticAddon | None:
        packet = evidence_packet or minimized_semantic_evidence_packet(
            self.candidates, self.basis_digest
        )
        return self.inner.review(evidence_packet=packet)
