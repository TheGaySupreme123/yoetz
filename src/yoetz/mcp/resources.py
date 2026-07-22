"""Closed registry of manifest-verified static MCP guidance resources."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from yoetz.version import read_verified_resource

__all__ = [
    "GUIDANCE_RESOURCES",
    "GuidanceResource",
    "GuidanceResourceAnnotations",
    "GuidanceResourceError",
    "list_resources",
    "read_resource",
]


class GuidanceResourceError(ValueError):
    """A bounded resource-registry failure with no caller-controlled text."""


@dataclass(frozen=True, slots=True)
class GuidanceResourceAnnotations:
    """Honest MCP resource annotations: intended audience and relative priority."""

    audience: tuple[str, ...]
    priority: float


@dataclass(frozen=True, slots=True)
class GuidanceResource:
    uri: str
    logical_name: str
    name: str
    title: str
    description: str
    annotations: GuidanceResourceAnnotations
    media_type: str = "text/markdown"

    @property
    def bytes(self) -> bytes:
        return read_verified_resource(self.logical_name)

    @property
    def text(self) -> str:
        return self.bytes.decode("utf-8", errors="strict")

    @property
    def size(self) -> int:
        return len(self.bytes)


GUIDANCE_RESOURCES: Final = (
    GuidanceResource(
        uri="yoetz://guidance/agent-instructions.md",
        logical_name="guidance/agent-instructions.md",
        name="agent-instructions.md",
        title="Yoetz agent instructions",
        description=(
            "Read at session start when cooperative recorded-work guidance is needed. "
            "Always-delivered floor instructions for using the recorded work surface."
        ),
        annotations=GuidanceResourceAnnotations(audience=("assistant",), priority=1.0),
    ),
    GuidanceResource(
        uri="yoetz://guidance/workflow.md",
        logical_name="guidance/workflow.md",
        name="workflow.md",
        title="Yoetz cooperative workflow",
        description=(
            "Read at task intake before substantive work. The bounded cooperative workflow for "
            "recording, checking, and resuming work."
        ),
        annotations=GuidanceResourceAnnotations(audience=("assistant",), priority=0.9),
    ),
    GuidanceResource(
        uri="yoetz://guidance/publication-policy.md",
        logical_name="guidance/publication-policy.md",
        name="publication-policy.md",
        title="Yoetz publication policy",
        description=(
            "Read before publishing material work events. Rules for publishing into the local "
            "record."
        ),
        annotations=GuidanceResourceAnnotations(audience=("assistant",), priority=0.6),
    ),
    GuidanceResource(
        uri="yoetz://guidance/coverage-and-receipts.md",
        logical_name="guidance/coverage-and-receipts.md",
        name="coverage-and-receipts.md",
        title="Yoetz coverage and receipts",
        description=(
            "Read before interpreting coverage, findings, conclusions, or receipt limitations."
        ),
        annotations=GuidanceResourceAnnotations(audience=("assistant",), priority=0.6),
    ),
)

_RESOURCE_BY_URI: Final = MappingProxyType(
    {resource.uri: resource for resource in GUIDANCE_RESOURCES}
)


def _resource_for_uri(uri: object) -> GuidanceResource:
    if type(uri) is not str:
        raise GuidanceResourceError("guidance_resource_uri_invalid")
    try:
        return _RESOURCE_BY_URI[uri]
    except KeyError:
        raise GuidanceResourceError("guidance_resource_uri_unregistered") from None


def list_resources() -> tuple[GuidanceResource, ...]:
    """Return the exact stable registry after verifying every resource member."""

    try:
        for resource in GUIDANCE_RESOURCES:
            resource.bytes
    except GuidanceResourceError:
        raise
    except BaseException:
        raise GuidanceResourceError("guidance_resource_integrity_failed") from None
    return GUIDANCE_RESOURCES


def read_resource(uri: str) -> bytes:
    """Read one exact URI registry key; URI text is never interpreted as a path."""

    resource = _resource_for_uri(uri)
    try:
        return resource.bytes
    except BaseException:
        raise GuidanceResourceError("guidance_resource_integrity_failed") from None
