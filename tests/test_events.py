"""Event envelope and transactional sequence allocation (spec 9.1)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import TracebackType
from typing import Any

import pytest

from cosmo.events import EventEmitter, EventType, Severity
from cosmo.store import StoreWriter


def test_sequence_is_monotonic_and_gapless_within_a_run(tmp_path: Path) -> None:
    writer = StoreWriter(tmp_path / "cosmo.db")
    emitter = EventEmitter(writer)
    events = [
        emitter.emit(event_type=EventType.TASK_HEARTBEAT, severity=Severity.INFO, run_id="run-1")
        for _ in range(5)
    ]
    assert [e.sequence for e in events] == [1, 2, 3, 4, 5]
    writer.close()


def test_sequence_is_scoped_per_run(tmp_path: Path) -> None:
    writer = StoreWriter(tmp_path / "cosmo.db")
    emitter = EventEmitter(writer)
    a = emitter.emit(event_type=EventType.TASK_HEARTBEAT, severity=Severity.INFO, run_id="run-a")
    b = emitter.emit(event_type=EventType.TASK_HEARTBEAT, severity=Severity.INFO, run_id="run-b")
    a2 = emitter.emit(event_type=EventType.TASK_HEARTBEAT, severity=Severity.INFO, run_id="run-a")
    assert (a.sequence, b.sequence, a2.sequence) == (1, 1, 2)
    writer.close()


def test_on_emit_hook_fires_after_the_row_is_persisted(tmp_path: Path) -> None:
    """v5 improvements plan part 6: the coarse print hook -- called after the
    DB insert succeeds, with the same `Event` the caller gets back."""
    writer = StoreWriter(tmp_path / "cosmo.db")
    seen: list[EventType] = []
    emitter = EventEmitter(writer, on_emit=lambda e: seen.append(EventType(e.event_type)))

    returned = emitter.emit(
        event_type=EventType.RUN_PAUSED, severity=Severity.WARNING, run_id="run-1"
    )

    assert seen == [EventType.RUN_PAUSED]
    row = writer.connection.execute("SELECT * FROM events").fetchone()
    assert row is not None  # the insert already happened by the time the hook fired
    assert returned.event_type == EventType.RUN_PAUSED.value
    writer.close()


def test_no_on_emit_hook_is_the_default(tmp_path: Path) -> None:
    writer = StoreWriter(tmp_path / "cosmo.db")
    emitter = EventEmitter(writer)
    emitter.emit(event_type=EventType.RUN_STARTED, severity=Severity.INFO, run_id="run-1")
    writer.close()


def test_run_less_events_get_their_own_scope(tmp_path: Path) -> None:
    """Project-level events like agent_assets.synced carry no run_id."""
    writer = StoreWriter(tmp_path / "cosmo.db")
    emitter = EventEmitter(writer)
    e = emitter.emit(event_type=EventType.AGENT_ASSETS_SYNCED, severity=Severity.INFO)
    assert e.run_id is None
    assert e.sequence == 1
    writer.close()


def test_emit_persists_payload_and_envelope_fields(tmp_path: Path) -> None:
    writer = StoreWriter(tmp_path / "cosmo.db")
    emitter = EventEmitter(writer)
    emitter.emit(
        event_type=EventType.TASK_BLOCKED,
        severity=Severity.WARNING,
        task_id="add-foo",
        payload={"blocked_reason": "environment"},
    )
    row = writer.connection.execute("SELECT * FROM events").fetchone()
    assert row["task_id"] == "add-foo"
    assert row["severity"] == "warning"
    assert row["event_type"] == "task.blocked"
    assert row["schema_version"] == 1
    writer.close()


class _FlakyOnSecondInsert:
    """Wraps the real connection and fails one specific `execute` call, to
    simulate a crash between the sequence bump and the event insert without
    monkeypatching a C-extension type (`sqlite3.Connection` has no
    per-instance `__dict__` to patch)."""

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real
        self.calls = 0

    def execute(self, sql: str, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
        self.calls += 1
        if self.calls == 2 and sql.strip().startswith("INSERT INTO events"):
            raise sqlite3.OperationalError("simulated crash mid-write")
        return self._real.execute(sql, *args, **kwargs)

    def __enter__(self) -> _FlakyOnSecondInsert:
        self._real.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self._real.__exit__(exc_type, exc_val, exc_tb)


def test_a_failed_emit_never_advances_the_sequence_without_its_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulates a crash between the sequence bump and the event insert: both
    are in one transaction, so a failure there must roll back the bump too --
    otherwise a later successful emit would leave a gap."""
    writer = StoreWriter(tmp_path / "cosmo.db")
    emitter = EventEmitter(writer)
    first = emitter.emit(
        event_type=EventType.TASK_HEARTBEAT, severity=Severity.INFO, run_id="run-1"
    )
    assert first.sequence == 1

    real_conn = writer.connection
    monkeypatch.setattr(writer, "_conn", _FlakyOnSecondInsert(real_conn), raising=True)
    with pytest.raises(sqlite3.OperationalError):
        emitter.emit(event_type=EventType.TASK_HEARTBEAT, severity=Severity.INFO, run_id="run-1")

    monkeypatch.setattr(writer, "_conn", real_conn, raising=True)
    second = emitter.emit(
        event_type=EventType.TASK_HEARTBEAT, severity=Severity.INFO, run_id="run-1"
    )
    assert second.sequence == 2, "the failed attempt's sequence bump must have rolled back"

    stored_sequences = [
        r[0]
        for r in real_conn.execute(
            "SELECT sequence FROM events WHERE run_id = 'run-1' ORDER BY sequence"
        ).fetchall()
    ]
    assert stored_sequences == [1, 2]
    writer.close()
