"""Contract tests for the workflow-owned typed release-input builder."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Final

_ROOT: Final = Path(__file__).resolve().parents[2]
_BUILDER: Final = _ROOT / "scripts" / "build_release_inputs.py"
_GENERATOR: Final = _ROOT / "scripts" / "generate_release_evidence.py"
_GATES: Final = (
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


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    root = tmp_path / "inputs"
    artifacts = root / "candidate-0.1.0"
    artifacts.mkdir(parents=True)
    (artifacts / "yoetz-0.1.0.whl").write_bytes(b"wheel")
    (artifacts / "yoetz-0.1.0.tar.gz").write_bytes(b"sdist")
    digest = "a" * 64
    matrix = root / "capability-matrix.json"
    matrix.write_text(
        json.dumps(
            {
                "schema": "yoetz.capability-matrix/1",
                "candidate_artifact_digest": f"sha256:{digest}",
                "cells": [
                    {
                        "capability_family": "mcp",
                        "external_version": "1.28.1",
                        "platform": "linux_x86_64",
                        "requirement_id": "initialize",
                        "status": "supported",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    limitations = root / "limitations.json"
    limitations.write_text(
        json.dumps(
            {
                "schema": "yoetz.release-known-limitations/1",
                "limitations": [{"code": "L-001", "description": "test limitation"}],
            }
        ),
        encoding="utf-8",
    )
    return artifacts, matrix, limitations, digest


def _run_builder(
    tmp_path: Path, records: list[dict[str, object]], *, output: Path | None = None
) -> subprocess.CompletedProcess[str]:
    artifacts, matrix, limitations, digest = _inputs(tmp_path)
    manifest = output or tmp_path / "release-inputs.json"
    command = [
        sys.executable,
        str(_BUILDER),
        "--candidate-version",
        "0.1.0",
        "--candidate-tag",
        "v0.1.0",
        "--candidate-commit",
        "0" * 40,
        "--artifact-root",
        str(artifacts),
        "--candidate-digest",
        digest,
        "--support-matrix",
        str(matrix),
        "--known-limitations",
        str(limitations),
    ]
    for record in records:
        command.extend(("--gate-record", json.dumps(record)))
    command.extend(("--output", str(manifest)))
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(_ROOT / "src") + os.pathsep + environment.get("PYTHONPATH", "")
    return subprocess.run(
        command, cwd=_ROOT, env=environment, text=True, capture_output=True, check=False
    )


def _passing_records() -> list[dict[str, object]]:
    return [
        {
            "name": name,
            "source_results": ["success"],
            "input_digests": ["sha256:" + name.encode().hex().ljust(64, "0")[:64]],
        }
        for name in _GATES
    ]


def test_builder_output_drives_the_real_evidence_generator(tmp_path: Path) -> None:
    manifest = tmp_path / "release-inputs.json"
    built = _run_builder(tmp_path, _passing_records(), output=manifest)
    assert built.returncode == 0, built.stderr
    document = json.loads(manifest.read_text(encoding="utf-8"))
    assert set(document["gates"]) == set(_GATES)
    assert document["support_matrix_cells"] == [
        {"label": "mcp:1.28.1:linux_x86_64:initialize", "status": "supported"}
    ]

    generated = subprocess.run(
        [
            sys.executable,
            str(_GENERATOR),
            "--input-manifest",
            str(manifest),
            "--output-dir",
            str(tmp_path / "evidence"),
            "--write",
        ],
        cwd=_ROOT,
        env={**os.environ, "PYTHONPATH": str(_ROOT / "src")},
        text=True,
        capture_output=True,
        check=False,
    )
    assert generated.returncode == 0, generated.stderr


def test_one_incomplete_gate_causes_the_real_generator_to_fail(tmp_path: Path) -> None:
    records = _passing_records()
    records[-1]["source_results"] = ["skipped"]
    manifest = tmp_path / "release-inputs.json"
    built = _run_builder(tmp_path, records, output=manifest)
    assert built.returncode == 0, built.stderr
    generated = subprocess.run(
        [
            sys.executable,
            str(_GENERATOR),
            "--input-manifest",
            str(manifest),
            "--output-dir",
            str(tmp_path / "evidence"),
            "--write",
        ],
        cwd=_ROOT,
        env={**os.environ, "PYTHONPATH": str(_ROOT / "src")},
        text=True,
        capture_output=True,
        check=False,
    )
    assert generated.returncode == 1
    assert "one or more required gates did not pass" in generated.stderr


def test_builder_rejects_missing_duplicate_and_unknown_gate_records(tmp_path: Path) -> None:
    missing = _run_builder(tmp_path / "missing", _passing_records()[:-1])
    assert missing.returncode == 1
    assert "gate_record_missing" in missing.stderr

    duplicate_records = _passing_records()
    duplicate_records.append(duplicate_records[0])
    duplicate = _run_builder(tmp_path / "duplicate", duplicate_records)
    assert duplicate.returncode == 1
    assert "gate_record_duplicate" in duplicate.stderr

    unknown_records = _passing_records()
    unknown_records[0]["name"] = "unknown_gate"
    unknown = _run_builder(tmp_path / "unknown", unknown_records)
    assert unknown.returncode == 1
    assert "gate_record_unknown" in unknown.stderr
