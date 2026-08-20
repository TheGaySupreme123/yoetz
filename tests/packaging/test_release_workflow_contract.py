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
    assert "candidate_ref:" in workflow
    assert "dry-run candidate ${candidate} is not current origin/main ${protected}" in workflow
    assert "scripts/build_release_inputs.py" in assembly
    assert "generate_release_evidence.py" in assembly
    assert 'pattern: "candidate-*,nightly-*,capability-*,security-*"' not in assembly
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


def test_npm_release_is_built_once_published_after_pypi_and_download_verified() -> None:
    workflow = _WORKFLOW.read_text(encoding="utf-8")
    build = workflow.split("  build-npm-launcher:\n", 1)[1].split(
        "  # ---------------------------------------------------------------------------------------------\n  # verify-linux",
        1,
    )[0]
    publish = workflow.split("  publish-npm:\n", 1)[1].split(
        "  # ---------------------------------------------------------------------------------------------\n  # publish-github-release",
        1,
    )[0]
    verify = workflow.split("  verify-npm-published:\n", 1)[1].split(
        "  # ---------------------------------------------------------------------------------------------\n  # release-required",
        1,
    )[0]

    assert "npm pack ./support/npm-launcher" in build
    assert "npm install --global --ignore-scripts npm@12.0.1" in build
    assert "npm-candidate-${{ needs.validate-release-source.outputs.version }}" in build
    assert "NPM_SHA256SUMS" in build
    assert '"npm": "12.0.1"' in build

    assert "publish-pypi" in "\n".join(publish.split("\n", 8)[0:8])
    assert "environment: npm" in publish
    assert "id-token: write" in publish
    assert "npm install --global --ignore-scripts npm@12.0.1" in publish
    assert "npm publish" in publish
    assert "npm pack" not in publish
    assert "skip-existing" not in publish

    assert "npm-published.tgz" in verify
    assert "cmp -s" in verify
    assert "yoetz==${{ needs.validate-release-source.outputs.version }} version --json" in verify


def test_tag_workflow_rejects_missing_or_drifted_hosted_schema_bytes() -> None:
    workflow = _WORKFLOW.read_text(encoding="utf-8")
    verify = workflow.split("  verify-schema-site:\n", 1)[1].split(
        "  # ---------------------------------------------------------------------------------------------\n  # verify-published",
        1,
    )[0]

    assert "find schemas -type f -print0 | sort -z" in verify
    assert "curl --fail --silent --show-error --location" in verify
    assert "cmp -s" in verify
    assert "|| true" not in verify
