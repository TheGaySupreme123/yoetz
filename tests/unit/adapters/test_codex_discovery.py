"""Codex PATH discovery: dedupe, bounds, version parsing, and zero mutation."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from yoetz.adapters.integrations.codex_discovery import discover_codex_binaries
from yoetz.ports.integrations import HarnessId


class _Probe:
    def __init__(self, entries: tuple[str, ...], versions: dict[str, str | None]) -> None:
        self._entries = entries
        self.versions = versions
        self.probed: list[str] = []

    def path_entries(self) -> tuple[str, ...]:
        return self._entries

    def run_version(self, executable: str) -> str | None:
        self.probed.append(executable)
        return self.versions.get(executable)


def _make_codex(directory: Path, *, executable: bool = True) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    binary = directory / "codex"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    mode = 0o755 if executable else 0o644
    os.chmod(binary, mode)
    return binary


def test_no_entries_yields_empty(tmp_path: Path) -> None:
    probe = _Probe((str(tmp_path / "missing"),), {})
    assert discover_codex_binaries(_probe=probe) == ()


def test_single_candidate_reports_untested_and_parsed_version(tmp_path: Path) -> None:
    binary = _make_codex(tmp_path / "bin")
    probe = _Probe((str(binary.parent),), {str(binary): "codex-cli 0.144.5"})
    result = discover_codex_binaries(_probe=probe)
    assert len(result) == 1
    found = result[0]
    assert found.harness_id is HarnessId.CODEX
    assert found.executable_path == str(binary)
    assert found.reported_version == "0.144.5"
    # E-002: discovery alone never claims capability support.
    assert found.compatibility == "untested"


def test_unparsable_or_failed_version_probe_yields_none(tmp_path: Path) -> None:
    binary = _make_codex(tmp_path / "bin")
    for output in (None, "no digits here", ""):
        probe = _Probe((str(binary.parent),), {str(binary): output})
        result = discover_codex_binaries(_probe=probe)
        assert result[0].reported_version is None


def test_symlinked_duplicate_is_deduplicated_but_path_visible_name_kept(tmp_path: Path) -> None:
    real = _make_codex(tmp_path / "real")
    alias_dir = tmp_path / "alias"
    alias_dir.mkdir()
    (alias_dir / "codex").symlink_to(real)
    probe = _Probe((str(alias_dir), str(real.parent)), {})
    result = discover_codex_binaries(_probe=probe)
    # One entry per resolved target; the first PATH-visible name wins.
    assert len(result) == 1
    assert result[0].executable_path == str(alias_dir / "codex")


def test_two_distinct_installs_are_both_reported_sorted(tmp_path: Path) -> None:
    first = _make_codex(tmp_path / "a")
    second = _make_codex(tmp_path / "b")
    probe = _Probe((str(second.parent), str(first.parent)), {})
    result = discover_codex_binaries(_probe=probe)
    assert [entry.executable_path for entry in result] == sorted([str(first), str(second)])


def test_non_executable_candidate_is_skipped(tmp_path: Path) -> None:
    binary = _make_codex(tmp_path / "bin", executable=False)
    probe = _Probe((str(binary.parent),), {})
    assert discover_codex_binaries(_probe=probe) == ()


def test_discovery_never_mutates_the_candidate(tmp_path: Path) -> None:
    binary = _make_codex(tmp_path / "bin")
    before = binary.read_bytes(), stat.S_IMODE(binary.stat().st_mode)
    probe = _Probe((str(binary.parent),), {})
    discover_codex_binaries(_probe=probe)
    assert (binary.read_bytes(), stat.S_IMODE(binary.stat().st_mode)) == before
