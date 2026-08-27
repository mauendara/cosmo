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
from cosmo.store.enums import (
    BlockedReason,
    FailureStage,
    FailureType,
    NextAction,
    PauseReason,
    RunStatus,
    StopReason,
    TaskStatus,
)
from cosmo.store.failure_signature import classify_failure_signature
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
        spec_batch_id: str | None = None,
    ) -> TransitionResult:
        now = utcnow_iso()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO task_queue (
                    task_id, spec_path, depends_on, priority, status,
                    attempt_count, max_attempts, allow_test_edits,
                    created_at, updated_at, spec_batch_id
                ) VALUES (?, ?, ?, ?, 'queued', 0, ?, ?, ?, ?, ?)
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
                    spec_batch_id,
                ),
            )
            return self._record_transition(
                task_id, run_id=None, from_state=None, to_state="queued", now=now
            )

    def queue_retry(
        self, task_id: str, *, run_id: str | None = None, clear_worktree: bool = True
    ) -> TransitionResult:
        """Reset a `blocked` or `failed_retry` task back to `queued` -- a
        genuine fresh start, not a continuation: `attempt_count` resets to 0
        (found by hand: leaving it as-is meant a task retried after
        exhausting `max_attempts` was already back over budget on its very
        next real failure, retry in name only) regardless of `clear_
        worktree`.

        `clear_worktree=True` (the default) also clears `worktree_path` --
        the caller (`cli.main.queue_retry`) is responsible for physically
        removing the worktree first, same convention `queue_complete`
        already uses for the same column. Pass `clear_worktree=False` when
        the caller instead did a soft reset (`git.worktree.
        reset_worktree_to_commit`, discarding a failed implementation
        attempt back to PROPOSING's own commit) and kept the worktree at
        the same path -- `worktree_path` must stay exactly as it was so the
        next `run_task` reuses it rather than creating a redundant one."""
        now = utcnow_iso()
        with self._conn:
            from_state = self._current_status(task_id)
            if clear_worktree:
                self._conn.execute(
                    """
                    UPDATE task_queue
                    SET status = 'queued', blocked_reason = NULL, attempt_count = 0,
                        worktree_path = NULL, updated_at = ?
                    WHERE task_id = ?
                    """,
                    (now, task_id),
                )
            else:
                self._conn.execute(
                    """
                    UPDATE task_queue
                    SET status = 'queued', blocked_reason = NULL, attempt_count = 0,
                        updated_at = ?
                    WHERE task_id = ?
                    """,
                    (now, task_id),
                )
            return self._record_transition(
                task_id, run_id=run_id, from_state=from_state, to_state="queued", now=now
            )

    def queue_block(
        self,
        task_id: str,
        blocked_reason: BlockedReason,
        *,
        run_id: str | None = None,
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
                task_id, run_id=run_id, from_state=from_state, to_state="blocked", now=now
            )

    def queue_transition(
        self, task_id: str, to_state: TaskStatus, *, run_id: str | None = None
    ) -> TransitionResult:
        """The generic `task_queue.status` setter Phase 7 needs for every
        state that has no dedicated method above (`proposing`, `proposed`,
        `implementing`, `validating`, `committing`, `merging`,
        `failed_retry`, and now `queued` again for Phase 8's run-wall-clock
        requeue) -- `queued`/`blocked`/`done` also keep their own named
        methods above since those touch other columns (`blocked_reason`,
        `worktree_path`). `run_id` defaults to `None` for every caller
        outside a run (the CLI's standalone `queue retry`/`queue block`
        commands, which have no run to attribute to) -- `task.machine.
        run_task` (Phase 8) is the one caller that now passes a real value."""
        now = utcnow_iso()
        with self._conn:
            from_state = self._current_status(task_id)
            self._conn.execute(
                "UPDATE task_queue SET status = ?, updated_at = ? WHERE task_id = ?",
                (to_state.value, now, task_id),
            )
            return self._record_transition(
                task_id, run_id=run_id, from_state=from_state, to_state=to_state.value, now=now
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

    def queue_clear_worktree_path(self, task_id: str) -> None:
        """Nulls `worktree_path` without touching `status` -- used by
        `run.recovery.reconcile_interrupted_tasks` (v5 improvements plan
        part 1), where the directory is already gone (the startup worktree
        sweep already pruned it) but the DB row still points at it."""
        now = utcnow_iso()
        with self._conn:
            self._current_status(task_id)  # raises TaskNotFoundError if absent
            self._conn.execute(
                "UPDATE task_queue SET worktree_path = NULL, updated_at = ? WHERE task_id = ?",
                (now, task_id),
            )

    def queue_complete(self, task_id: str, *, run_id: str | None = None) -> TransitionResult:
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
                task_id, run_id=run_id, from_state=from_state, to_state="done", now=now
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
        # v5 improvements plan part 5 (Class 1): computed here, at the one
        # real writer of this table, so every caller gets it for free --
        # `error_summary` alone can't tell two build failures with different
        # root causes apart (see the classifier's own docstring).
        failure_signature = classify_failure_signature(error_detail)
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO task_failures (
                    task_id, run_id, attempt_number, failure_type, failure_stage,
                    error_summary, error_detail, files_touched, will_retry,
                    next_action, timestamp, event_id, failure_signature
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    failure_signature,
                ),
            )

    # -- run_state / run_cost / task_cost (spec 3.1, 7.3) -- Phase 8's own
    # first real writer; the tables shipped unused since Phase 1. ----------
    def run_create(
        self,
        *,
        run_id: str,
        harness: str,
        permission_mode: str,
        max_turns: int,
        base_branch: str,
    ) -> None:
        """Inserts at `idle` (spec 3.1's own starting state) -- the caller
        transitions to `running` via `run_transition` immediately after, the
        same "insert, then transition" split `queue_add`/`queue_transition`
        already use for tasks, so `run.started` has a real row to reference
        by the time it's emitted."""
        now = utcnow_iso()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO run_state (
                    run_id, status, harness, permission_mode, max_turns, base_branch,
                    started_at, updated_at
                ) VALUES (?, 'idle', ?, ?, ?, ?, ?, ?)
                """,
                (run_id, harness, permission_mode, max_turns, base_branch, now, now),
            )
            self._conn.execute(
                "INSERT INTO run_cost (run_id, total_cost_usd, updated_at) VALUES (?, 0.0, ?)",
                (run_id, now),
            )

    def run_transition(
        self,
        run_id: str,
        status: RunStatus,
        *,
        pause_reason: PauseReason | None = None,
        stop_reason: StopReason | None = None,
    ) -> None:
        """`pause_reason`/`stop_reason` are set exactly on the transition
        that carries them and left alone otherwise (`COALESCE`-free here,
        unlike `stopped_at` below, since a later `RUNNING`->`PAUSED` cycle
        with a *different* reason should overwrite the old one outright, not
        retain it)."""
        now = utcnow_iso()
        stopped_at = now if status is RunStatus.STOPPED else None
        with self._conn:
            self._conn.execute(
                """
                UPDATE run_state
                SET status = ?, pause_reason = ?, stop_reason = ?, updated_at = ?,
                    stopped_at = COALESCE(?, stopped_at)
                WHERE run_id = ?
                """,
                (
                    status.value,
                    pause_reason.value if pause_reason is not None else None,
                    stop_reason.value if stop_reason is not None else None,
                    now,
                    stopped_at,
                    run_id,
                ),
            )

    def run_cost_add(self, run_id: str, delta_usd: float) -> float:
        """UPSERT-accumulate (spec 8: current-state, one row per run, never
        one row per tick). Returns the new running total so the caller
        (spec 7.3's ceiling/80%-warning checks) doesn't need a second
        query."""
        now = utcnow_iso()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO run_cost (run_id, total_cost_usd, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    total_cost_usd = total_cost_usd + excluded.total_cost_usd,
                    updated_at = excluded.updated_at
                """,
                (run_id, delta_usd, now),
            )
            row = self._conn.execute(
                "SELECT total_cost_usd FROM run_cost WHERE run_id = ?", (run_id,)
            ).fetchone()
            return float(row["total_cost_usd"])

    def task_cost_add(self, task_id: str, delta_usd: float) -> float:
        """Deliberately lifetime-accumulated per `task_id`, not per-run:
        `task_cost`'s schema (Phase 1) has no `run_id` column, matching
        spec 8's own framing ("accumulated per-task cost", not
        per-task-per-run). A task once `BLOCKED` with `blocked_reason=cost`
        therefore stays over its ceiling across a later `queue retry` unless
        `cost.max_cost_per_task_usd` is raised -- the ceiling is a real
        budget, not something a retry silently resets."""
        now = utcnow_iso()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO task_cost (task_id, total_cost_usd, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    total_cost_usd = total_cost_usd + excluded.total_cost_usd,
                    updated_at = excluded.updated_at
                """,
                (task_id, delta_usd, now),
            )
            row = self._conn.execute(
                "SELECT total_cost_usd FROM task_cost WHERE task_id = ?", (task_id,)
            ).fetchone()
            return float(row["total_cost_usd"])

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
