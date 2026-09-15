# Code review: PR 736

**PR:** [fix(release): use private state for integration verification](https://github.com/TheGaySupreme123/yoetz/pull/736)
**Branch:** `codex/release-021-test-root`
**Head:** `4f429b65e36733ec5c9f9ef5d3a30aa6249d7dd5`
**Merge:** `29083194dd309c624a7a8b4c0c91e3a400e98483` (into `main`)
**Author:** TheGaySupreme123
**Reviewer:** Cursor agent
**Date:** 2026-09-15

Last push from TheGaySupreme123 to `main` as of this review. Diff is 12 insertions across `.github/workflows/release.yml` and `tests/packaging/test_release_workflow_contract.py`. Production path-safety code is unchanged. Official rehearsal [34863343861](https://github.com/TheGaySupreme123/yoetz/actions/runs/34863343861) passed after merge, including Linux, macOS, and evidence assembly. Fixes #735; refs #733.

No live Yoetz ledger or receipt was available for this review.

**Verdict:** merge-appropriate. The production guards stay fail-closed; only the release verifier’s pytest state root moved. I would not block on this. The notes below are hygiene, not correctness. The PR was already merged; this is an independent written review.

---

## What holds

- Production `path_shared_temp` / `path_in_repository` checks were not weakened to make CI green.
- Both `verify-linux-x86_64` and `verify-macos-arm64` get the same canonical `RUNNER_TEMP/yoetz-integration` base.
- `--basetemp` is a dedicated leaf, not `${{ runner.temp }}` itself. Pytest deletes `--basetemp` if it exists; pointing it at the runner temp root would wipe the downloaded candidate under `dist/`.
- The workflow contract test splits Linux and macOS job text separately, then asserts the Path snippet and `--basetemp` in each slice.
- GitHub’s default shell is `bash -eo pipefail`, so a missing `RUNNER_TEMP` or a failed Python one-liner fails the step.
- The 14 rehearsal failures were production-composition tests placing vault/bundle state on pytest `tmp_path` under `/tmp/pytest-of-runner`. `chmod 0700` on the leaf does not satisfy the shared-temp classifier.

---

## The change

Linux and macOS verifiers now compute a runner-private pytest base and pass it only to `tests/integration`.

```bash
# .github/workflows/release.yml — verify-linux-x86_64 (origin/main)
# Production vault tests must not use pytest's shared /tmp base or a repository path.
# Resolve runner temp first so macOS aliases cannot enter the private state path.
release_test_root="$(.venv/bin/python -c 'import os; from pathlib import Path; print((Path(os.environ["RUNNER_TEMP"]) / "yoetz-integration").resolve())')"

YZCI_DENY_NETWORK=1 \
YOETZ_CANDIDATE_PYTHON="${{ runner.temp }}/verify-venv/bin/python" \
  uv run --locked pytest tests/integration \
  --basetemp "$release_test_root" \
  --deselect tests/integration/observation/test_acceptance_scenarios.py::test_approved_check_stale_when_digest_changes \
  --deselect tests/integration/observation/test_production_composition.py::test_approved_true_check_succeeds_in_sandbox \
  -m "not fault and not soak and not live" -q --timeout=900
```

macOS is the same compute + `--basetemp`, without those two Linux deselections:

```bash
# .github/workflows/release.yml — verify-macos-arm64 (origin/main)
release_test_root="$(.venv/bin/python -c 'import os; from pathlib import Path; print((Path(os.environ["RUNNER_TEMP"]) / "yoetz-integration").resolve())')"

YZCI_DENY_NETWORK=1 \
YOETZ_CANDIDATE_PYTHON="${{ runner.temp }}/verify-venv/bin/python" \
  uv run --locked pytest tests/integration \
  --basetemp "$release_test_root" \
  -m "not fault and not soak and not live" -q --timeout=900
```

Contract lock, once per platform job slice:

```python
# tests/packaging/test_release_workflow_contract.py
for verifier in (linux, macos):
    assert "pytest tests/integration \\" in verifier
    # Actual vault composition rejects shared /tmp even when its leaf directory is 0700.
    assert '(Path(os.environ["RUNNER_TEMP"]) / "yoetz-integration").resolve()' in verifier
    integration = verifier.split("pytest tests/integration", 1)[1]
    assert '--basetemp "$release_test_root"' in integration.split("\n      - name:", 1)[0]
```

`.resolve()` is the macOS `/tmp` → `/private/tmp` defense. `/private/tmp` is not named in `fixed_shared_temps`, but `Path("/tmp").resolve()` is `/private/tmp`, so the existing guard still covers the alias.

---

## Why `/tmp` was illegal

Fixed shared-temp roots, then reject anything underneath them. A `0700` leaf does not help.

```python
# src/yoetz/config/paths.py
def _default_probe() -> _PathProbe:
    return _PathProbe(
        platform=sys.platform,
        effective_uid=effective_uid,
        home=Path.home(),
        shared_temp=Path(tempfile.gettempdir()),
        fixed_shared_temps=(Path("/tmp"), Path("/var/tmp"), Path("/dev/shm")),
        ...
    )


def _check_shared_temp(path: Path, probe: _PathProbe) -> None:
    for root in probe.fixed_shared_temps:
        try:
            resolved_root = root.resolve(strict=False)
        except OSError:
            resolved_root = root
        if _is_beneath(path, resolved_root):
            raise PathSafetyError("path_shared_temp")

    try:
        shared_temp = probe.shared_temp.resolve(strict=False)
        temp_mode = stat.S_IMODE(shared_temp.stat().st_mode)
    except OSError:
        probe.diagnostic("shared_temp_probe_failed")
        return
    if temp_mode & 0o077 and _is_beneath(path, shared_temp):
        raise PathSafetyError("path_shared_temp")
```

Vault/bundle placement hits that in a fixed order: symlink → owner/mode → shared temp → repository → sync folder → network filesystem.

```python
# src/yoetz/config/paths.py
def verify_private_local_bundle(path: Path, *, _probe: _PathProbe | None = None) -> None:
    probe = _probe_or_default(_probe)
    _reject_symlink_components(path)
    resolved = path.resolve(strict=False)
    _check_owner_and_mode(resolved, probe)
    _check_shared_temp(resolved, probe)
    _check_repository(resolved)
    _check_sync_folder(resolved, probe)
    _check_network_filesystem(resolved, probe)
    _record_location_diagnostic(resolved, probe)
```

Unit test that encodes the 0700-under-shared-root refusal:

```python
# tests/unit/config/test_isolated_root.py
def test_shared_temp_root_is_refused(tmp_path, monkeypatch):
    shared = tmp_path / "shared-temp"
    shared.mkdir(mode=0o700)
    root = shared / "root"
    root.mkdir(mode=0o700)
    probe = _probe(tmp_path, monkeypatch, fixed_shared_temps=(shared,))
    monkeypatch.setenv(ISOLATED_ROOT_ENV, str(root))

    with pytest.raises(PathSafetyError) as caught:
        config_file_path(_probe=probe)
    assert caught.value.reason_code == "path_shared_temp"
```

---

## The tests that failed

They put real production-composition state on pytest `tmp_path`. Default pytest `tmp_path` lives under `/tmp/pytest-of-runner/...`.

```python
# tests/integration/service/test_consent_vault_initialize_composition.py
async def _production_daemon(tmp_path: Path, *, pristine: bool) -> ServiceDaemon:
    root = tmp_path / "data"
    metadata = tmp_path / "state"
    root.mkdir(mode=0o700, exist_ok=True)
    metadata.mkdir(mode=0o700, exist_ok=True)
    paths = daemon_module._ProductionPaths(root, metadata / "service-generation.json", ...)
    composition = await daemon_module._production_composition(
        _config=YoetzConfig(), _paths=paths, ...
    )


async def test_failed_initialize_discards_staged_credential_and_retry_succeeds(
    tmp_path, runtime_directory
):
    tmp_path.chmod(0o700)
    store = _approved_store(tmp_path / "data", backend)
    daemon = await _production_daemon(tmp_path, pristine=False)
```

```python
# tests/integration/service/test_check_repository_grant_replay.py
async def test_same_request_replay_after_repository_grant_reaches_terminal_result(
    tmp_path, variant
):
    tmp_path.chmod(0o700)
    vault = VaultService(
        ...,
        vault_store_factory=lambda: EncryptedVaultStore(tmp_path / "vault"),
        ...
    )
    factory = build_ready_application_factory(..., paths=_Paths(tmp_path), ...)
```

`--basetemp $RUNNER_TEMP/yoetz-integration` moves that tree off the shared-temp roots without touching the guard.

---

## Findings

### 1. `--basetemp` is only on `tests/integration`

**Severity:** note

The same step still runs packaging and subprocess pytest on the default `/tmp` base. Subprocess tests already special-case sockets. A new subprocess production-composition test would regress the same way.

**Fix (optional):** comment the split in the workflow, or pass the private base to any suite that constructs a production vault.

### 2. The comment oversells `.resolve()`

**Severity:** note

Canonicalizing does not keep a bad `RUNNER_TEMP` out. If that env were a symlink into `/tmp`, tests would still fail closed. That is the correct outcome; the comment just overstates the protection.

### 3. PR CI never ran this suite

**Severity:** note

`.github/workflows/pr-ci.yml` does not execute `tests/integration/service` production compositions. That is why this showed up only at tagged-release rehearsal. This patch does not close that coverage gap; it only fixes the release job.

### 4. String-lock tests are brittle by design

**Severity:** note

The contract test will fail on quote or whitespace churn. That is consistent with the rest of `test_release_workflow_contract.py`.

---

## Suggested follow-up (not merge-blocking)

1. Leave production path guards unchanged.
2. If another suite starts constructing production vaults in this job, give it the same private `--basetemp`.
3. Optionally document that PR CI does not run the release integration compositions that need a private pytest root.
