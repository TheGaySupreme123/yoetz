"""Ordinary-control support handlers for ObservationPort methods."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import cast

from yoetz.domain.observation import (
    ObservationControlCommand,
    ObservationEnvelope,
    ObservationRevokeCommand,
    ObservationStatusQuery,
    observation_control_command_from_json,
    observation_envelope_from_json,
    observation_ingest_result_to_json,
    observation_revoke_command_from_json,
    observation_status_query_from_json,
    observation_status_to_json,
)
from yoetz.domain.values import JsonObject, JsonValue
from yoetz.ports.control import ControlError, ControlMethod
from yoetz.ports.observation import ObservationPort
from yoetz.protocol.errors import ProtocolValueError, PublicOperationError

__all__ = ["build_observation_support_handlers"]

type _SupportHandler = Callable[[object], Awaitable[JsonObject]]


def _as_json_object(request: object) -> JsonObject:
    if type(request) is JsonObject:
        return request
    if isinstance(request, Mapping):
        raw = dict(cast(Mapping[str, JsonValue], request))
        # Wire JSON arrays arrive as lists; domain parsers require tuples.
        for key in ("content_object_refs", "gap_codes"):
            value = raw.get(key)
            if type(value) is list:
                raw[key] = tuple(cast(list[object], value))
        cursor = raw.get("cursor")
        if isinstance(cursor, Mapping):
            raw["cursor"] = JsonObject(cast(Mapping[str, JsonValue], cursor))
        structural = raw.get("structural_payload")
        if isinstance(structural, Mapping) and type(structural) is not JsonObject:
            raw["structural_payload"] = JsonObject(cast(Mapping[str, JsonValue], structural))
        return JsonObject(raw)
    raise ControlError("invalid_request")


def _map_public_error(error: PublicOperationError) -> ControlError:
    code = error.code.value.lower()
    if code in {"invalid_request", "session_conflict"}:
        return ControlError(code, retryable=False)
    return ControlError("invalid_request", retryable=False)


def build_observation_support_handlers(port: ObservationPort) -> Mapping[ControlMethod, _SupportHandler]:
    """Bind the five observation_* control methods to one ObservationPort."""

    async def ingest(request: object) -> JsonObject:
        try:
            envelope = observation_envelope_from_json(_as_json_object(request))
        except ProtocolValueError as exc:
            raise ControlError("invalid_request") from exc
        if type(envelope) is not ObservationEnvelope:
            raise ControlError("invalid_request")
        try:
            result = await port.ingest(envelope)
        except PublicOperationError as exc:
            raise _map_public_error(exc) from exc
        return observation_ingest_result_to_json(result)

    async def status(request: object) -> JsonObject:
        try:
            query = observation_status_query_from_json(_as_json_object(request))
        except ProtocolValueError as exc:
            raise ControlError("invalid_request") from exc
        if type(query) is not ObservationStatusQuery:
            raise ControlError("invalid_request")
        try:
            result = await port.status(query)
        except PublicOperationError as exc:
            raise _map_public_error(exc) from exc
        return observation_status_to_json(result)

    async def pause(request: object) -> JsonObject:
        try:
            command = observation_control_command_from_json(_as_json_object(request))
        except ProtocolValueError as exc:
            raise ControlError("invalid_request") from exc
        if type(command) is not ObservationControlCommand:
            raise ControlError("invalid_request")
        try:
            result = await port.pause(command)
        except PublicOperationError as exc:
            raise _map_public_error(exc) from exc
        return observation_status_to_json(result)

    async def resume(request: object) -> JsonObject:
        try:
            command = observation_control_command_from_json(_as_json_object(request))
        except ProtocolValueError as exc:
            raise ControlError("invalid_request") from exc
        if type(command) is not ObservationControlCommand:
            raise ControlError("invalid_request")
        try:
            result = await port.resume(command)
        except PublicOperationError as exc:
            raise _map_public_error(exc) from exc
        return observation_status_to_json(result)

    async def revoke(request: object) -> JsonObject:
        try:
            command = observation_revoke_command_from_json(_as_json_object(request))
        except ProtocolValueError as exc:
            raise ControlError("invalid_request") from exc
        if type(command) is not ObservationRevokeCommand:
            raise ControlError("invalid_request")
        try:
            result = await port.revoke(command)
        except PublicOperationError as exc:
            raise _map_public_error(exc) from exc
        return observation_status_to_json(result)

    return {
        ControlMethod.OBSERVATION_INGEST: ingest,
        ControlMethod.OBSERVATION_STATUS: status,
        ControlMethod.OBSERVATION_PAUSE: pause,
        ControlMethod.OBSERVATION_RESUME: resume,
        ControlMethod.OBSERVATION_REVOKE: revoke,
    }
