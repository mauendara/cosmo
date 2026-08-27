"""StoreWriter: task_queue round-trip, transitions, and cross-thread handoff
(spec 5, spec 8)."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from cosmo.store import StoreWriter, TaskNotFoundError, get_task, list_tasks
from cosmo.store.enums import BlockedReason, FailureStage, FailureType, NextAction, TaskStatus


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


def test_queue_retry_resets_attempt_count_and_clears_worktree_path(tmp_path: Path) -> None:
    """Found by hand: a task retried after exhausting `max_attempts` was
    left carrying its old, already-over-budget `attempt_count` -- the very
    next genuine code-level failure blocked it again immediately, no
    retries actually available despite `cosmo queue retry` supposedly
    meaning "try again." `worktree_path` clears too (the caller physically
    removes the worktree first, same convention `queue_complete` already
    uses) so a later pick-up never silently reuses what the blocked attempt
    left behind."""
    db_path = tmp_path / "cosmo.db"
    writer = StoreWriter(db_path)
    writer.queue_add(task_id="add-foo", spec_path="p1", max_attempts=2)
    writer.queue_begin_attempt("add-foo")
    writer.queue_begin_attempt("add-foo")
    writer.queue_begin_attempt("add-foo")
    writer.queue_set_worktree_path("add-foo", Path("/some/worktree"))
    writer.queue_block("add-foo", BlockedReason.CODE_FAILURE)

    writer.queue_retry("add-foo")
    requeued = get_task(db_path, "add-foo")
    assert requeued is not None
    assert requeued.status == "queued"
    assert requeued.attempt_count == 0
    assert requeued.worktree_path is None
    writer.close()


def test_queue_retry_unknown_task_raises(tmp_path: Path) -> None:
    writer = StoreWriter(tmp_path / "cosmo.db")
    with pytest.raises(TaskNotFoundError):
        writer.queue_retry("nonexistent")
    writer.close()


def test_queue_resume_at_sets_stage_without_touching_attempt_count_or_worktree(
    tmp_path: Path,
) -> None:
    """Unlike `queue_retry`, `queue_resume_at` must not reset `attempt_count`
    or `worktree_path` -- an `environment_error` at `COMMITTING`/`MERGING`
    never consumed the retry budget and the worktree still has the good,
    already-validated-and-reviewed work sitting on it (the entire point of
    resuming there instead of discarding it)."""
    db_path = tmp_path / "cosmo.db"
    writer = StoreWriter(db_path)
    writer.queue_add(task_id="add-foo", spec_path="p1", max_attempts=2)
    writer.queue_begin_attempt("add-foo")
    writer.queue_set_worktree_path("add-foo", Path("/some/worktree"))
    writer.queue_block("add-foo", BlockedReason.ENVIRONMENT)

    writer.queue_resume_at("add-foo", TaskStatus.MERGING)
    resumed = get_task(db_path, "add-foo")
    assert resumed is not None
    assert resumed.status == "queued"
    assert resumed.blocked_reason is None
    assert resumed.resume_at_stage == "merging"
    assert resumed.attempt_count == 1  # untouched
    assert resumed.worktree_path == "/some/worktree"  # untouched
    writer.close()


def test_queue_transition_clears_resume_at_stage(tmp_path: Path) -> None:
    """`resume_at_stage` is consumed exactly once, by the first real
    transition after it was set -- `task.machine.run_task`'s own next move
    on resuming is always a `queue_transition` call, so this is what
    actually clears the hint (see `queue_transition`'s own docstring)."""
    db_path = tmp_path / "cosmo.db"
    writer = StoreWriter(db_path)
    writer.queue_add(task_id="add-foo", spec_path="p1", max_attempts=2)
    writer.queue_resume_at("add-foo", TaskStatus.COMMITTING)

    writer.queue_transition("add-foo", TaskStatus.COMMITTING)
    task = get_task(db_path, "add-foo")
    assert task is not None
    assert task.resume_at_stage is None
    writer.close()


def test_queue_set_worktree_path_round_trips(tmp_path: Path) -> None:
    db_path = tmp_path / "cosmo.db"
    writer = StoreWriter(db_path)
    writer.queue_add(task_id="add-foo", spec_path="p1", max_attempts=2)

    writer.queue_set_worktree_path("add-foo", Path("/var/cosmo/work/run-1/add-foo"))

    task = get_task(db_path, "add-foo")
    assert task is not None
    assert task.worktree_path == "/var/cosmo/work/run-1/add-foo"
    writer.close()


def test_queue_set_worktree_path_unknown_task_raises(tmp_path: Path) -> None:
    writer = StoreWriter(tmp_path / "cosmo.db")
    with pytest.raises(TaskNotFoundError):
        writer.queue_set_worktree_path("nonexistent", Path("/tmp/x"))
    writer.close()


def test_queue_complete_clears_worktree_path_and_sets_done(tmp_path: Path) -> None:
    db_path = tmp_path / "cosmo.db"
    writer = StoreWriter(db_path)
    writer.queue_add(task_id="add-foo", spec_path="p1", max_attempts=2)
    writer.queue_set_worktree_path("add-foo", Path("/var/cosmo/work/run-1/add-foo"))

    writer.queue_complete("add-foo")

    task = get_task(db_path, "add-foo")
    assert task is not None
    assert task.status == "done"
    assert task.worktree_path is None

    rows = writer.connection.execute(
        "SELECT from_state, to_state FROM task_transitions WHERE task_id = 'add-foo' ORDER BY id"
    ).fetchall()
    assert [(r["from_state"], r["to_state"]) for r in rows][-1] == ("queued", "done")
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


def test_record_task_failure_round_trips(tmp_path: Path) -> None:
    """`task_failures` (spec 9.3's payload shape) -- Phase 6's first real
    writer, unused since Phase 1 shipped the schema."""
    db_path = tmp_path / "cosmo.db"
    writer = StoreWriter(db_path)
    writer.queue_add(task_id="add-foo", spec_path="p1", max_attempts=2)

    writer.record_task_failure(
        task_id="add-foo",
        run_id=None,
        attempt_number=1,
        failure_type=FailureType.CODE_ERROR,
        failure_stage=FailureStage.UNIT_TESTS,
        error_summary="1 unit test failed",
        error_detail="FooTest#bar: expected 1 but was 2",
        files_touched=["src/test/FooTest.java"],
        will_retry=True,
        next_action=NextAction.RETRY,
    )
    writer.close()

    row = (
        sqlite3.connect(db_path)
        .execute("SELECT * FROM task_failures WHERE task_id = 'add-foo'")
        .fetchone()
    )
    assert row is not None
    columns = [
        d[0] for d in sqlite3.connect(db_path).execute("SELECT * FROM task_failures").description
    ]
    record = dict(zip(columns, row, strict=True))
    assert record["failure_type"] == "code_error"
    assert record["failure_stage"] == "unit_tests"
    assert record["will_retry"] == 1
    assert record["next_action"] == "retry"
    assert "FooTest.java" in record["files_touched"]


def test_record_task_failure_computes_failure_signature_from_error_detail(tmp_path: Path) -> None:
    """v5 improvements plan part 5 (Class 1): computed automatically at this
    one chokepoint, no call-site change needed."""
    db_path = tmp_path / "cosmo.db"
    writer = StoreWriter(db_path)
    writer.queue_add(task_id="add-foo", spec_path="p1", max_attempts=2)

    writer.record_task_failure(
        task_id="add-foo",
        run_id=None,
        attempt_number=1,
        failure_type=FailureType.ENVIRONMENT_ERROR,
        failure_stage=FailureStage.BUILD,
        error_summary="frontend build failed",
        error_detail="npm ERR! The `npm ci` command can only install packages when your "
        "package.json and package-lock.json are in sync.",
        files_touched=[],
        will_retry=True,
        next_action=NextAction.RETRY,
    )
    writer.close()

    row = (
        sqlite3.connect(db_path)
        .execute("SELECT failure_signature FROM task_failures WHERE task_id = 'add-foo'")
        .fetchone()
    )
    assert row[0] == "missing_lockfile"


def test_record_task_failure_leaves_failure_signature_null_when_unmatched(tmp_path: Path) -> None:
    db_path = tmp_path / "cosmo.db"
    writer = StoreWriter(db_path)
    writer.queue_add(task_id="add-foo", spec_path="p1", max_attempts=2)

    writer.record_task_failure(
        task_id="add-foo",
        run_id=None,
        attempt_number=1,
        failure_type=FailureType.CODE_ERROR,
        failure_stage=FailureStage.UNIT_TESTS,
        error_summary="1 unit test failed",
        error_detail="FooTest#bar: expected 1 but was 2",
        files_touched=[],
        will_retry=True,
        next_action=NextAction.RETRY,
    )
    writer.close()

    row = (
        sqlite3.connect(db_path)
        .execute("SELECT failure_signature FROM task_failures WHERE task_id = 'add-foo'")
        .fetchone()
    )
    assert row[0] is None


def test_record_task_failure_accepts_secrets_stage(tmp_path: Path) -> None:
    """`FailureStage.SECRETS` (Phase 6 deviation #11) round-trips through
    the migration-2 CHECK constraint."""
    db_path = tmp_path / "cosmo.db"
    writer = StoreWriter(db_path)
    writer.queue_add(task_id="add-foo", spec_path="p1", max_attempts=2)

    writer.record_task_failure(
        task_id="add-foo",
        run_id=None,
        attempt_number=1,
        failure_type=FailureType.CODE_ERROR,
        failure_stage=FailureStage.SECRETS,
        error_summary="gitleaks found a potential secret",
        error_detail=None,
        files_touched=[],
        will_retry=False,
        next_action=NextAction.BLOCK,
    )
    writer.close()

    row = (
        sqlite3.connect(db_path)
        .execute("SELECT failure_stage FROM task_failures WHERE task_id = 'add-foo'")
        .fetchone()
    )
    assert row[0] == "secrets"
