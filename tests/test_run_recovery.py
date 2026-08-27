"""`run.recovery` (v5 improvements plan part 1): a task interrupted
mid-flight by a killed/crashed process is requeued rather than lost
forever, a `run_state` row still `running` at startup is marked
`crashed`, and only one `cosmo run` may hold the process lock at a time."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cosmo.events.emitter import EventEmitter
from cosmo.events.envelope import EventType
from cosmo.run.recovery import RunLockHeldError, acquire_run_lock, reconcile_interrupted_tasks
from cosmo.store.enums import RunStatus, TaskStatus
from cosmo.store.reader import get_run, get_task, list_events, list_task_failures
from cosmo.store.writer import StoreWriter

# -- acquire_run_lock ---------------------------------------------------------


def test_acquire_run_lock_writes_the_current_pid(tmp_path: Path) -> None:
    lock = acquire_run_lock(tmp_path)
    assert lock.path == tmp_path / "cosmo-run.lock"
    assert lock.path.read_text().strip() == str(os.getpid())
    lock.release()


def test_release_removes_the_lock_file(tmp_path: Path) -> None:
    lock = acquire_run_lock(tmp_path)
    lock.release()
    assert not lock.path.exists()


def test_release_is_idempotent(tmp_path: Path) -> None:
    lock = acquire_run_lock(tmp_path)
    lock.release()
    lock.release()  # must not raise


def test_a_live_lock_refuses_a_second_acquire(tmp_path: Path) -> None:
    # This test process's own pid is unambiguously alive.
    first = acquire_run_lock(tmp_path)
    with pytest.raises(RunLockHeldError, match=str(os.getpid())):
        acquire_run_lock(tmp_path)
    first.release()


def test_a_stale_lock_is_reclaimed_automatically(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    lock_path = tmp_path / "cosmo-run.lock"
    lock_path.write_text("999999999")  # far past any real pid on this host

    lock = acquire_run_lock(tmp_path)

    assert lock_path.read_text().strip() == str(os.getpid())
    lock.release()


def test_a_garbage_lock_file_is_treated_as_stale(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "cosmo-run.lock").write_text("not-a-pid")

    lock = acquire_run_lock(tmp_path)
    lock.release()


# -- reconcile_interrupted_tasks ----------------------------------------------


def test_a_mid_flight_task_is_requeued_and_recorded_as_environment_error(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "cosmo.db"
    writer = StoreWriter(db_path)
    emitter = EventEmitter(writer)
    writer.queue_add(task_id="a", spec_path="openspec/changes/a", max_attempts=2)
    writer.queue_transition(task_id="a", to_state=TaskStatus.IMPLEMENTING)
    writer.queue_begin_attempt("a")  # attempt_count -> 1, as a real IMPLEMENTING entry would
    writer.queue_set_worktree_path("a", Path("/tmp/some/leftover/worktree"))
    # The new run's own row must already exist -- task_failures/
    # task_transitions both hold a real FK to run_state(run_id), and
    # run.loop.run_queue calls reconcile only after writer.run_create.
    writer.run_create(
        run_id="new-run",
        harness="claude",
        permission_mode="dontAsk",
        max_turns=80,
        base_branch="develop",
    )

    outcome = reconcile_interrupted_tasks(
        db_path=db_path, writer=writer, emitter=emitter, run_id="new-run"
    )

    assert outcome.requeued_task_ids == ["a"]
    task = get_task(db_path, "a")
    assert task is not None
    assert task.status == "queued"
    assert task.worktree_path is None
    assert task.attempt_count == 1, "must not consume the code-level retry budget"

    failures = list_task_failures(db_path, "a")
    assert len(failures) == 1
    assert failures[0].failure_type == "environment_error"
    assert failures[0].will_retry is True
    assert "implementing" in failures[0].error_summary

    interrupted = list_events(db_path, event_type=EventType.TASK_INTERRUPTED.value)
    assert len(interrupted) == 1
    assert interrupted[0].task_id == "a"
    assert interrupted[0].payload["previous_status"] == "implementing"
    writer.close()


@pytest.mark.parametrize("status", ["queued", "done", "blocked"])
def test_queued_done_and_blocked_tasks_are_left_alone(tmp_path: Path, status: str) -> None:
    db_path = tmp_path / "cosmo.db"
    writer = StoreWriter(db_path)
    emitter = EventEmitter(writer)
    writer.queue_add(task_id="a", spec_path="openspec/changes/a", max_attempts=2)
    if status == "done":
        writer.queue_complete("a")
    elif status == "blocked":
        from cosmo.store.enums import BlockedReason

        writer.queue_block("a", BlockedReason.ENVIRONMENT)
    # else: leave as the freshly-queued default.

    outcome = reconcile_interrupted_tasks(
        db_path=db_path, writer=writer, emitter=emitter, run_id="new-run"
    )

    assert outcome.requeued_task_ids == []
    task = get_task(db_path, "a")
    assert task is not None
    assert task.status == status
    writer.close()


def test_a_run_state_row_still_running_is_marked_crashed(tmp_path: Path) -> None:
    db_path = tmp_path / "cosmo.db"
    writer = StoreWriter(db_path)
    emitter = EventEmitter(writer)
    writer.run_create(
        run_id="old-run",
        harness="claude",
        permission_mode="dontAsk",
        max_turns=80,
        base_branch="develop",
    )
    writer.run_transition("old-run", RunStatus.RUNNING)

    outcome = reconcile_interrupted_tasks(
        db_path=db_path, writer=writer, emitter=emitter, run_id="new-run"
    )

    assert outcome.crashed_run_ids == ["old-run"]
    row = get_run(db_path, "old-run")
    assert row is not None
    assert row.status == "stopped"
    assert row.stop_reason == "crashed"
    writer.close()


def test_a_paused_run_is_not_mistaken_for_crashed(tmp_path: Path) -> None:
    """The run being resumed itself sits at `paused`, not `running`, at
    reconciliation time -- it must not be swept up as a crash of itself."""
    db_path = tmp_path / "cosmo.db"
    writer = StoreWriter(db_path)
    emitter = EventEmitter(writer)
    writer.run_create(
        run_id="paused-run",
        harness="claude",
        permission_mode="dontAsk",
        max_turns=80,
        base_branch="develop",
    )
    writer.run_transition("paused-run", RunStatus.PAUSED)

    outcome = reconcile_interrupted_tasks(
        db_path=db_path, writer=writer, emitter=emitter, run_id="paused-run"
    )

    assert outcome.crashed_run_ids == []
    row = get_run(db_path, "paused-run")
    assert row is not None
    assert row.status == "paused"
    writer.close()


def test_the_current_run_s_own_running_row_is_never_marked_crashed(tmp_path: Path) -> None:
    """Regression: `run.loop.run_queue` calls this *after* transitioning the
    new/resumed run's own row to `running` (a real FK constraint forces
    that ordering). Without excluding `run_id` itself, every fresh
    `cosmo run` would immediately mark its own brand-new run
    `stopped`/`crashed` a few lines after starting it -- caught by a real
    `run_queue` invocation emitting two `run.stopped` events for one run,
    not by inspection."""
    db_path = tmp_path / "cosmo.db"
    writer = StoreWriter(db_path)
    emitter = EventEmitter(writer)
    writer.run_create(
        run_id="current-run",
        harness="claude",
        permission_mode="dontAsk",
        max_turns=80,
        base_branch="develop",
    )
    writer.run_transition("current-run", RunStatus.RUNNING)  # exactly what run_queue just did

    outcome = reconcile_interrupted_tasks(
        db_path=db_path, writer=writer, emitter=emitter, run_id="current-run"
    )

    assert outcome.crashed_run_ids == []
    row = get_run(db_path, "current-run")
    assert row is not None
    assert row.status == "running"
    writer.close()


def test_reconcile_is_a_no_op_on_a_healthy_startup(tmp_path: Path) -> None:
    db_path = tmp_path / "cosmo.db"
    writer = StoreWriter(db_path)
    emitter = EventEmitter(writer)
    writer.queue_add(task_id="a", spec_path="openspec/changes/a", max_attempts=2)

    outcome = reconcile_interrupted_tasks(
        db_path=db_path, writer=writer, emitter=emitter, run_id="new-run"
    )

    assert outcome.requeued_task_ids == []
    assert outcome.crashed_run_ids == []
    writer.close()
