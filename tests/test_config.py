"""Configuration loading and validation."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
from pydantic import ValidationError

from cosmo.config import load_config
from cosmo.config.loader import _deep_merge, write_user_config_table


def test_defaults_load_and_validate() -> None:
    cfg = load_config(config_path=Path("/nonexistent/config.toml"))
    assert cfg.harness.permission_mode == "dontAsk"
    assert cfg.retries.max_attempts == 2
    assert cfg.timeouts.run_wall == 36000
    assert cfg.knowledge.max_file_lines == 400
    assert cfg.git.base_branch == "develop"


def test_paths_default_to_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    cfg = load_config(config_path=Path("/nonexistent/config.toml"))
    assert cfg.paths.data_dir == tmp_path / "cosmo"
    assert cfg.paths.db_path == tmp_path / "cosmo" / "cosmo.db"


def test_user_config_overrides_defaults(tmp_path: Path) -> None:
    override = tmp_path / "config.toml"
    override.write_text('[retries]\nmax_attempts = 5\n\n[paths]\nwork_dir = "/var/cosmo/work"\n')
    cfg = load_config(config_path=override)
    assert cfg.retries.max_attempts == 5
    assert cfg.paths.work_dir == Path("/var/cosmo/work")
    # Untouched sections keep their shipped defaults.
    assert cfg.timeouts.run_wall == 36000


def test_stall_timeout_must_be_below_wall_clock(tmp_path: Path) -> None:
    """A stall timer that outlives its wall clock can never fire (spec 3.3)."""
    override = tmp_path / "config.toml"
    override.write_text("[timeouts]\nimplementing_stall = 9999999\n")
    with pytest.raises(ValidationError, match="can never fire"):
        load_config(config_path=override)


def test_retry_delay_range_must_be_ordered(tmp_path: Path) -> None:
    override = tmp_path / "config.toml"
    override.write_text("[retries]\ndelay_min = 90\n")
    with pytest.raises(ValidationError, match="exceeds"):
        load_config(config_path=override)


def test_floating_playwright_tag_is_rejected(tmp_path: Path) -> None:
    """Spec 1.1: version pinning is atomic; 'latest' turns green red overnight."""
    override = tmp_path / "config.toml"
    override.write_text('[gate]\nplaywright_image = "mcr.microsoft.com/playwright:latest"\n')
    with pytest.raises(ValidationError, match="pinned to an explicit tag"):
        load_config(config_path=override)


def test_unknown_setting_is_rejected(tmp_path: Path) -> None:
    """A typo in a config file must fail loudly, not be silently ignored."""
    override = tmp_path / "config.toml"
    override.write_text("[retries]\nmax_attemps = 5\n")
    with pytest.raises(ValidationError):
        load_config(config_path=override)


def test_zero_cost_ceiling_means_disabled() -> None:
    cfg = load_config(config_path=Path("/nonexistent/config.toml"))
    assert cfg.cost.run_limit_enabled is False
    assert cfg.cost.task_limit_enabled is False


def test_deep_merge_preserves_untouched_keys() -> None:
    base = {"a": {"x": 1, "y": 2}, "b": 3}
    assert _deep_merge(base, {"a": {"y": 9}}) == {"a": {"x": 1, "y": 9}, "b": 3}


def test_notify_disabled_by_default() -> None:
    cfg = load_config(config_path=Path("/nonexistent/config.toml"))
    assert cfg.notify.enabled is False
    assert cfg.notify.telegram_bot_token is None
    assert cfg.notify.stale_after_seconds == 1800


def test_quota_bypass_requires_a_nonzero_run_cost_ceiling(tmp_path: Path) -> None:
    """v5 improvements plan part 7 (decision 7): the bypass must not exist
    without the spend ceiling it recreates the need for."""
    override = tmp_path / "config.toml"
    override.write_text("[quota]\nbypass_5h_with_credits = true\n")
    with pytest.raises(ValidationError, match="max_cost_per_run_usd"):
        load_config(config_path=override)


def test_quota_bypass_is_accepted_alongside_a_real_cost_ceiling(tmp_path: Path) -> None:
    override = tmp_path / "config.toml"
    override.write_text(
        "[quota]\nbypass_5h_with_credits = true\n\n[cost]\nmax_cost_per_run_usd = 10.0\n"
    )
    cfg = load_config(config_path=override)
    assert cfg.quota.bypass_5h_with_credits is True


def test_write_user_config_table_creates_a_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "config.toml"
    write_user_config_table(path, "notify", {"enabled": True, "telegram_chat_id": "1"})

    cfg = load_config(config_path=path)
    assert cfg.notify.enabled is True
    assert cfg.notify.telegram_chat_id == "1"


def test_write_user_config_table_preserves_other_tables(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[retries]\nmax_attempts = 5\n")

    write_user_config_table(path, "notify", {"enabled": True})

    cfg = load_config(config_path=path)
    assert cfg.retries.max_attempts == 5
    assert cfg.notify.enabled is True


def test_write_user_config_table_overwrites_its_own_prior_values(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    write_user_config_table(path, "notify", {"enabled": True, "telegram_chat_id": "old"})
    write_user_config_table(path, "notify", {"enabled": True, "telegram_chat_id": "new"})

    cfg = load_config(config_path=path)
    assert cfg.notify.telegram_chat_id == "new"


def test_write_user_config_table_sets_owner_only_permissions(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    write_user_config_table(path, "notify", {"telegram_bot_token": "secret"})

    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == stat.S_IRUSR | stat.S_IWUSR
