"""Static contract coverage for the tagged release evidence assembly workflow."""

from __future__ import annotations

from pathlib import Path
from typing import Final

_ROOT: Final = Path(__file__).resolve().parents[2]
_WORKFLOW: Final = _ROOT / ".github" / "workflows" / "release.yml"
_REUSABLE_WORKFLOWS: Final = (
    _ROOT / ".github" / "workflows" / "capability.yml",
    _ROOT / ".github" / "workflows" / "nightly-fault.yml",
    _ROOT / ".github" / "workflows" / "security-privacy.yml",
)


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
    assert 'mkdir -p "${{ runner.temp }}/release-evidence/${{' not in assembly
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


def test_candidate_digest_is_path_independent_and_checksum_backed() -> None:
    workflow = _WORKFLOW.read_text(encoding="utf-8")
    build = workflow.split("  build-candidate:\n", 1)[1].split(
        "  # ---------------------------------------------------------------------------------------------\n  # build-npm-launcher",
        1,
    )[0]

    assert "sha256sum ./*.whl ./*.tar.gz > SHA256SUMS" in build
    assert "sha256sum --check SHA256SUMS" in build
    assert 'sha256sum "${{ runner.temp }}/dist/SHA256SUMS"' in build
    assert 'find "${{ runner.temp }}/dist"' not in build


def test_reusable_release_workflows_select_supplied_artifacts_by_input() -> None:
    for path in _REUSABLE_WORKFLOWS:
        workflow = path.read_text(encoding="utf-8")
        assert "github.event_name == 'workflow_call'" not in workflow
        assert "github.event_name != 'workflow_call'" not in workflow
        assert "if: inputs.candidate-digest != ''" in workflow
        assert "if: inputs.candidate-digest == ''" in workflow


def test_workflows_do_not_declare_step_level_permissions() -> None:
    for path in (_WORKFLOW, *_REUSABLE_WORKFLOWS):
        assert "\n        permissions:\n" not in path.read_text(encoding="utf-8")


def test_empty_soak_selection_is_disclosed_without_claiming_coverage() -> None:
    workflow = (_ROOT / ".github" / "workflows" / "nightly-fault.yml").read_text(encoding="utf-8")

    assert "No soak-marked replay/resource tests are checked in" in workflow
    assert "no 1M-entry coverage is claimed" in workflow
    assert '"soak_coverage": os.environ.get(' in workflow


def test_platform_verifiers_split_suites_and_bound_linux_alpha_claims() -> None:
    workflow = _WORKFLOW.read_text(encoding="utf-8")
    linux = workflow.split("  verify-linux-x86_64:\n", 1)[1].split(
        "  # ---------------------------------------------------------------------------------------------\n  # verify-macos-arm64",
        1,
    )[0]
    macos = workflow.split("  verify-macos-arm64:\n", 1)[1].split(
        "  # ---------------------------------------------------------------------------------------------\n  # fault-release-profile",
        1,
    )[0]

    assert 'export HOME="${RUNNER_TEMP}/yoetz-home"' not in linux
    assert 'export HOME="${RUNNER_TEMP}/yoetz-home"' not in macos
    for verifier in (linux, macos):
        assert "pytest tests/packaging \\" in verifier
        assert "pytest tests/subprocess \\" in verifier
        assert "pytest tests/integration \\" in verifier
        assert "export YOETZ_DENY_NETWORK" not in verifier
        assert "export YOETZ_CANDIDATE_PYTHON" not in verifier
        assert verifier.count("YOETZ_DENY_NETWORK=1 \\") == 2
        assert (
            verifier.count('YOETZ_CANDIDATE_PYTHON="${{ runner.temp }}/verify-venv/bin/python" \\')
            == 2
        )
        assert '--runtime-tree "$installed_root"' in verifier
    assert "test_approved_check_stale_when_digest_changes" in linux
    assert "test_approved_true_check_succeeds_in_sandbox" in linux
    assert "test_approved_check_stale_when_digest_changes" not in macos

    limitations = (_ROOT / "release" / "known-limitations.json").read_text(encoding="utf-8")
    release_notes = (_ROOT / "docs" / "releases" / "v0.1.0.md").read_text(encoding="utf-8")
    assert "linux_approved_command_sandbox_unavailable" in limitations
    assert "approved external-command checks fail closed" in release_notes


def test_generated_evidence_uses_evidence_scanner_mode() -> None:
    release = _WORKFLOW.read_text(encoding="utf-8")
    capability = (_ROOT / ".github" / "workflows" / "capability.yml").read_text(encoding="utf-8")
    fault = (_ROOT / ".github" / "workflows" / "nightly-fault.yml").read_text(encoding="utf-8")

    assert '--evidence-dir "${{ runner.temp }}/release-evidence"' in release
    assert '--evidence-dir "${{ runner.temp }}/release-evidence"' in capability
    assert '--evidence-dir "${RUNNER_TEMP}/evidence"' in fault
    assert "evidence-scanned" not in fault.split("  replay-and-resource:\n", 1)[1]

    download = capability.split(
        "      - name: Download CapabilityEvidence records (exclude candidate artifact JSON)\n",
        1,
    )[1].split("\n      - name:", 1)[0]
    assert "if-no-files-found" not in download
