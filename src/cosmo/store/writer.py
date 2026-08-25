"""The single write connection Cosmo's main loop owns (spec 8).

Every mutation goes through one `StoreWriter`. A background thread -- a
future file-watcher or stream reader (Phase 2/3) -- must never open a second
write connection; it calls `submit()` to hand its write to whichever thread
owns this object, which applies it with `drain()`. `connect_writer` itself is
not importable outside this module and `store/migrations.py`
(`tests/test_store_boundary.py` enforces that), so this is the only path to a
writable connection in the whole codebase.
"""

from __future__ import annotations

import json
import queue
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cosmo.store.clock import utcnow_iso
from cosmo.store.connection import checkpoint_truncate, connect_writer
from cosmo.store.enums import BlockedReason, FailureStage, FailureType, NextAction, TaskStatus
from cosmo.store.migrations import migrate

WriteJob = Callable[[sqlite3.Connection], None]


class TaskNotFoundError(KeyError):
    """Raised by a queue mutation naming a task_id that isn't queued."""


@dataclass(frozen=True, slots=True)
class TransitionResult:
    """What every `task_queue.status`-mutating method returns (Phase 7):
    enough for a caller to emit a canonical `task.state_changed` event
    (`events.helpers.emit_state_changed`) without re-querying the row it
    just wrote. `run_id` is carried through rather than looked up -- every
    write site already knows it (or knows it's `None`, spec 3.2's "no run
    tracking yet" posture before Phase 8)."""

    task_id: str
    run_id: str | None
    from_state: str | None
    to_state: str
    attempt_number: int


