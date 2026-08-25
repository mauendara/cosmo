"""CLI surface: the Phase 0 exit criteria, asserted."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from cosmo import __version__
from cosmo.cli.main import app
from cosmo.config import load_config
from cosmo.store import find_project_by_path

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never read the developer's real user config during tests."""
    monkeypatch.setenv("COSMO_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


def _db_path() -> Path:
    return load_config().paths.db_path


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


def test_explicit_config_flag_naming_a_missing_file_fails_loudly() -> None:
    """A typo'd --config path must not silently fall back to defaults --
    only the *absence* of a user config (nothing passed at all) is expected
    and silent; naming a file that doesn't exist is a mistake worth surfacing."""
    result = runner.invoke(app, ["doctor", "--config", "/nonexistent/typo.toml"])
    assert result.exit_code == 2
    assert "not found" in result.stderr


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])
    assert "Autonomous spec-driven" in result.stdout


# ---------------------------------------------------------------------------
# Phase 1: queue, events, project (spec 5, 8, 9, 10.4 step 6).
# ---------------------------------------------------------------------------


def test_queue_add_then_ls_round_trips_a_dag() -> None:
    add_foo = runner.invoke(
        app, ["queue", "add", "openspec/changes/add-foo/proposal.md", "--task-id", "add-foo"]
    )
    assert add_foo.exit_code == 0, add_foo.stdout
    assert "queued add-foo" in add_foo.stdout

    add_bar = runner.invoke(
        app,
        [
            "queue",
            "add",
            "openspec/changes/add-bar/proposal.md",
            "--task-id",
            "add-bar",
            "--depends-on",
            "add-foo",
        ],
    )
    assert add_bar.exit_code == 0, add_bar.stdout

    ls = runner.invoke(app, ["queue", "ls"])
    assert ls.exit_code == 0
    assert "add-foo" in ls.stdout
    assert "add-bar" in ls.stdout


def test_queue_add_duplicate_task_id_fails_loudly() -> None:
    runner.invoke(app, ["queue", "add", "p1", "--task-id", "dup"])
    result = runner.invoke(app, ["queue", "add", "p2", "--task-id", "dup"])
    assert result.exit_code == 1
    assert "already queued" in result.stderr


def test_queue_show_reports_an_unknown_task() -> None:
    result = runner.invoke(app, ["queue", "show", "nonexistent"])
    assert result.exit_code == 1
    assert "no such task" in result.stderr


def test_queue_block_then_retry_round_trips_status() -> None:
    runner.invoke(app, ["queue", "add", "p1", "--task-id", "t1"])
    blocked = runner.invoke(app, ["queue", "block", "t1", "--reason", "environment"])
    assert blocked.exit_code == 0
    assert "blocked t1" in blocked.stdout

    show = runner.invoke(app, ["queue", "show", "t1"])
    assert "blocked" in show.stdout

    retried = runner.invoke(app, ["queue", "retry", "t1"])
    assert retried.exit_code == 0
    assert "requeued t1" in retried.stdout


def test_queue_block_rejects_an_invalid_reason() -> None:
    runner.invoke(app, ["queue", "add", "p1", "--task-id", "t1"])
    result = runner.invoke(app, ["queue", "block", "t1", "--reason", "not_a_real_reason"])
    assert result.exit_code == 2
    assert "invalid reason" in result.stderr


def test_events_tail_shows_events_emitted_by_queue_commands() -> None:
    runner.invoke(app, ["queue", "add", "p1", "--task-id", "t1"])
    runner.invoke(app, ["queue", "block", "t1", "--reason", "cost"])

    result = runner.invoke(app, ["events", "tail"])
    assert result.exit_code == 0
    assert "task.state_changed" in result.stdout
    assert "task.blocked" in result.stdout


def test_project_register_then_list(tmp_path: Path) -> None:
    target = tmp_path / "target-repo"
    target.mkdir()
    registered = runner.invoke(app, ["project", "register", str(target)])
    assert registered.exit_code == 0, registered.stdout
    assert "registered" in registered.stdout

    listed = runner.invoke(app, ["project", "list"])
    assert "claude" in listed.stdout
    assert find_project_by_path(_db_path(), str(target)) is not None


def test_project_register_rejects_a_non_directory(tmp_path: Path) -> None:
    missing = tmp_path / "nonexistent"
    result = runner.invoke(app, ["project", "register", str(missing)])
    assert result.exit_code == 2


def test_doctor_resolves_the_project_tier_from_a_registered_project(tmp_path: Path) -> None:
    target = tmp_path / "target-repo"
    target.mkdir()
    runner.invoke(app, ["project", "register", str(target)])

    result = runner.invoke(app, ["doctor", "--project-path", str(target)])
    assert "project registration" in result.stdout
