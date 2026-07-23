"""Exact-byte approved-check policy parsing and local trust gates."""

from __future__ import annotations

from pathlib import Path

import pytest

from yoetz.application.observation_check_policy import (
    load_observation_check_policy,
    parse_observation_check_policy,
)
from yoetz.protocol.errors import ProtocolValueError


def _policy(*, argv: str = '["/usr/bin/true"]') -> bytes:
    return (
        'format = "yoetz.approved-check-policy/1"\n'
        "\n"
        "[[checks]]\n"
        'id = "smoke"\n'
        f"argv = {argv}\n"
        "timeout_seconds = 10\n"
        "network = false\n"
    ).encode()


def test_policy_trust_identity_is_exact_raw_bytes() -> None:
    original = parse_observation_check_policy(_policy())
    whitespace_changed = parse_observation_check_policy(_policy() + b"\n")
    assert original.raw_digest != whitespace_changed.raw_digest
    assert original.checks[0].argv == ("/usr/bin/true",)
    assert original.checks[0].allow_network is False


def test_policy_rejects_unknown_fields_and_freeform_command() -> None:
    with pytest.raises(ProtocolValueError):
        parse_observation_check_policy(
            _policy().replace(b"network = false", b'network = false\ncommand = "true"')
        )
    with pytest.raises(ProtocolValueError):
        parse_observation_check_policy(_policy(argv='["/bin/sh", "-c", "echo unsafe"]'))


def test_policy_reader_rejects_symlinked_policy_directory(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "checks.toml").write_bytes(_policy())
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".yoetz").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ProtocolValueError):
        load_observation_check_policy(workspace)


def test_policy_reader_accepts_fixed_in_workspace_file(tmp_path: Path) -> None:
    policy_dir = tmp_path / ".yoetz"
    policy_dir.mkdir()
    (policy_dir / "checks.toml").write_bytes(_policy())
    policy, raw = load_observation_check_policy(tmp_path)
    assert raw == _policy()
    assert tuple(item.approval_id for item in policy.checks) == ("smoke",)
