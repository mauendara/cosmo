"""`cosmo run resume` (v5 improvements plan part 2): CLI glue only --
resolving the target run, the confirmation prompt, and wiring
`resume_run_id` through to `run.loop.run_queue` (monkeypatched out here,
same convention `test_cli_run_queue.py` already uses for the no-`--task`
path)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import cosmo.cli.main as cli_main
from cosmo.cli.main import app
from cosmo.config import load_config
from cosmo.harness.fake import FakeHarnessAdapter
from cosmo.run.recovery import RunLockHeldError
from cosmo.run.types import RunOutcome, RunSummary
from cosmo.store import StoreWriter
from cosmo.store.enums import RunStatus, StopReason

runner = CliRunner()


def _register(repo: Path, *, harness: str = "claude") -> None:
    writer = StoreWriter(load_config().paths.db_path)
    try:
        writer.register_project(target_path=str(repo.resolve()), harness=harness)
    finally:
        writer.close()


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COSMO_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


@pytest.fixture(autouse=True)
def _fake_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_main, "get_adapter", lambda name: FakeHarnessAdapter)


def _outcome(**overrides: object) -> RunOutcome:
    base: dict[str, object] = {
        "run_id": "run-1",
        "status": RunStatus.STOPPED,
        "stop_reason": StopReason.QUEUE_EMPTY,
        "summary": RunSummary(),
        "execution_order": [],
    }
    base.update(overrides)
    return RunOutcome(**base)  # type: ignore[arg-type]


def _paused_run(run_id: str = "old-run") -> None:
    writer = StoreWriter(load_config().paths.db_path)
    try:
        writer.run_create(
            run_id=run_id,
            harness="claude",
            permission_mode="dontAsk",
            max_turns=80,
            base_branch="develop",
        )
        writer.run_transition(run_id, RunStatus.PAUSED)
    finally:
        writer.close()


def test_no_run_id_and_no_paused_run_errors_clearly(tmp_path: Path) -> None:
    _register(tmp_path)
    result = runner.invoke(app, ["run", "resume", "--repo", str(tmp_path)])
    assert result.exit_code == 1
    assert "no paused run" in result.output.lower()


def test_an_unknown_run_id_errors_clearly(tmp_path: Path) -> None:
    _register(tmp_path)
    result = runner.invoke(app, ["run", "resume", "does-not-exist", "--repo", str(tmp_path)])
    assert result.exit_code == 1
    assert "no such run" in result.output.lower()


def test_a_non_paused_run_is_refused(tmp_path: Path) -> None:
    _register(tmp_path)
    writer = StoreWriter(load_config().paths.db_path)
    writer.run_create(
        run_id="r1",
        harness="claude",
        permission_mode="dontAsk",
        max_turns=80,
        base_branch="develop",
    )
    writer.run_transition("r1", RunStatus.STOPPED, stop_reason=StopReason.QUEUE_EMPTY)
    writer.close()

    result = runner.invoke(app, ["run", "resume", "r1", "--repo", str(tmp_path)])

    assert result.exit_code == 1
    assert "not paused" in result.output.lower()


def test_yes_skips_the_confirmation_and_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(tmp_path)
    _paused_run("old-run")
    captured: dict[str, Any] = {}

    def _fake_run_queue(**kwargs: Any) -> RunOutcome:
        captured.update(kwargs)
        return _outcome(status=RunStatus.STOPPED, stop_reason=StopReason.QUEUE_EMPTY)

    monkeypatch.setattr(cli_main, "run_queue", _fake_run_queue)

    result = runner.invoke(app, ["run", "resume", "old-run", "--repo", str(tmp_path), "--yes"])

    assert result.exit_code == 0, result.output
    assert captured["resume_run_id"] == "old-run"
    assert captured["base_branch"] == "develop"


def test_declining_the_prompt_does_not_call_run_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(tmp_path)
    _paused_run("old-run")
    called = False

    def _fake_run_queue(**_kwargs: Any) -> RunOutcome:
        nonlocal called
        called = True
        return _outcome()

    monkeypatch.setattr(cli_main, "run_queue", _fake_run_queue)

    result = runner.invoke(app, ["run", "resume", "old-run", "--repo", str(tmp_path)], input="n\n")

    assert result.exit_code == 0
    assert called is False


def test_defaults_to_the_most_recently_paused_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(tmp_path)
    _paused_run("older-run")
    _paused_run("newer-run")
    captured: dict[str, Any] = {}

    def _fake_run_queue(**kwargs: Any) -> RunOutcome:
        captured.update(kwargs)
        return _outcome()

    monkeypatch.setattr(cli_main, "run_queue", _fake_run_queue)

    result = runner.invoke(app, ["run", "resume", "--repo", str(tmp_path), "--yes"])

    assert result.exit_code == 0, result.output
    assert captured["resume_run_id"] == "newer-run"


def test_a_held_lock_is_reported_cleanly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _register(tmp_path)
    _paused_run("old-run")

    def _fake_run_queue(**_kwargs: Any) -> RunOutcome:
        raise RunLockHeldError("another cosmo run (pid 123) already holds the lock")

    monkeypatch.setattr(cli_main, "run_queue", _fake_run_queue)

    result = runner.invoke(app, ["run", "resume", "old-run", "--repo", str(tmp_path), "--yes"])

    assert result.exit_code == 1
    assert "already holds the lock" in result.output
