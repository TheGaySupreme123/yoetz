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
            "Read at session start, and re-read if the initialize instructions are not in "
            "context. The non-negotiable floor: when to activate, how often to call each "
            "operation, what is never published, and how honestly to word a conclusion."
        ),
        annotations=GuidanceResourceAnnotations(audience=("assistant",), priority=1.0),
    ),
    GuidanceResource(
        uri="yoetz://guidance/workflow.md",
        logical_name="guidance/workflow.md",
        name="workflow.md",
        title="Yoetz cooperative workflow",
        description=(
            "Read before the first start call. The ten steps, the per-operation cadence, when to "
            "stop retrying, resume and handoff behavior, and how to state what the run changed."
        ),
        annotations=GuidanceResourceAnnotations(audience=("assistant",), priority=0.9),
    ),
    GuidanceResource(
        uri="yoetz://guidance/publication-policy.md",
        logical_name="guidance/publication-policy.md",
        name="publication-policy.md",
        title="Yoetz publication policy",
        description=(
            "Read before the first publish_work call. What is material enough to publish, how "
            "large a batch should be, the sixteen event families, and what is never published."
        ),
        annotations=GuidanceResourceAnnotations(audience=("assistant",), priority=0.8),
    ),
    GuidanceResource(
        uri="yoetz://guidance/coverage-and-receipts.md",
        logical_name="guidance/coverage-and-receipts.md",
        name="coverage-and-receipts.md",
        title="Yoetz coverage and receipts",
        description=(
            "Read before the first check call. The coverage vector, why a recorded finding stays "
            "recorded, when to stop requesting semantic review, and how to word a conclusion."
        ),
        annotations=GuidanceResourceAnnotations(audience=("assistant",), priority=0.8),
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
