"""Core and harness preflight checks."""

from __future__ import annotations

from pathlib import Path

import pytest

from cosmo.checks import CheckStatus
from cosmo.config import CosmoConfig, load_config
from cosmo.doctor import check_disk, check_work_dir_filesystem, core_checks
from cosmo.harness.claude import BILLING_ENV_VAR, ClaudeCodeAdapter

NO_USER_CONFIG = Path("/nonexistent/config.toml")


def _config(tmp_path: Path, **overrides: object) -> CosmoConfig:
    cfg = load_config(config_path=NO_USER_CONFIG)
    paths = cfg.paths.model_copy(
        update={"data_dir": tmp_path, "work_dir": tmp_path / "work", "log_dir": tmp_path / "logs"}
    )
    return cfg.model_copy(update={"paths": paths, **overrides})


def test_core_checks_produce_a_result_per_check(tmp_path: Path) -> None:
    results = core_checks(_config(tmp_path))
    assert len(results) == len({r.name for r in results}), "check names must be unique"
    assert {"git", "docker", "openspec", "disk space"} <= {r.name for r in results}


def test_disk_floor_fails_when_below_threshold(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    huge = cfg.disk.model_copy(update={"min_free_gb": 10_000_000.0})
    result = check_disk(cfg.model_copy(update={"disk": huge}))
    assert result.status is CheckStatus.FAIL
    assert result.blocking


def test_windows_mount_work_dir_warns_but_does_not_block(tmp_path: Path) -> None:
    """Spec 1: /mnt/c I/O is slow enough to distort the section 3.3 timeouts."""
    cfg = _config(tmp_path)
    paths = cfg.paths.model_copy(update={"work_dir": Path("/mnt/c/Users/dev/work")})
    result = check_work_dir_filesystem(cfg.model_copy(update={"paths": paths}))
    assert result.status is CheckStatus.WARN
    assert not result.blocking


def test_wsl_filesystem_work_dir_passes(tmp_path: Path) -> None:
    assert check_work_dir_filesystem(_config(tmp_path)).status is CheckStatus.OK


def test_billing_env_var_is_a_blocking_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec 2.3: set, it silently reroutes billing to per-token API rates."""
    monkeypatch.setenv(BILLING_ENV_VAR, "sk-test")
    results = ClaudeCodeAdapter(_config(tmp_path)).preflight()
    billing = next(r for r in results if r.name == "subscription billing")
    assert billing.status is CheckStatus.FAIL
    assert billing.blocking


def test_billing_check_passes_when_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(BILLING_ENV_VAR, raising=False)
    results = ClaudeCodeAdapter(_config(tmp_path)).preflight()
    billing = next(r for r in results if r.name == "subscription billing")
    assert billing.status is CheckStatus.OK


def test_bypass_permissions_is_a_blocking_failure(tmp_path: Path) -> None:
    """Spec 2.3: never used -- the host holds SSH keys and real credentials."""
    cfg = _config(tmp_path)
    harness = cfg.harness.model_copy(update={"permission_mode": "bypassPermissions"})
    results = ClaudeCodeAdapter(cfg.model_copy(update={"harness": harness})).preflight()
    mode = next(r for r in results if r.name == "permission mode")
    assert mode.status is CheckStatus.FAIL


def test_preflight_is_side_effect_free(tmp_path: Path) -> None:
    """It runs before every command; it must not create or mutate anything."""
    before = set(tmp_path.rglob("*"))
    ClaudeCodeAdapter(_config(tmp_path)).preflight()
    assert set(tmp_path.rglob("*")) == before
