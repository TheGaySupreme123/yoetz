"""Static contract coverage for the tagged release evidence assembly workflow."""

from __future__ import annotations

from pathlib import Path
from typing import Final

_ROOT: Final = Path(__file__).resolve().parents[2]
_WORKFLOW: Final = _ROOT / ".github" / "workflows" / "release.yml"


def test_dry_run_retains_builder_backed_release_evidence_bundle() -> None:
    workflow = _WORKFLOW.read_text(encoding="utf-8")
    assembly = workflow.split("  assemble-release-evidence:\n", 1)[1].split(
        "  # ---------------------------------------------------------------------------------------------\n  # approve-publication",
        1,
    )[0]

    assert "workflow_dispatch:" in workflow
    assert "DRY_RUN: ${{ github.event_name == 'workflow_dispatch' }}" in workflow
    assert "workflow_dispatch is dry-run only; dry_run must be true" in workflow
    assert "scripts/build_release_inputs.py" in assembly
    assert "generate_release_evidence.py" in assembly
    assert "python -c" not in assembly
    assert "--candidate-commit" in assembly
    assert (
        '--artifact-root "${{ runner.temp }}/inputs/candidate-${{ needs.validate-release-source.outputs.version }}"'
        in assembly
    )
    assert (
        '--support-matrix "${{ runner.temp }}/inputs/capability-matrix/capability-matrix.json"'
        in assembly
    )
    assert assembly.count("--gate-record") == 12
    for gate in (
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
    ):
        assert f'"name":"{gate}"' in assembly

    upload = assembly.split("      - name: Upload release evidence bundle\n", 1)[1]
    upload = upload.split(
        "\n  # ---------------------------------------------------------------------------------------------",
        1,
    )[0]
    assert "if:" not in upload
    assert "path: ${{ runner.temp }}/release-evidence" in upload
    assert "retention-days: 90" in upload
