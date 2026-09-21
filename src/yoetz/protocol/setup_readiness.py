"""Bounded read-only setup continuation contract."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from yoetz.protocol.canonical import JsonValue


class SetupReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_tag: Literal["yoetz.setup-readiness/1"] = Field(alias="schema")
    operation: Literal["local", "review", "connection"]
    reason: str
    project: str
    inspected_config_root: str | None
    next_command: str | None
    facts: dict[str, JsonValue]
    connection_observed: Literal[False] = False
