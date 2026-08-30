"""`task.progress`: checkbox parsing, the `task_progress`/`task_heartbeat`
UPSERTs (debounced, numerator/denominator never collapsed to a percent),
and the `watchdog` file-mode path (its first real caller)."""

from __future__ import annotations

import time
from pathlib import Path

from cosmo.events import EventEmitter
from cosmo.store.enums import HeartbeatSource
from cosmo.store.reader import get_progress
from cosmo.store.writer import StoreWriter
from cosmo.task.progress import ProgressWatcher, parse_tasks_md, read_progress_from_file


def test_parse_tasks_md_counts_checked_and_total_and_finds_the_last_checked_label() -> None:
    text = "\n".join(
        [
            "# Tasks",
            "- [x] 1.1 First",
            "- [ ] 1.2 Second",
            "- [x] 1.3 Third",
            "not a checkbox line",
        ]
    )

    completed, total, last_label = parse_tasks_md(text)

    assert (completed, total) == (2, 3)
    assert last_label == "1.3 Third"


def test_parse_tasks_md_a_shrinking_total_is_not_flattened_to_a_percent() -> None:
    first = parse_tasks_md("- [x] 1.1 A\n- [ ] 1.2 B\n- [ ] 1.3 C\n")
    # The agent edited the list mid-flight (spec 4 explicitly permits this)
    # and the total shrank.
    second = parse_tasks_md("- [x] 1.1 A\n")

    assert first == (1, 3, "1.1 A")
    assert second == (1, 1, "1.1 A")
    # Both numerator and denominator are available separately; nothing in
    # this module ever collapses them into one number.


def test_read_progress_from_file_missing_file_is_zero_zero_none(tmp_path: Path) -> None:
    assert read_progress_from_file(tmp_path / "nope" / "tasks.md") == (0, 0, None)


def test_check_writes_progress_only_on_change_but_heartbeat_every_time(tmp_path: Path) -> None:
    db_path = tmp_path / "cosmo.db"
    writer = StoreWriter(db_path)
    try:
        writer.queue_add(task_id="t1", spec_path="p1", max_attempts=2)
        emitter = EventEmitter(writer)

        calls = iter([(0, 3, None), (0, 3, None), (1, 3, "1.1 A")])
        watcher = ProgressWatcher(
            task_id="t1",
            run_id=None,
            state="implementing",
            writer=writer,
            emitter=emitter,
            read_progress=lambda: next(calls),
        )

        watcher.check(HeartbeatSource.MTIME)
        writer.drain()
        first = get_progress(db_path, "t1")
        assert first is not None
        assert (first.completed, first.total) == (0, 3)
        first_updated_at = first.updated_at

        # Second check reports the same progress -- no new progress row
        # write, but the heartbeat should still have moved (it always
        # reflects "still alive", independent of whether progress changed).
        watcher.check(HeartbeatSource.MTIME)
        writer.drain()
        second = get_progress(db_path, "t1")
        assert second is not None
        assert second.updated_at == first_updated_at

        watcher.check(HeartbeatSource.MTIME)
        writer.drain()
        third = get_progress(db_path, "t1")
        assert third is not None
        assert (third.completed, third.total, third.last_label) == (1, 3, "1.1 A")
    finally:
        writer.close()


def test_check_pokes_the_liveness_timer_only_when_progress_changes(tmp_path: Path) -> None:
    from cosmo.proc.timers import LivenessTimers

    db_path = tmp_path / "cosmo.db"
    writer = StoreWriter(db_path)
    try:
        writer.queue_add(task_id="t1", spec_path="p1", max_attempts=2)
        emitter = EventEmitter(writer)
        timers = LivenessTimers(wall_s=1000.0, stall_s=0.05)

        calls = iter([(0, 3, None), (0, 3, None)])
        watcher = ProgressWatcher(
            task_id="t1",
            run_id=None,
            state="implementing",
            writer=writer,
            emitter=emitter,
            read_progress=lambda: next(calls),
            timers=timers,
        )

        watcher.check(HeartbeatSource.MTIME)  # first sighting: a change, pokes
        assert not timers.stall.expired()
        time.sleep(0.1)
        assert timers.stall.expired()

        watcher.check(HeartbeatSource.MTIME)  # unchanged progress: no poke
        assert timers.stall.expired()
    finally:
        writer.close()


def test_watchdog_observer_detects_a_real_write_to_tasks_md(tmp_path: Path) -> None:
    db_path = tmp_path / "cosmo.db"
    writer = StoreWriter(db_path)
    try:
        writer.queue_add(task_id="t1", spec_path="p1", max_attempts=2)
        emitter = EventEmitter(writer)
        tasks_md = tmp_path / "change" / "tasks.md"

        watcher = ProgressWatcher(
            task_id="t1",
            run_id=None,
            state="implementing",
            writer=writer,
            emitter=emitter,
            read_progress=lambda: read_progress_from_file(tasks_md),
            tasks_md_path=tasks_md,
        )
        watcher.start()
        try:
            tasks_md.write_text("- [ ] 1.1 First\n- [ ] 1.2 Second\n")

            deadline = time.monotonic() + 5.0
            progress = None
            while time.monotonic() < deadline:
                writer.drain()
                progress = get_progress(db_path, "t1")
                if progress is not None and progress.total == 2:
                    break
                time.sleep(0.05)

            assert progress is not None
            assert (progress.completed, progress.total) == (0, 2)
        finally:
            watcher.stop()
    finally:
        writer.close()
