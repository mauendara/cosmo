"""StoreWriter: task_queue round-trip, transitions, and cross-thread handoff
(spec 5, spec 8)."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from cosmo.store import StoreWriter, TaskNotFoundError, get_task, list_tasks
from cosmo.store.enums import BlockedReason


def test_queue_add_then_list_round_trips_a_dag(tmp_path: Path) -> None:
    db_path = tmp_path / "cosmo.db"
    writer = StoreWriter(db_path)
    writer.queue_add(
        task_id="add-foo", spec_path="openspec/changes/add-foo/proposal.md", max_attempts=2
    )
    writer.queue_add(
        task_id="add-bar",
        spec_path="openspec/changes/add-bar/proposal.md",
        depends_on=["add-foo"],
        priority=5,
        max_attempts=2,
    )
    writer.close()

    tasks = {t.task_id: t for t in list_tasks(db_path)}
    assert set(tasks) == {"add-foo", "add-bar"}
    assert tasks["add-bar"].depends_on == ["add-foo"]
    assert tasks["add-bar"].priority == 5
    assert tasks["add-foo"].status == "queued"


def test_queue_add_duplicate_task_id_raises(tmp_path: Path) -> None:
    writer = StoreWriter(tmp_path / "cosmo.db")
    writer.queue_add(task_id="add-foo", spec_path="p1", max_attempts=2)
    with pytest.raises(sqlite3.IntegrityError):
        writer.queue_add(task_id="add-foo", spec_path="p2", max_attempts=2)
    writer.close()


def test_queue_block_then_retry_round_trips_status(tmp_path: Path) -> None:
    db_path = tmp_path / "cosmo.db"
    writer = StoreWriter(db_path)
    writer.queue_add(task_id="add-foo", spec_path="p1", max_attempts=2)

    writer.queue_block("add-foo", BlockedReason.ENVIRONMENT)
    blocked = get_task(db_path, "add-foo")
    assert blocked is not None
    assert blocked.status == "blocked"
    assert blocked.blocked_reason == "environment"

    writer.queue_retry("add-foo")
    requeued = get_task(db_path, "add-foo")
    assert requeued is not None
    assert requeued.status == "queued"
    assert requeued.blocked_reason is None
    writer.close()


def test_queue_retry_unknown_task_raises(tmp_path: Path) -> None:
    writer = StoreWriter(tmp_path / "cosmo.db")
    with pytest.raises(TaskNotFoundError):
        writer.queue_retry("nonexistent")
    writer.close()


def test_every_transition_is_recorded_append_only(tmp_path: Path) -> None:
    writer = StoreWriter(tmp_path / "cosmo.db")
    writer.queue_add(task_id="add-foo", spec_path="p1", max_attempts=2)
    writer.queue_block("add-foo", BlockedReason.TIMEOUT)
    writer.queue_retry("add-foo")

    rows = writer.connection.execute(
        "SELECT from_state, to_state FROM task_transitions WHERE task_id = 'add-foo' ORDER BY id"
    ).fetchall()
    assert [(r["from_state"], r["to_state"]) for r in rows] == [
        (None, "queued"),
        ("queued", "blocked"),
        ("blocked", "queued"),
    ]
    writer.close()


def test_submit_and_drain_hand_a_write_from_another_thread_to_the_owner(tmp_path: Path) -> None:
    """Spec 8: a background thread (a future watcher/stream reader) pushes a
    write onto the queue instead of opening its own connection; only the
    owning thread's `drain()` actually touches the database."""
    writer = StoreWriter(tmp_path / "cosmo.db")
    writer.queue_add(task_id="add-foo", spec_path="p1", max_attempts=2)

    def job(conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO task_progress (task_id, completed, total, updated_at) "
            "VALUES ('add-foo', 1, 4, '2026-01-01T00:00:00.000+00:00')"
        )

    def background() -> None:
        writer.submit(job)

    thread = threading.Thread(target=background)
    thread.start()
    thread.join()

    applied = writer.drain()
    assert applied == 1
    row = writer.connection.execute("SELECT completed, total FROM task_progress").fetchone()
    assert (row["completed"], row["total"]) == (1, 4)
    writer.close()
