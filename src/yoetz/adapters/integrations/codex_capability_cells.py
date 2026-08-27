"""Exact Codex rollout-parser evidence identities.

These constructed fixtures lock one session-file grammar. They are not an
installed skill, MCP, hook, activation, or model-use capture and therefore do
not populate any support surface consumed by ordinary installation/status.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from yoetz.protocol.canonical import JsonValue

__all__ = [
    "CODEX_ROLLOUT_CLI_VERSION",
    "CODEX_ROLLOUT_EVIDENCE_CASE_IDS",
    "CODEX_ROLLOUT_HISTORY_MODES",
    "CODEX_ROLLOUT_IMPORTER_PROFILE_ID",
    "CODEX_ROLLOUT_STREAM_MAPPING_VERSION",
    "CODEX_ROLLOUT_SURFACE",
    "codex_version_manifest_profiles",
    "skill_manifest_capability_fields",
]

CODEX_ROLLOUT_CLI_VERSION: Final = "0.148.0"
CODEX_ROLLOUT_IMPORTER_PROFILE_ID: Final = "codex-rollout-jsonl/0.148.0/v1"
CODEX_ROLLOUT_STREAM_MAPPING_VERSION: Final = "codex-obs-stream/1.2.0"
CODEX_ROLLOUT_SURFACE: Final = "session_stream_rollout"
CODEX_ROLLOUT_HISTORY_MODES: Final = ("legacy", "paginated")
CODEX_ROLLOUT_EVIDENCE_CASE_IDS: Final = ("IMP-006", "IMP-007", "IMP-008", "IMP-009")


def skill_manifest_capability_fields() -> Mapping[str, JsonValue]:
    """Keep skill support empty until the installed-artifact matrix is proven."""

    body: dict[str, JsonValue] = {
        "capability_profile_ids": [],
        "codex_version_bounds": {
            "denied": [],
            "supported": [],
            "tested": [],
        },
        "hooks_by_capability_profile": {},
    }
    return MappingProxyType(body)


def codex_version_manifest_profiles() -> tuple[Mapping[str, JsonValue], ...]:
    """Do not advertise a support cell from parser-only fixture evidence."""

    return ()
