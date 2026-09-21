"""Bounded historical loss identity, independent of any later host envelope."""

from __future__ import annotations

from dataclasses import dataclass

from yoetz.domain.observation import ObservationSource, observation_selection_route
from yoetz.domain.values import JsonObject, validate_commitment, validate_sha256_digest
from yoetz.protocol.canonical import canonical_digest


@dataclass(frozen=True)
class ObservationSelectionLoss:
    lane: str
    source: ObservationSource
    session_commitment: str
    source_generation: int
    task_id: str
    session_id: str
    writer_id: str
    authority_generation: str

    @classmethod
    def from_local_range(cls, value: JsonObject) -> ObservationSelectionLoss:
        lane = value.get("lane")
        source = value.get("source")
        session = value.get("session")
        generation = value.get("source_generation")
        route = observation_selection_route(value.get("route"))
        if (
            type(lane) is not str
            or type(source) is not str
            or type(session) is not str
            or type(generation) is not int
            or generation <= 0
            or route is None
        ):
            raise ValueError("selection_loss_invalid")
        validate_sha256_digest(lane)
        validate_commitment(session)
        if lane != canonical_digest(
            JsonObject(
                {
                    "source": source,
                    "session": session,
                    "generation": generation,
                    "route": value["route"],
                }
            )
        ):
            raise ValueError("selection_loss_invalid")
        return cls(lane, ObservationSource(source), session, generation, *route)

    def identity(self) -> JsonObject:
        return JsonObject(
            {
                "lane": self.lane,
                "source": self.source.value,
                "session_commitment": self.session_commitment,
                "source_generation": self.source_generation,
                "task_id": self.task_id,
                "session_id": self.session_id,
                "writer_id": self.writer_id,
                "authority_generation": self.authority_generation,
            }
        )
