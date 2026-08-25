"""Exact Codex capability cells derived from reviewed rollout fixtures.

Neighboring Codex builds stay untested. This module is the owning projection for
``CODEX_HARNESS_PROFILE``, the Codex skill manifest bounds, and
``yoetz version --json`` ``codex_capability_profiles``. It does not freeze the
full skill/MCP/hook matrix in ``runtime-support.json``.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from yoetz.protocol.canonical import JsonValue

__all__ = [
    "CODEX_ROLLOUT_CAPABILITY_PROFILE_ID",
    "CODEX_ROLLOUT_CLI_VERSION",
    "CODEX_ROLLOUT_DENIED_VERSIONS",
    "CODEX_ROLLOUT_EVIDENCE_CASE_IDS",
    "CODEX_ROLLOUT_HISTORY_MODES",
    "CODEX_ROLLOUT_IMPORTER_PROFILE_ID",
    "CODEX_ROLLOUT_STREAM_MAPPING_VERSION",
    "CODEX_ROLLOUT_SUPPORTED_VERSIONS",
    "CODEX_ROLLOUT_SURFACE",
    "codex_version_manifest_profiles",
    "skill_manifest_capability_fields",
]

CODEX_ROLLOUT_CLI_VERSION: Final = "0.148.0"
CODEX_ROLLOUT_CAPABILITY_PROFILE_ID: Final = "codex-cli-rollout-0.148.0"
CODEX_ROLLOUT_IMPORTER_PROFILE_ID: Final = "codex-rollout-jsonl/0.148.0/v1"
CODEX_ROLLOUT_STREAM_MAPPING_VERSION: Final = "codex-obs-stream/1.1.0"
CODEX_ROLLOUT_SURFACE: Final = "session_stream_rollout"
CODEX_ROLLOUT_HISTORY_MODES: Final = ("legacy", "paginated")
CODEX_ROLLOUT_SUPPORTED_VERSIONS: Final = (CODEX_ROLLOUT_CLI_VERSION,)
CODEX_ROLLOUT_DENIED_VERSIONS: Final = ()
CODEX_ROLLOUT_EVIDENCE_CASE_IDS: Final = ("IMP-006", "IMP-007", "IMP-008", "IMP-009")


def skill_manifest_capability_fields() -> Mapping[str, JsonValue]:
    """Return the skill-manifest bounds owned by the rollout cell."""

    body: dict[str, JsonValue] = {
        "capability_profile_ids": [CODEX_ROLLOUT_CAPABILITY_PROFILE_ID],
        "codex_version_bounds": {
            "denied": list(CODEX_ROLLOUT_DENIED_VERSIONS),
            "supported": list(CODEX_ROLLOUT_SUPPORTED_VERSIONS),
            "tested": list(CODEX_ROLLOUT_SUPPORTED_VERSIONS),
        },
        "hooks_by_capability_profile": {CODEX_ROLLOUT_CAPABILITY_PROFILE_ID: None},
    }
    return MappingProxyType(body)


def codex_version_manifest_profiles() -> tuple[Mapping[str, JsonValue], ...]:
    """Return the exact advertised Codex cells for ``yoetz version --json``.

    Shape is the frozen ``codex_capability_profile`` in
    ``schemas/version/version-manifest-2.0.0.schema.json``. Hook arms stay
    ``absent``: this cell proves session-stream rollout parsing, not skill/MCP
    observation or trigger hooks.
    """

    profile: dict[str, JsonValue] = {
        "capability_profile_id": CODEX_ROLLOUT_CAPABILITY_PROFILE_ID,
        "capability_profile_version": CODEX_ROLLOUT_IMPORTER_PROFILE_ID,
        "codex_version": CODEX_ROLLOUT_CLI_VERSION,
        "integration_modes": ["local_cli"],
        "observation_hook_status": "absent",
        "trigger_hook_status": "absent",
    }
    return (MappingProxyType(profile),)
