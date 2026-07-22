"""Frozen, honesty-bounded MCP tool descriptors and initialize instructions."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from yoetz.mcp.resources import read_resource
from yoetz.protocol.canonical import JsonValue
from yoetz.protocol.schemas import SCHEMA_NAMESPACE, schema_document_for

__all__ = [
    "TOOL_DESCRIPTOR_DIGESTS",
    "TOOL_DESCRIPTOR_SET_DIGEST",
    "TOOL_DESCRIPTORS",
    "ToolAnnotations",
    "ToolDescriptor",
    "descriptor_for",
    "server_instructions",
]

_SCHEMA_VERSION: Final = "1.0.0"
_INSTRUCTIONS_URI: Final = "yoetz://guidance/agent-instructions.md"
_FORBIDDEN_CLAIMS: Final = re.compile(
    r"\b(?:authenticated|enforces?|gates?|observes?|proved|proves?|verified)\b",
    re.IGNORECASE | re.ASCII,
)
_BOUNDARY_TERMS: Final = re.compile(
    r"(?:/|\\|\b(?:claude|codex|cursor|gemini|host|model|openai|provider|version)\b)",
    re.IGNORECASE | re.ASCII,
)


@dataclass(frozen=True, slots=True)
class ToolAnnotations:
    """The exact MCP annotation hints for one public operation."""

    read_only: bool
    idempotent: bool
    destructive: bool = False
    open_world: bool = False

    def as_mcp_dict(self) -> dict[str, bool]:
        return {
            "readOnlyHint": self.read_only,
            "destructiveHint": self.destructive,
            "idempotentHint": self.idempotent,
            "openWorldHint": self.open_world,
        }


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    """Static identity, text, schemas, and annotations for one MCP tool."""

    name: str
    title: str
    description: str
    input_schema_ref: str
    output_schema_ref: str
    annotations: ToolAnnotations

    @property
    def input_schema(self) -> Mapping[str, JsonValue]:
        return schema_document_for(
            f"{self.name.replace('_', '-')}-request", _SCHEMA_VERSION
        ).json_schema

    @property
    def output_schema(self) -> Mapping[str, JsonValue]:
        return schema_document_for(
            f"{self.name.replace('_', '-')}-result", _SCHEMA_VERSION
        ).json_schema


def _descriptor(
    name: str,
    title: str,
    description: str,
    *,
    read_only: bool,
    idempotent: bool,
) -> ToolDescriptor:
    schema_name = name.replace("_", "-")
    return ToolDescriptor(
        name=name,
        title=title,
        description=description,
        input_schema_ref=(
            f"{SCHEMA_NAMESPACE}operations/{schema_name}-request-{_SCHEMA_VERSION}.schema.json"
        ),
        output_schema_ref=(
            f"{SCHEMA_NAMESPACE}operations/{schema_name}-result-{_SCHEMA_VERSION}.schema.json"
        ),
        annotations=ToolAnnotations(read_only=read_only, idempotent=idempotent),
    )


TOOL_DESCRIPTORS: Final = (
    _descriptor(
        "start",
        "Start or resume a work session",
        "Call for material multi-step, delegated, resumable, or verification-heavy work before "
        "substantive work; skip trivial questions or edits. Records or resumes a cooperative work "
        "session and returns its compact record. It does not show that work outside the published "
        "record occurred.",
        read_only=False,
        idempotent=True,
    ),
    _descriptor(
        "publish_work",
        "Publish recorded work",
        "Records a bounded batch of agent-published work events and returns the accepted event "
        "range and coverage. It has no information about work outside that batch.",
        read_only=False,
        idempotent=True,
    ),
    _descriptor(
        "check",
        "Check recorded work",
        "Runs the requested recorded-work checks and records the result; it returns at most "
        "max_findings findings plus a suppressed count, and status with view=findings reads the "
        "rest. A no_issue_detected verdict does not mean the work is correct.",
        read_only=False,
        idempotent=True,
    ),
    _descriptor(
        "respond",
        "Respond to a finding",
        "Records an acknowledgement, rejection, or bounded waiver for one finding at its recorded "
        "frontier. It does not resolve other findings or establish that underlying work changed.",
        read_only=False,
        idempotent=True,
    ),
    _descriptor(
        "status",
        "Read recorded status",
        "Reads one bounded, paginated view: assignment, candidate_findings, compact, evidence, "
        "findings, history, obligations, or versions. Call it when uncertain what you already did "
        "or committed to, rather than reconstructing from memory. view=findings reads recorded "
        "findings; view=candidate_findings returns unrecorded deterministic candidates without "
        "verdicts or IDs.",
        read_only=True,
        idempotent=True,
    ),
    _descriptor(
        "receipt",
        "Record and read a receipt",
        "Records and returns a receipt of the recorded conclusion and coverage limitations at one "
        "frontier. It does not establish correctness beyond that recorded coverage.",
        read_only=False,
        idempotent=True,
    ),
)


def _canonical_descriptor_bytes(descriptor: ToolDescriptor) -> bytes:
    return json.dumps(
        {
            "annotations": descriptor.annotations.as_mcp_dict(),
            "description": descriptor.description,
            "input_schema_ref": descriptor.input_schema_ref,
            "name": descriptor.name,
            "output_schema_ref": descriptor.output_schema_ref,
            "title": descriptor.title,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _digest_descriptor(descriptor: ToolDescriptor) -> str:
    return "sha256:" + hashlib.sha256(_canonical_descriptor_bytes(descriptor)).hexdigest()


# These are reviewed golden identities, not values supplied by a host or environment.
TOOL_DESCRIPTOR_DIGESTS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "start": "sha256:42509100525d5c866aa21c02cfa33942163967f79968ef1c7c7e00e15fb0e696",
        "publish_work": "sha256:8203bfce3611794f1164f1416c3e5602c286746d69c0f3385cd0e904b1bd7e19",
        "check": "sha256:bd78bde8d0586896318534abcc248aa8d87f30ff4952046593549dc57f394500",
        "respond": "sha256:740e576f822636bdcdf4f246a86192a336e7d0284aae611bbc6421ee62ed469a",
        "status": "sha256:298be02f811b28b0d588ee4cff81cf97a1a47d1f1e2bd7ed7a40d619ad7e4d60",
        "receipt": "sha256:75a8a26a45689c4d0fec54ee20784eda43096b8726fd59f924d599f4bd27d095",
    }
)
TOOL_DESCRIPTOR_SET_DIGEST: Final = (
    "sha256:fed4821789eb054b73919233b785c2750696f65af7ebe2ea3d98dbc407bbae6f"
)


def _lint_descriptor_set() -> None:
    names = tuple(descriptor.name for descriptor in TOOL_DESCRIPTORS)
    if names != ("start", "publish_work", "check", "respond", "status", "receipt"):
        raise RuntimeError("descriptor_registry_invalid")
    if len(set(names)) != len(names):
        raise RuntimeError("descriptor_registry_invalid")
    for descriptor in TOOL_DESCRIPTORS:
        if _FORBIDDEN_CLAIMS.search(descriptor.description) is not None:
            raise RuntimeError("descriptor_honesty_lint_failed")
        if _BOUNDARY_TERMS.search(descriptor.description) is not None:
            raise RuntimeError("descriptor_boundary_lint_failed")
        if _digest_descriptor(descriptor) != TOOL_DESCRIPTOR_DIGESTS[descriptor.name]:
            raise RuntimeError("descriptor_digest_mismatch")
    set_bytes = b"\n".join(_canonical_descriptor_bytes(item) for item in TOOL_DESCRIPTORS)
    if "sha256:" + hashlib.sha256(set_bytes).hexdigest() != TOOL_DESCRIPTOR_SET_DIGEST:
        raise RuntimeError("descriptor_set_digest_mismatch")


_DESCRIPTOR_BY_NAME: Final[Mapping[str, ToolDescriptor]] = MappingProxyType(
    {descriptor.name: descriptor for descriptor in TOOL_DESCRIPTORS}
)


def descriptor_for(name: str) -> ToolDescriptor:
    """Return one exact registered descriptor or fail without echoing its input."""

    if type(name) is not str:
        raise TypeError("tool_descriptor_name_wrong_type")
    try:
        return _DESCRIPTOR_BY_NAME[name]
    except KeyError:
        raise KeyError("unregistered_tool_descriptor") from None


def server_instructions() -> str:
    """Return the manifest-verified initialize instructions as strict UTF-8 text."""

    return read_resource(_INSTRUCTIONS_URI).decode("utf-8", errors="strict")


_lint_descriptor_set()
