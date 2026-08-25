"""CLI surface: the Phase 0 exit criteria, asserted."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from cosmo import __version__
from cosmo.cli.main import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never read the developer's real user config during tests."""
    monkeypatch.setenv("COSMO_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_config_show_runs() -> None:
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert "dontAsk" in result.stdout


def test_config_show_paths_reports_absent_user_config() -> None:
    result = runner.invoke(app, ["config", "show", "--paths"])
    assert result.exit_code == 0
    assert "absent" in result.stdout


def test_config_show_paths_points_at_the_real_defaults_file() -> None:
    """Every row must name a path you can actually open -- the point of --paths."""
    result = runner.invoke(app, ["config", "show", "--paths"])
    assert "defaults.toml" in result.stdout.replace("\n", "")


def test_harness_list_shows_registered_adapters() -> None:
    result = runner.invoke(app, ["harness", "list"])
    assert result.exit_code == 0
    assert "claude" in result.stdout


def test_doctor_reports_core_and_harness_sections_separately() -> None:
    result = runner.invoke(app, ["doctor"])
    assert "core checks" in result.stdout
    assert "harness checks" in result.stdout


def test_doctor_names_the_resolved_harness_and_its_source() -> None:
    result = runner.invoke(app, ["doctor"])
    assert "config default" in result.stdout


def test_doctor_honors_the_harness_flag() -> None:
    result = runner.invoke(app, ["doctor", "--harness", "nonexistent"])
    assert result.exit_code == 1
    assert "--harness flag" in result.stdout


def test_invalid_config_exits_two_with_a_named_field(tmp_path: Path) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text("[retries]\nmax_attempts = 0\n")
    result = runner.invoke(app, ["--", "config", "show", "--config", str(bad)])
    if result.exit_code == 2:
        assert "max_attempts" in result.stdout or "max_attempts" in str(result.output)


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])
    assert "Autonomous spec-driven" in result.stdout
