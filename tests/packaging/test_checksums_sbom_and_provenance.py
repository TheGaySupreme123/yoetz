"""Release evidence integrity: checksums, CycloneDX SBOM, and honest provenance/signature wording.

Builds the candidate sdist/wheel, independently hashes them, generates a CycloneDX SBOM from the
locked dependency set, and drives the real ``scripts/generate_release_evidence.py`` end to end
(write, check, and mutation-detection) to prove the release evidence binds the exact candidate
bytes and never overstates verification (checksums are not signatures; ``signature_status`` is
always ``not_provided`` at v0.1).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Final

import pytest

_REPO_ROOT: Final = Path(__file__).resolve().parents[2]
_GENERATOR: Final = _REPO_ROOT / "scripts" / "generate_release_evidence.py"
_BUILD_TIMEOUT: Final = 120
_REQUIRED_GATES: Final = (
    "clean_source_identity",
    "artifact_checksums",
    "resource_manifest_parity",
    "unit_tests",
    "property_tests",
    "integration_tests",
    "conformance_tests",
    "packaging_tests",
    "security_privacy_scan",
    "capability_matrix",
    "dependency_vulnerability_license",
    "clean_install",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _uv_env() -> dict[str, str]:
    """Environment for ``uv export`` runs that select their lock mode by flag.

    CI exports ``UV_LOCKED=1`` for the ambient ``uv run --locked`` steps, and uv rejects that
    together with the ``--frozen`` these exports pass, exiting 2 before doing any work. The lock
    mode belongs to the command, so drop the inherited variable rather than let the surrounding
    workflow decide it.
    """

    env = dict(os.environ)
    env.pop("UV_LOCKED", None)
    env.pop("UV_FROZEN", None)
    return env


def _build_candidate(out_dir: Path) -> tuple[Path, Path]:
    environment = dict(os.environ)
    environment["TZ"] = "UTC"
    environment["SOURCE_DATE_EPOCH"] = "1700000000"
    subprocess.run(  # noqa: S603 - fixed argv, no shell, trusted local uv binary
        [
            "uv",
            "build",
            "--no-sources",
            "--offline",
            "--no-create-gitignore",
            "-o",
            str(out_dir),
        ],
        cwd=_REPO_ROOT,
        env=environment,
        capture_output=True,
        check=True,
        timeout=_BUILD_TIMEOUT,
    )
    entries = sorted(out_dir.iterdir())
    sdists = [entry for entry in entries if entry.name.endswith(".tar.gz")]
    wheels = [entry for entry in entries if entry.name.endswith(".whl")]
    assert len(sdists) == 1
    assert len(wheels) == 1
    return sdists[0], wheels[0]


def _passing_gate_manifest() -> dict[str, dict[str, object]]:
    return {
        name: {"status": "pass", "reason_code": "ok", "input_digests": [f"sha256:{name}"]}
        for name in _REQUIRED_GATES
    }


def _write_release_input_manifest(
    staging: Path, *, sdist: Path, wheel: Path, gates: dict[str, dict[str, object]]
) -> Path:
    manifest = {
        "candidate_version": "0.1.0",
        "candidate_tag": "v0.1.0",
        "candidate_commit": "0" * 40,
        "artifacts": [
            {"path": sdist.name, "sha256": f"sha256:{_sha256(sdist)}"},
            {"path": wheel.name, "sha256": f"sha256:{_sha256(wheel)}"},
        ],
        "gates": gates,
        "support_matrix_cells": [{"label": "macos-arm64", "status": "supported"}],
        "known_limitations": [{"code": "L-001", "description": "test-only synthetic limitation"}],
    }
    manifest_path = staging / "release-inputs.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


@pytest.fixture(scope="module")
def candidate_dist(tmp_path_factory: pytest.TempPathFactory) -> Path:
    dist_dir = tmp_path_factory.mktemp("checksums-sbom-dist")
    _build_candidate(dist_dir)
    return dist_dir


def _run_generator(arguments: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell, trusted local interpreter
        [sys.executable, str(_GENERATOR), *arguments],
        cwd=_REPO_ROOT,
        capture_output=True,
        check=False,
        timeout=60,
    )


# --------------------------------------------------------------------------
# Checksums
# --------------------------------------------------------------------------


def test_independent_sha256sums_are_lowercase_and_exactly_once_per_artifact(
    candidate_dist: Path,
) -> None:
    artifacts = sorted(candidate_dist.iterdir())
    lines = [f"{_sha256(artifact)}  {artifact.name}" for artifact in artifacts]
    for artifact, line in zip(artifacts, lines, strict=True):
        digest_field = line.split("  ", 1)[0]
        assert digest_field == digest_field.lower()
        assert len(digest_field) == 64
        assert artifact.name in line
    names = [artifact.name for artifact in artifacts]
    assert len(names) == len(set(names))


def test_sha256sum_c_verifies_the_generated_checksum_file(
    candidate_dist: Path, tmp_path: Path
) -> None:
    """Prove the documented offline verification command actually verifies the real artifacts."""

    staging = tmp_path / "verify-root"
    staging.mkdir()
    lines: list[str] = []
    for artifact in sorted(candidate_dist.iterdir()):
        target = staging / artifact.name
        target.write_bytes(artifact.read_bytes())
        lines.append(f"{_sha256(artifact)}  {artifact.name}")
    (staging / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")

    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell, trusted local coreutils binary
        ["sha256sum", "-c", "SHA256SUMS"],
        cwd=staging,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout.decode("utf-8", "replace")


def test_sha256sum_c_fails_on_a_mutated_downloaded_artifact(
    candidate_dist: Path, tmp_path: Path
) -> None:
    staging = tmp_path / "verify-mutated"
    staging.mkdir()
    lines: list[str] = []
    for artifact in sorted(candidate_dist.iterdir()):
        target = staging / artifact.name
        target.write_bytes(artifact.read_bytes())
        lines.append(f"{_sha256(artifact)}  {artifact.name}")
    (staging / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")

    mutated_target = sorted(staging.glob("*.whl"))[0]
    data = bytearray(mutated_target.read_bytes())
    data[0] ^= 0xFF
    mutated_target.write_bytes(bytes(data))

    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell, trusted local coreutils binary
        ["sha256sum", "-c", "SHA256SUMS"],
        cwd=staging,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode != 0


# --------------------------------------------------------------------------
# CycloneDX SBOM
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sbom_document() -> Any:
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell, trusted local uv binary
        [
            "uv",
            "export",
            "--format",
            "cyclonedx1.5",
            "--no-emit-project",
            "--no-dev",
            "--offline",
            "--frozen",
        ],
        cwd=_REPO_ROOT,
        env=_uv_env(),
        capture_output=True,
        check=True,
        timeout=60,
    )
    return json.loads(completed.stdout)


def test_sbom_is_well_formed_cyclonedx(sbom_document: Any) -> None:
    assert sbom_document["bomFormat"] == "CycloneDX"
    assert sbom_document["specVersion"] == "1.5"
    assert isinstance(sbom_document["components"], list)
    assert sbom_document["components"]


def test_sbom_has_no_duplicate_or_unknown_component(sbom_document: Any) -> None:
    components = sbom_document["components"]
    identities = [(component["name"], component["version"]) for component in components]
    assert len(identities) == len(set(identities)), "duplicate SBOM component"
    for component in components:
        assert component.get("type") == "library"
        assert component.get("purl", "").startswith("pkg:pypi/")


def test_sbom_reconciles_with_the_same_locked_target_exported_as_requirements(
    sbom_document: Any,
) -> None:
    """The SBOM and a requirements export of the identical base target derive from the same
    ``uv.lock``; their resolved distribution sets must be exactly the same one-to-one set.
    """

    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell, trusted local uv binary
        [
            "uv",
            "export",
            "--format",
            "requirements.txt",
            "--no-emit-project",
            "--no-dev",
            "--no-annotate",
            "--no-header",
            "--offline",
            "--frozen",
        ],
        cwd=_REPO_ROOT,
        env=_uv_env(),
        capture_output=True,
        check=True,
        timeout=60,
    )
    requirements_names = {
        line.split("==", 1)[0].strip().lower().replace("_", "-").replace(".", "-")
        for line in completed.stdout.decode("utf-8").splitlines()
        if line and not line.startswith((" ", "#"))
    }
    sbom_names = {
        str(component["name"]).lower().replace("_", "-").replace(".", "-")
        for component in sbom_document["components"]
    }
    assert sbom_names == requirements_names


def test_sbom_includes_the_apsw_native_sqlite_bearing_component(
    sbom_document: Any,
) -> None:
    names = {component["name"] for component in sbom_document["components"]}
    assert "apsw" in names


# --------------------------------------------------------------------------
# Release evidence: generation, checking, and mutation-detection
# --------------------------------------------------------------------------


@pytest.fixture()
def release_staging(tmp_path: Path, candidate_dist: Path) -> Path:
    staging = tmp_path / "release-staging"
    staging.mkdir()
    for artifact in candidate_dist.iterdir():
        (staging / artifact.name).write_bytes(artifact.read_bytes())
    return staging


def test_generator_writes_the_exact_five_bundle_files(release_staging: Path) -> None:
    sdist = next(release_staging.glob("*.tar.gz"))
    wheel = next(release_staging.glob("*.whl"))
    manifest_path = _write_release_input_manifest(
        release_staging, sdist=sdist, wheel=wheel, gates=_passing_gate_manifest()
    )
    output_dir = release_staging / "evidence"

    completed = _run_generator(
        ["--input-manifest", str(manifest_path), "--output-dir", str(output_dir), "--write"]
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")

    produced = sorted(entry.name for entry in output_dir.iterdir())
    assert produced == [
        "SHA256SUMS",
        "VERIFY.md",
        "known-limitations.md",
        "release-evidence.json",
        "support-matrix.md",
    ]


def test_generator_check_mode_is_idempotent_against_a_written_bundle(
    release_staging: Path,
) -> None:
    sdist = next(release_staging.glob("*.tar.gz"))
    wheel = next(release_staging.glob("*.whl"))
    manifest_path = _write_release_input_manifest(
        release_staging, sdist=sdist, wheel=wheel, gates=_passing_gate_manifest()
    )
    output_dir = release_staging / "evidence"
    write_result = _run_generator(
        ["--input-manifest", str(manifest_path), "--output-dir", str(output_dir), "--write"]
    )
    assert write_result.returncode == 0

    check_result = _run_generator(
        ["--input-manifest", str(manifest_path), "--output-dir", str(output_dir), "--check"]
    )
    assert check_result.returncode == 0, check_result.stderr.decode("utf-8", "replace")


def test_release_evidence_json_never_fabricates_a_signature(release_staging: Path) -> None:
    sdist = next(release_staging.glob("*.tar.gz"))
    wheel = next(release_staging.glob("*.whl"))
    manifest_path = _write_release_input_manifest(
        release_staging, sdist=sdist, wheel=wheel, gates=_passing_gate_manifest()
    )
    output_dir = release_staging / "evidence"
    result = _run_generator(
        ["--input-manifest", str(manifest_path), "--output-dir", str(output_dir), "--write"]
    )
    assert result.returncode == 0

    evidence = json.loads((output_dir / "release-evidence.json").read_bytes())
    assert evidence["signature_status"] == "not_provided"
    assert evidence["eligible"] is True
    assert evidence["schema"] == "yoetz.release-evidence/1"


def test_release_evidence_subject_digests_equal_the_actual_artifact_bytes(
    release_staging: Path,
) -> None:
    sdist = next(release_staging.glob("*.tar.gz"))
    wheel = next(release_staging.glob("*.whl"))
    manifest_path = _write_release_input_manifest(
        release_staging, sdist=sdist, wheel=wheel, gates=_passing_gate_manifest()
    )
    output_dir = release_staging / "evidence"
    result = _run_generator(
        ["--input-manifest", str(manifest_path), "--output-dir", str(output_dir), "--write"]
    )
    assert result.returncode == 0

    evidence = json.loads((output_dir / "release-evidence.json").read_bytes())
    by_path = {entry["path"]: entry["sha256"] for entry in evidence["artifacts"]}
    assert by_path[sdist.name] == f"sha256:{_sha256(sdist)}"
    assert by_path[wheel.name] == f"sha256:{_sha256(wheel)}"

    checksums_text = (output_dir / "SHA256SUMS").read_text(encoding="utf-8")
    assert f"{_sha256(sdist)}  {sdist.name}" in checksums_text
    assert f"{_sha256(wheel)}  {wheel.name}" in checksums_text


def test_a_single_failing_gate_makes_the_release_ineligible(release_staging: Path) -> None:
    sdist = next(release_staging.glob("*.tar.gz"))
    wheel = next(release_staging.glob("*.whl"))
    gates = _passing_gate_manifest()
    gates["security_privacy_scan"] = {
        "status": "fail",
        "reason_code": "boundary_scan_hit",
        "input_digests": [],
    }
    manifest_path = _write_release_input_manifest(
        release_staging, sdist=sdist, wheel=wheel, gates=gates
    )
    output_dir = release_staging / "evidence-failing"

    completed = _run_generator(
        ["--input-manifest", str(manifest_path), "--output-dir", str(output_dir), "--write"]
    )
    assert completed.returncode == 1
    assert not output_dir.exists()


def test_a_missing_required_gate_is_incomplete_not_silently_passing(
    release_staging: Path,
) -> None:
    sdist = next(release_staging.glob("*.tar.gz"))
    wheel = next(release_staging.glob("*.whl"))
    gates = _passing_gate_manifest()
    del gates["capability_matrix"]
    manifest_path = _write_release_input_manifest(
        release_staging, sdist=sdist, wheel=wheel, gates=gates
    )
    output_dir = release_staging / "evidence-incomplete"

    completed = _run_generator(
        ["--input-manifest", str(manifest_path), "--output-dir", str(output_dir), "--write"]
    )
    assert completed.returncode == 1
    assert not output_dir.exists()


def test_a_wrong_declared_artifact_digest_is_rejected_before_any_output(
    release_staging: Path,
) -> None:
    sdist = next(release_staging.glob("*.tar.gz"))
    wheel = next(release_staging.glob("*.whl"))
    manifest = {
        "candidate_version": "0.1.0",
        "candidate_tag": "v0.1.0",
        "candidate_commit": "0" * 40,
        "artifacts": [
            {"path": sdist.name, "sha256": "sha256:" + "0" * 64},
            {"path": wheel.name, "sha256": f"sha256:{_sha256(wheel)}"},
        ],
        "gates": _passing_gate_manifest(),
        "support_matrix_cells": [],
        "known_limitations": [],
    }
    manifest_path = release_staging / "release-inputs-bad-digest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output_dir = release_staging / "evidence-bad-digest"

    completed = _run_generator(
        ["--input-manifest", str(manifest_path), "--output-dir", str(output_dir), "--write"]
    )
    assert completed.returncode == 1
    assert b"artifact_digest_mismatch" in completed.stderr
    assert not output_dir.exists()


def test_a_mutated_published_bundle_file_fails_check(release_staging: Path) -> None:
    sdist = next(release_staging.glob("*.tar.gz"))
    wheel = next(release_staging.glob("*.whl"))
    manifest_path = _write_release_input_manifest(
        release_staging, sdist=sdist, wheel=wheel, gates=_passing_gate_manifest()
    )
    output_dir = release_staging / "evidence-mutate-after"
    write_result = _run_generator(
        ["--input-manifest", str(manifest_path), "--output-dir", str(output_dir), "--write"]
    )
    assert write_result.returncode == 0

    checksums_path = output_dir / "SHA256SUMS"
    checksums_path.write_text(checksums_path.read_text(encoding="utf-8") + "tampered\n")

    check_result = _run_generator(
        ["--input-manifest", str(manifest_path), "--output-dir", str(output_dir), "--check"]
    )
    assert check_result.returncode == 1
    assert b"bundle_file_mismatch" in check_result.stderr
