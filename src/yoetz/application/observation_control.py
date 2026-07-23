"""Ordinary-control support handlers for ObservationPort / ObservationCoordinator."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol, cast

from yoetz.domain.observation import (
    ObservationControlCommand,
    ObservationEnvelope,
    ObservationIngestRequest,
    ObservationRevokeCommand,
    ObservationStatusQuery,
    observation_control_command_from_json,
    observation_envelope_from_json,
    observation_ingest_request_from_json,
    observation_ingest_result_to_json,
    observation_revoke_command_from_json,
    observation_status_query_from_json,
    observation_status_to_json,
)
from yoetz.domain.values import JsonObject, JsonValue
from yoetz.ports.control import ControlError, ControlMethod
from yoetz.ports.observation import ObservationPort
from yoetz.protocol.errors import ProtocolValueError, PublicOperationError

__all__ = ["ObservationIngestPort", "build_observation_support_handlers"]

type _SupportHandler = Callable[[object], Awaitable[JsonObject]]


class ObservationIngestPort(Protocol):
    """Port or coordinator that accepts redacted ObservationIngestRequest bodies."""

    async def ingest_request(self, request: ObservationIngestRequest): ...

    async def status(self, query: ObservationStatusQuery): ...

    async def pause(self, command: ObservationControlCommand): ...

    async def resume(self, command: ObservationControlCommand): ...

    async def revoke(self, command: ObservationRevokeCommand): ...


def _as_json_object(request: object) -> JsonObject:
    if type(request) is JsonObject:
        return request
    if isinstance(request, Mapping):
        source = cast(Mapping[str, object], request)
        raw: dict[str, JsonValue] = {}
        for key, value in source.items():
            if type(key) is not str:
                raise ControlError("invalid_request")
            raw[key] = cast(JsonValue, value)
        # Wire JSON arrays arrive as lists; domain parsers require tuples.
        for key in ("content_object_refs", "gap_codes"):
            value = source.get(key)
            if type(value) is list:
                raw[key] = tuple(cast(list[JsonValue], value))
        cursor = raw.get("cursor")
        if isinstance(cursor, Mapping):
            raw["cursor"] = JsonObject(cast(Mapping[str, JsonValue], cursor))
        structural = raw.get("structural_payload")
        if isinstance(structural, Mapping) and type(structural) is not JsonObject:
            raw["structural_payload"] = JsonObject(cast(Mapping[str, JsonValue], structural))
        envelope = raw.get("envelope")
        if isinstance(envelope, Mapping) and type(envelope) is not JsonObject:
            nested = dict(cast(Mapping[str, JsonValue], envelope))
            for key in ("content_object_refs", "gap_codes"):
                value = nested.get(key)
                if type(value) is list:
                    nested[key] = tuple(cast(list[JsonValue], value))
            nested_cursor = nested.get("cursor")
            if isinstance(nested_cursor, Mapping):
                nested["cursor"] = JsonObject(cast(Mapping[str, JsonValue], nested_cursor))
            nested_structural = nested.get("structural_payload")
            if isinstance(nested_structural, Mapping) and type(nested_structural) is not JsonObject:
                nested["structural_payload"] = JsonObject(
                    cast(Mapping[str, JsonValue], nested_structural)
                )
            raw["envelope"] = JsonObject(nested)
        return JsonObject(raw)
    raise ControlError("invalid_request")


def _map_public_error(error: PublicOperationError) -> ControlError:
    code = error.code.value.lower()
    if code in {"invalid_request", "session_conflict"}:
        return ControlError(code, retryable=False)
    return ControlError("invalid_request", retryable=False)


def build_observation_support_handlers(
    port: ObservationPort | ObservationIngestPort,
) -> Mapping[ControlMethod, _SupportHandler]:
    """Bind the five observation_* control methods to one ObservationPort/coordinator."""

    async def ingest(request: object) -> JsonObject:
        body = _as_json_object(request)
        try:
            if "codex_session_id" in body and hasattr(port, "ingest_request"):
                ingest_request = observation_ingest_request_from_json(body)
                if type(ingest_request) is not ObservationIngestRequest:
                    raise ControlError("invalid_request")
                result = await cast(ObservationIngestPort, port).ingest_request(ingest_request)
            else:
                envelope = observation_envelope_from_json(body)
                if type(envelope) is not ObservationEnvelope:
                    raise ControlError("invalid_request")
                result = await cast(ObservationPort, port).ingest(envelope)
        except ProtocolValueError as exc:
            raise ControlError("invalid_request") from exc
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
