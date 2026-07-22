"""Tier-zero agent instructions must front-load the material-task intake cue."""

from __future__ import annotations

from pathlib import Path

from yoetz.mcp.resources import read_resource

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CANONICAL = _REPO_ROOT / "guidance" / "agent-instructions.md"
_PACKAGED = _REPO_ROOT / "src" / "yoetz" / "resources" / "guidance" / "agent-instructions.md"
_INTAKE_WINDOW = 512
_MATERIAL_TRIGGER = (
    b"Use Yoetz for material multi-step, delegated, resumable, or verification-heavy work."
)
_START_CUE = b"Call `start` before substantive work."
_TRIVIAL_EXCLUSION = (
    b"Skip Yoetz for trivial questions or edits where the ceremony exceeds the integrity benefit."
)


def _assert_intake_cue_within_first_512(data: bytes) -> None:
    assert data == data.decode("utf-8").encode("utf-8")
    window = data[:_INTAKE_WINDOW]
    assert _MATERIAL_TRIGGER in window
    assert _START_CUE in window
    assert _TRIVIAL_EXCLUSION in window


def test_canonical_agent_instructions_front_load_intake_cue() -> None:
    _assert_intake_cue_within_first_512(_CANONICAL.read_bytes())


def test_packaged_agent_instructions_front_load_intake_cue() -> None:
    packaged = _PACKAGED.read_bytes()
    assert packaged == _CANONICAL.read_bytes()
    _assert_intake_cue_within_first_512(packaged)
    assert packaged == read_resource("yoetz://guidance/agent-instructions.md")
