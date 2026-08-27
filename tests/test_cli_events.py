"""`cosmo events tail`, including `--follow` (v5 improvements plan part 4)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cosmo.cli.main import app
from cosmo.config import load_config
from cosmo.events import EventEmitter, EventType, Severity
from cosmo.store import StoreWriter

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COSMO_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


def test_tail_renders_existing_rows() -> None:
    cfg = load_config()
    writer = StoreWriter(cfg.paths.db_path)
    EventEmitter(writer).emit(
        event_type=EventType.TASK_BLOCKED, severity=Severity.WARNING, task_id="a"
    )
    writer.close()

    result = runner.invoke(app, ["events", "tail"])

    assert result.exit_code == 0, result.output
    assert "task.blocked" in result.output


def test_follow_prints_pre_existing_rows_then_stops_on_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No new rows land after the initial listing -- the poll loop's first
    `sleep` call is where a real `--follow` session would block forever, so
    the test raises `KeyboardInterrupt` there to unwind cleanly, the same
    way a real operator's Ctrl-C does."""
    cfg = load_config()
    writer = StoreWriter(cfg.paths.db_path)
    EventEmitter(writer).emit(
        event_type=EventType.TASK_BLOCKED, severity=Severity.WARNING, task_id="a"
    )
    writer.close()

    def _interrupt(_seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(time, "sleep", _interrupt)

    result = runner.invoke(app, ["events", "tail", "--follow"])

    assert result.exit_code == 0, result.output
    assert "task.blocked" in result.output


def test_follow_prints_a_new_row_that_lands_after_the_initial_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = load_config()
    writer = StoreWriter(cfg.paths.db_path)
    emitter = EventEmitter(writer)
    emitter.emit(event_type=EventType.TASK_BLOCKED, severity=Severity.WARNING, task_id="a")

    calls = {"n": 0}

    def _sleep_then_write_then_interrupt(_seconds: float) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            emitter.emit(event_type=EventType.TASK_COMPLETED, severity=Severity.INFO, task_id="b")
            return
        raise KeyboardInterrupt

    monkeypatch.setattr(time, "sleep", _sleep_then_write_then_interrupt)

    result = runner.invoke(app, ["events", "tail", "--follow"])
    writer.close()

    assert result.exit_code == 0, result.output
    assert "task.completed" in result.output
