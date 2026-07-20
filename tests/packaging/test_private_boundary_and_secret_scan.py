"""Publication-boundary mutation suite for ``scripts/scan_public_boundary.py``.

Exercises the real, already-implemented boundary scanner (read-only; this file never modifies
``scripts/scan_public_boundary.py``) against synthetic clean and mutated candidates, and against
the real built wheel/sdist, to prove the invariants from
``specs/tests/packaging/test_private_boundary_and_secret_scan.py.md``: every candidate member is
scanned, any match or incomplete state blocks publication, reports never reveal matched bytes/
absolute paths, and exceptions (where they exist) are exact.

Scope notes (verbatim conflicts, reported rather than guessed around):

1. The family/script spec (``specs/scripts/scan_public_boundary.py.md``) describes a reviewed
   per-file exception mechanism ("Allow exceptions match exact rule ID + normalized file + bounded
   digest/line context ... every exception states ... keys cannot be allowlisted"). The actual
   ``scripts/scan_public_boundary.py`` has no such mechanism: ``BoundaryRule``/``ScanReport`` carry
   no exception fields, and ``main()`` exposes only ``--rules`` (a full test-only ruleset override)
   and ``--canary-file``. The "exact reviewed exception" test case below is marked ``xfail`` with
   this exact reason instead of being invented against a nonexistent API.
2. ``enumerate_target`` does not recurse into a nested supported archive found inside a scanned
   artifact (e.g. a ``.zip`` stored as a member of the outer wheel/sdist): the nested archive's raw
   compressed bytes are scanned as one opaque content blob, so a plaintext secret placed only inside
   the nested archive is not detected. This contradicts this spec's "nested supported archive"
   mutation placement. It is demonstrated and marked ``xfail`` below as a known scanner gap.
3. Running the scanner's ``--source-tree`` target against the *actual* full repository checkout
   (rather than a clean release export) currently reports real findings, because several other
   already-committed test files elsewhere in the repository intentionally embed PEM-shaped/
   credential-shaped synthetic fixtures for their own redaction tests, and the shipped
   ``src/yoetz/observability/privacy.py`` module legitimately embeds the literal PEM header
   constants it uses to *detect* and redact such content in diagnostics. Both are genuine rule
   PRIV-CRED-001 matches against non-secret pattern-definition source text. This file does not
   assert a clean scan of the live repository tree or the live built wheel/sdist; it demonstrates
   the exact, current, single wheel finding as an ``xfail`` characterization instead of hiding it.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Final

import pytest

_REPO_ROOT: Final = Path(__file__).resolve().parents[2]
_SCRIPT_PATH: Final = _REPO_ROOT / "scripts" / "scan_public_boundary.py"


def _load_scanner() -> ModuleType:
    spec = importlib.util.spec_from_file_location("yoetz_scan_public_boundary", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def scanner() -> ModuleType:
    return _load_scanner()


@dataclass(frozen=True, slots=True)
class _BuiltDist:
    directory: Path
    wheel: Path
    sdist: Path


@pytest.fixture(scope="module")
def built_dist(tmp_path_factory: pytest.TempPathFactory) -> _BuiltDist:
    dist_dir = tmp_path_factory.mktemp("boundary-dist")
    result = subprocess.run(
        ["uv", "build", "--no-sources", "-o", str(dist_dir), str(_REPO_ROOT)],
        capture_output=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    wheels = sorted(dist_dir.glob("*.whl"))
    sdists = sorted(dist_dir.glob("*.tar.gz"))
    assert len(wheels) == 1
    assert len(sdists) == 1
    return _BuiltDist(dist_dir, wheels[0], sdists[0])


def _clean_git_source_tree(root: Path) -> Path:
    """Build a tiny, real git-tracked source tree with no boundary findings."""

    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "safe.py").write_text('"""A clean module."""\n', encoding="utf-8")
    (root / "README.md").write_text("# clean project\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "clean"], cwd=root, check=True)
    return root


# ---------------------------------------------------------------------------
# Documented public API surface
# ---------------------------------------------------------------------------


def test_scanner_exposes_the_documented_public_api(scanner: ModuleType) -> None:
    expected = {
        "BoundaryFinding",
        "BoundaryRule",
        "FileEntry",
        "ScanReport",
        "ScanTarget",
        "build_report",
        "enumerate_target",
        "load_rules",
        "main",
        "scan_archive_member",
        "scan_bytes",
        "scan_filename",
        "scan_metadata",
    }
    assert expected <= set(scanner.__all__)


# ---------------------------------------------------------------------------
# Clean candidate
# ---------------------------------------------------------------------------


def test_clean_synthetic_source_tree_scans_with_zero_findings(
    scanner: ModuleType, tmp_path: Path
) -> None:
    root = _clean_git_source_tree(tmp_path / "clean-repo")
    target = scanner.ScanTarget(kind="source", label="clean", path=root)
    entries = scanner.enumerate_target(target)
    assert len(entries) == 2
    findings: list[object] = []
    for entry in entries:
        findings.extend(scanner.scan_filename(entry, scanner.load_rules(), target_label="clean"))
        findings.extend(scanner.scan_bytes(entry, scanner.load_rules(), target_label="clean"))
    assert findings == []


def test_scanner_cli_passes_on_the_clean_synthetic_tree(
    scanner: ModuleType, tmp_path: Path
) -> None:
    root = _clean_git_source_tree(tmp_path / "clean-repo-cli")
    rc = scanner.main(["--source-tree", str(root)])
    assert rc == 0


# ---------------------------------------------------------------------------
# Mutation placements: filename, text, binary, archive metadata
# ---------------------------------------------------------------------------


def test_filename_placement_is_detected_by_rule_id(scanner: ModuleType) -> None:
    entry = scanner.FileEntry(relative_path=".claude/session.json", size=2, data=b"{}")
    findings = scanner.scan_filename(entry, scanner.load_rules(), target_label="t")
    rule_ids = {f.rule_id for f in findings}
    assert "PRIV-SESSION-001" in rule_ids


def test_text_content_placement_is_detected(scanner: ModuleType) -> None:
    data = b"reach me at postgres://alice:s3cr3t@db.internal:5432/app\n"
    entry = scanner.FileEntry(relative_path="config.toml", size=len(data), data=data)
    findings = scanner.scan_bytes(entry, scanner.load_rules(), target_label="t")
    rule_ids = {f.rule_id for f in findings}
    assert "PRIV-CRED-003" in rule_ids


def test_binary_undecodable_content_still_scans_raw_bytes(scanner: ModuleType) -> None:
    # Invalid UTF-8 byte (0xFF) surrounding a raw AWS-shaped key; scan_bytes must fall back to
    # scanning the raw byte pattern rather than treating undecodable content as clean.
    data = b"\xff\xfeAKIA1234567890123456\xff\xfe"
    entry = scanner.FileEntry(relative_path="blob.bin", size=len(data), data=data)
    findings = scanner.scan_bytes(entry, scanner.load_rules(), target_label="t")
    rule_ids = {f.rule_id for f in findings}
    assert "PRIV-CRED-002" in rule_ids


def test_archive_metadata_placement_is_detected(scanner: ModuleType) -> None:
    entries = (
        scanner.FileEntry(
            relative_path="pkg-0.1.dist-info/METADATA",
            size=20,
            data=b"Home-page: /Users/alice/notes\n",
        ),
    )
    findings = scanner.scan_metadata(entries, scanner.load_rules(), target_label="t")
    rule_ids = {f.rule_id for f in findings}
    assert "PRIV-PATH-001" in rule_ids


def test_metadata_scan_ignores_non_metadata_paths(scanner: ModuleType) -> None:
    entries = (
        scanner.FileEntry(
            relative_path="yoetz/somewhere/not_metadata.py",
            size=20,
            data=b"# /Users/alice/notes\n",
        ),
    )
    findings = scanner.scan_metadata(entries, scanner.load_rules(), target_label="t")
    assert findings == ()


# ---------------------------------------------------------------------------
# Canary detection and redaction
# ---------------------------------------------------------------------------


def test_injected_canary_is_detected_and_never_leaked_in_the_report(
    scanner: ModuleType, tmp_path: Path
) -> None:
    canary = b"CANARY-3f9c2a7e1b6d4f58"
    entry = scanner.FileEntry(
        relative_path="notes.txt", size=64, data=b"prefix " + canary + b" suffix"
    )
    findings = scanner.scan_bytes(entry, scanner.load_rules(), target_label="t", canary=canary)
    canary_findings = [f for f in findings if f.rule_id == "CANARY-EXACT-001"]
    assert len(canary_findings) == 1
    report_bytes = scanner.build_report("t", "artifact", findings)
    assert canary not in report_bytes
    report = json.loads(report_bytes)
    assert report["finding_count"] == len(findings)
    for rendered in report["findings"]:
        assert set(rendered) == {
            "category",
            "file_digest",
            "location_bucket",
            "match_count",
            "relative_path",
            "rule_id",
            "severity",
            "target_label",
        }


def test_report_never_carries_an_absolute_repository_path(scanner: ModuleType) -> None:
    entry = scanner.FileEntry(
        relative_path=".env", size=10, data=str(_REPO_ROOT).encode("utf-8") + b"\nSECRET=x\n"
    )
    findings = scanner.scan_filename(
        entry, scanner.load_rules(), target_label="t"
    ) + scanner.scan_bytes(entry, scanner.load_rules(), target_label="t")
    report_bytes = scanner.build_report("t", "artifact", findings)
    assert str(_REPO_ROOT).encode("utf-8") not in report_bytes


# ---------------------------------------------------------------------------
# Archive traversal / collision / compression-bomb safety (via a real zip)
# ---------------------------------------------------------------------------


def test_archive_traversal_member_is_rejected(scanner: ModuleType, tmp_path: Path) -> None:
    archive_path = tmp_path / "evil.whl"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../../etc/passwd", "not really")
    target = scanner.ScanTarget(kind="artifact", label="evil", path=archive_path)
    with pytest.raises(scanner.BoundaryScanError) as excinfo:
        scanner.enumerate_target(target)
    assert excinfo.value.reason == "unsafe_archive_member_path"


def test_archive_compression_bomb_member_is_rejected(scanner: ModuleType, tmp_path: Path) -> None:
    archive_path = tmp_path / "bomb.whl"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("big.bin", b"\x00" * 5_000_000)
    target = scanner.ScanTarget(kind="artifact", label="bomb", path=archive_path)
    # A real, highly compressible all-zero blob exceeds the fixed 200x compression-ratio cap.
    with pytest.raises(scanner.BoundaryScanError) as excinfo:
        scanner.enumerate_target(target)
    assert excinfo.value.reason == "compression_ratio_exceeded"


def test_unsupported_artifact_format_is_an_incomplete_scan(
    scanner: ModuleType, tmp_path: Path
) -> None:
    stray = tmp_path / "not-an-archive.bin"
    stray.write_bytes(b"\x00\x01\x02")
    target = scanner.ScanTarget(kind="artifact", label="stray", path=stray)
    with pytest.raises(scanner.BoundaryScanError) as excinfo:
        scanner.enumerate_target(target)
    assert excinfo.value.reason == "unsupported_artifact_format"


def test_missing_artifact_blocks_as_incomplete_via_cli(scanner: ModuleType, tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.whl"
    rc = scanner.main(["--artifact", str(missing)])
    assert rc == 1


# ---------------------------------------------------------------------------
# Rule-config loading (the test-only substitution mechanism that does exist)
# ---------------------------------------------------------------------------


def test_custom_rule_config_duplicate_id_is_rejected(scanner: ModuleType, tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(
        json.dumps(
            [
                {
                    "rule_id": "DUP-001",
                    "category": "test",
                    "severity": "low",
                    "scope": "all",
                    "detector": "filename",
                    "pattern": "a",
                    "justification": "j",
                    "owner_role": "release-maintainer",
                },
                {
                    "rule_id": "DUP-001",
                    "category": "test",
                    "severity": "low",
                    "scope": "all",
                    "detector": "filename",
                    "pattern": "b",
                    "justification": "j",
                    "owner_role": "release-maintainer",
                },
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(scanner.BoundaryScanError) as excinfo:
        scanner.load_rules(rules_path)
    assert excinfo.value.reason == "rule_config_duplicate_id"


def test_custom_rule_config_missing_field_is_rejected(scanner: ModuleType, tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps([{"rule_id": "X"}]), encoding="utf-8")
    with pytest.raises(scanner.BoundaryScanError) as excinfo:
        scanner.load_rules(rules_path)
    assert excinfo.value.reason == "rule_config_invalid"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "scripts/scan_public_boundary.py has no per-file reviewed-exception mechanism "
        "(no --exceptions flag, no exception fields on BoundaryRule/ScanReport); only a "
        "full-ruleset --rules override and --canary-file exist. An 'exact rule+path+digest "
        "passes, changed path/digest/expired owner fails' exception test cannot be built "
        "against the real script without inventing an API it does not have."
    ),
)
def test_exact_reviewed_exception_matches_rule_path_and_digest(scanner: ModuleType) -> None:
    raise AssertionError("no reviewed-exception mechanism exists in scan_public_boundary.py")


# ---------------------------------------------------------------------------
# Real built artifacts
# ---------------------------------------------------------------------------


def test_built_sdist_and_wheel_are_fully_enumerable_without_error(
    scanner: ModuleType, built_dist: _BuiltDist
) -> None:
    wheel_entries = scanner.enumerate_target(
        scanner.ScanTarget(kind="artifact", label="wheel", path=built_dist.wheel)
    )
    sdist_entries = scanner.enumerate_target(
        scanner.ScanTarget(kind="artifact", label="sdist", path=built_dist.sdist)
    )
    assert len(wheel_entries) > 0
    assert len(sdist_entries) > 0
    # Every scanned member is a real, distinct, safely-named path.
    assert len({entry.relative_path for entry in wheel_entries}) == len(wheel_entries)


def test_built_wheel_has_no_development_cache_or_vcs_files(
    scanner: ModuleType, built_dist: _BuiltDist
) -> None:
    entries = scanner.enumerate_target(
        scanner.ScanTarget(kind="artifact", label="wheel", path=built_dist.wheel)
    )
    findings: list[Any] = []
    for entry in entries:
        findings.extend(scanner.scan_filename(entry, scanner.load_rules(), target_label="wheel"))
    cache_findings = [f for f in findings if f.category == "debug_build_cache_file"]
    assert cache_findings == []


@pytest.mark.xfail(
    strict=True,
    reason=(
        "The shipped src/yoetz/observability/privacy.py module legitimately embeds the literal "
        "PEM header constants ('-----BEGIN PRIVATE KEY-----' etc.) it uses to detect and redact "
        "such content in diagnostics. scan_public_boundary.py's PRIV-CRED-001 content rule matches "
        "the bare header text regardless of context, and that rule's own doc states private-key "
        "rules 'cannot be allowlisted'. The real, current wheel therefore fails this scan with "
        "exactly one PRIV-CRED-001 finding against that file -- not a leaked secret, but a real, "
        "reportable false positive in the current scanner/product pairing that this test file must "
        "not paper over by loosening the scanner (out of scope) or editing src/yoetz (out of scope)."
    ),
)
def test_built_wheel_scans_clean_of_every_boundary_rule(
    scanner: ModuleType, built_dist: _BuiltDist
) -> None:
    entries = scanner.enumerate_target(
        scanner.ScanTarget(kind="artifact", label="wheel", path=built_dist.wheel)
    )
    rules = scanner.load_rules()
    findings: list[object] = []
    for entry in entries:
        findings.extend(scanner.scan_filename(entry, rules, target_label="wheel"))
        findings.extend(scanner.scan_bytes(entry, rules, target_label="wheel"))
    findings.extend(scanner.scan_metadata(entries, rules, target_label="wheel"))
    assert findings == []


@pytest.mark.xfail(
    strict=True,
    reason=(
        "enumerate_target does not recurse into a nested supported archive: a secret placed only "
        "inside a .zip stored as a member of the outer artifact is scanned as opaque compressed "
        "bytes and is not detected, contradicting this spec's 'nested supported archive' mutation "
        "placement. Demonstrated here rather than silently skipped."
    ),
)
def test_nested_archive_member_secret_is_detected(scanner: ModuleType, tmp_path: Path) -> None:
    # Real DEFLATE compression on both layers: the plaintext key never appears as a literal
    # byte run in the outer member's stored bytes, which is exactly what lets it slip past a
    # non-recursive scan of the outer archive.
    inner_path = tmp_path / "inner.zip"
    with zipfile.ZipFile(inner_path, "w", compression=zipfile.ZIP_DEFLATED) as inner:
        inner.writestr(
            "secret.pem",
            "-----BEGIN RSA PRIVATE KEY-----\n" + "MIIBOgIBAAJBAK" * 40 + "\n",
        )
    outer_path = tmp_path / "outer.whl"
    with zipfile.ZipFile(outer_path, "w", compression=zipfile.ZIP_DEFLATED) as outer:
        outer.writestr("pkg/inner.zip", inner_path.read_bytes())
    entries = scanner.enumerate_target(
        scanner.ScanTarget(kind="artifact", label="outer", path=outer_path)
    )
    rules = scanner.load_rules()
    findings: list[Any] = []
    for entry in entries:
        findings.extend(scanner.scan_bytes(entry, rules, target_label="outer"))
    rule_ids = {f.rule_id for f in findings}
    assert "PRIV-CRED-001" in rule_ids


# ---------------------------------------------------------------------------
# CLI end-to-end: report file, exit codes, stderr never leaks the canary
# ---------------------------------------------------------------------------


def test_cli_writes_a_canonical_json_out_report(
    scanner: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _clean_git_source_tree(tmp_path / "clean-cli-report")
    report_path = tmp_path / "report.json"
    rc = scanner.main(["--source-tree", str(root), "--json-out", str(report_path)])
    assert rc == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schema"] == "yoetz.public-boundary-report/1"
    assert report["finding_count"] == 0


def test_cli_blocks_and_never_prints_the_canary_bytes(
    scanner: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "dirty-repo"
    (root).mkdir()
    canary = "sk-canary-8f24c9b1a7"
    (root / "leak.py").write_text(f"TOKEN = '{canary}'\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "dirty"], cwd=root, check=True)

    canary_file = tmp_path / "canary.bin"
    canary_file.write_bytes(canary.encode("utf-8"))
    rc = scanner.main(["--source-tree", str(root), "--canary-file", str(canary_file)])
    assert rc == 1
    captured = capsys.readouterr()
    assert canary not in captured.out
    assert canary not in captured.err


def test_sdist_can_be_enumerated_via_tarfile_directly(built_dist: _BuiltDist) -> None:
    with tarfile.open(built_dist.sdist, mode="r:gz") as archive:
        names = archive.getnames()
    assert any(name.endswith("src/yoetz/__init__.py") for name in names)