class StoreWriter:
    def __init__(self, db_path: Path) -> None:
        self._conn = connect_writer(db_path)
        migrate(self._conn)
        self._pending: queue.Queue[WriteJob] = queue.Queue()

    @property
    def connection(self) -> sqlite3.Connection:
        """The single write connection. Exposed for the event emitter
        (spec 9.1's sequence allocation shares this connection's transaction)
        and for tests -- still the one connection, never a second one."""
        return self._conn

    def close(self) -> None:
        self._conn.close()

    def checkpoint(self) -> None:
        """Spec 8.1: run at run boundaries so the WAL does not grow
        unbounded across a 10-hour session."""
        checkpoint_truncate(self._conn)

    # -- cross-thread handoff -------------------------------------------
    def submit(self, job: WriteJob) -> None:
        """Called by any thread other than this object's owner. `job`
        receives the write connection and runs on the owner's next `drain()`
        -- this is the queue spec 8 requires in place of a second writer."""
        self._pending.put(job)

    def drain(self) -> int:
        """Called by the owning thread to apply queued cross-thread writes.
        Returns the number applied."""
        applied = 0
        while True:
            try:
                job = self._pending.get_nowait()
            except queue.Empty:
                break
            job(self._conn)
            applied += 1
        return applied

    # -- task_queue (spec 5) ----------------------------------------------
    def queue_add(
        self,
        *,
        task_id: str,
        spec_path: str,
        depends_on: list[str] | None = None,
        priority: int = 0,
        max_attempts: int,
        allow_test_edits: bool = False,
    ) -> TransitionResult:
        now = utcnow_iso()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO task_queue (
                    task_id, spec_path, depends_on, priority, status,
                    attempt_count, max_attempts, allow_test_edits,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'queued', 0, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    spec_path,
                    json.dumps(depends_on or []),
                    priority,
                    max_attempts,
                    int(allow_test_edits),
                    now,
                    now,
                ),
            )
            return self._record_transition(
                task_id, run_id=None, from_state=None, to_state="queued", now=now
            )

    def queue_retry(self, task_id: str) -> TransitionResult:
        """Reset a `blocked` or `failed_retry` task back to `queued`."""
        now = utcnow_iso()
        with self._conn:
            from_state = self._current_status(task_id)
            self._conn.execute(
                """
                UPDATE task_queue
                SET status = 'queued', blocked_reason = NULL, updated_at = ?
                WHERE task_id = ?
                """,
                (now, task_id),
            )
            return self._record_transition(
                task_id, run_id=None, from_state=from_state, to_state="queued", now=now
            )

    def queue_block(
        self,
        task_id: str,
        blocked_reason: BlockedReason,
        *,
        note: str | None = None,
    ) -> TransitionResult:
        now = utcnow_iso()
        with self._conn:
            from_state = self._current_status(task_id)
            self._conn.execute(
                """
                UPDATE task_queue
                SET status = 'blocked', blocked_reason = ?, last_error = COALESCE(?, last_error),
                    updated_at = ?
                WHERE task_id = ?
                """,
                (blocked_reason.value, note, now, task_id),
            )
            return self._record_transition(
                task_id, run_id=None, from_state=from_state, to_state="blocked", now=now
            )

    def queue_transition(self, task_id: str, to_state: TaskStatus) -> TransitionResult:
        """The generic `task_queue.status` setter Phase 7 needs for every
        state that has no dedicated method above (`proposing`, `proposed`,
        `implementing`, `validating`, `committing`, `merging`,
        `failed_retry`) -- `queued`/`blocked`/`done` keep their own named
        methods above since they also touch other columns
        (`blocked_reason`, `worktree_path`). `run_id` is always `None` here,
        like every other write site in this class -- `task_transitions.
        run_id` has a real, enforced FK to `run_state`, which nothing writes
        a row to until Phase 8's run-level state machine exists."""
        now = utcnow_iso()
        with self._conn:
            from_state = self._current_status(task_id)
            self._conn.execute(
                "UPDATE task_queue SET status = ?, updated_at = ? WHERE task_id = ?",
                (to_state.value, now, task_id),
            )
            return self._record_transition(
                task_id, run_id=None, from_state=from_state, to_state=to_state.value, now=now
            )

    def queue_begin_attempt(self, task_id: str) -> int:
        """Increments `task_queue.attempt_count` -- called once per
        `IMPLEMENTING` entry (the first attempt and every retry), spec 6.3's
        code-level retry budget. Deliberately not folded into
        `queue_transition`: `PROPOSING`/`COMMITTING` also transition through
        this writer but their own retry-once policy (spec 3.3) never touches
        this column (see `docs/v3-implementation-state.md` Phase 7 decision
        on `attempt_count` scope)."""
        now = utcnow_iso()
        with self._conn:
            self._current_status(task_id)  # raises TaskNotFoundError if absent
            self._conn.execute(
                "UPDATE task_queue SET attempt_count = attempt_count + 1, updated_at = ? "
                "WHERE task_id = ?",
                (now, task_id),
            )
            row = self._conn.execute(
                "SELECT attempt_count FROM task_queue WHERE task_id = ?", (task_id,)
            ).fetchone()
            return int(row["attempt_count"])

    def queue_set_worktree_path(self, task_id: str, worktree_path: Path) -> None:
        """Spec 3.2: recorded the moment `git worktree add` succeeds, so a
        crash before the task reaches a terminal state still leaves a trail
        pointing at the worktree (the startup sweep's only source of truth)."""
        now = utcnow_iso()
        with self._conn:
            self._current_status(task_id)  # raises TaskNotFoundError if absent
            self._conn.execute(
                "UPDATE task_queue SET worktree_path = ?, updated_at = ? WHERE task_id = ?",
                (str(worktree_path), now, task_id),
            )

    def queue_complete(self, task_id: str) -> TransitionResult:
        """`DONE`: the worktree has already been removed by the caller (spec
        3.2), so `worktree_path` is cleared here rather than left dangling."""
        now = utcnow_iso()
        with self._conn:
            from_state = self._current_status(task_id)
            self._conn.execute(
                """
                UPDATE task_queue
                SET status = 'done', worktree_path = NULL, updated_at = ?
                WHERE task_id = ?
                """,
                (now, task_id),
            )
            return self._record_transition(
                task_id, run_id=None, from_state=from_state, to_state="done", now=now
            )

    # -- task_failures (spec 9.3) -------------------------------------------
    def record_task_failure(
        self,
        *,
        task_id: str,
        run_id: str | None,
        attempt_number: int,
        failure_type: FailureType,
        failure_stage: FailureStage,
        error_summary: str,
        error_detail: str | None,
        files_touched: list[str],
        will_retry: bool,
        next_action: NextAction,
        event_id: str | None = None,
    ) -> None:
        """The append-only historical trail (spec 8) task_failures feeds --
        this is its first real writer (Phase 1 shipped the table unused).
        Columns match spec 9.3's `task.failed` payload shape exactly rather
        than inventing a parallel structure."""
        now = utcnow_iso()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO task_failures (
                    task_id, run_id, attempt_number, failure_type, failure_stage,
                    error_summary, error_detail, files_touched, will_retry,
                    next_action, timestamp, event_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    run_id,
                    attempt_number,
                    failure_type.value,
                    failure_stage.value,
                    error_summary,
                    error_detail,
                    json.dumps(files_touched),
                    int(will_retry),
                    next_action.value,
                    now,
                    event_id,
                ),
            )

    def _current_status(self, task_id: str) -> str:
        row = self._conn.execute(
            "SELECT status, attempt_count FROM task_queue WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            raise TaskNotFoundError(task_id)
        return str(row["status"])

    def _record_transition(
        self,
        task_id: str,
        *,
        run_id: str | None,
        from_state: str | None,
        to_state: str,
        now: str,
    ) -> TransitionResult:
        attempt_count = self._conn.execute(
            "SELECT attempt_count FROM task_queue WHERE task_id = ?", (task_id,)
        ).fetchone()["attempt_count"]
        self._conn.execute(
            """
            INSERT INTO task_transitions (
                task_id, run_id, from_state, to_state, attempt_number, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (task_id, run_id, from_state, to_state, attempt_count, now),
        )
        return TransitionResult(
            task_id=task_id,
            run_id=run_id,
            from_state=from_state,
            to_state=to_state,
            attempt_number=attempt_count,
        )

    # -- projects (spec 10.4 step 6) ---------------------------------------
    def register_project(
        self,
        *,
        target_path: str,
        harness: str,
        project_template: str | None = None,
    ) -> str:
        project_id = f"{Path(target_path).name}-{_short_id()}"
        now = utcnow_iso()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO projects (
                    project_id, target_path, harness, project_template, initialized_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (project_id, target_path, harness, project_template, now),
            )
        return project_id


def _short_id() -> str:
    return uuid.uuid4().hex[:8]
