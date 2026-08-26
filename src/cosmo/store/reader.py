"""Read-only queries (spec 8's single-writer discipline: reading never needs
the write connection).

Every function here opens its own short-lived `connect_reader` connection --
genuinely read-only at the SQLite level -- and closes it before returning, so
CLI commands stay simple and no read path can accidentally hold a lock.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from cosmo.store.connection import connect_reader


@dataclass(frozen=True, slots=True)
class TaskRow:
    task_id: str
    spec_path: str
    depends_on: list[str]
    priority: int
    status: str
    attempt_count: int
    max_attempts: int
    last_error: str | None
    blocked_reason: str | None
    allow_test_edits: bool
    worktree_path: str | None
    session_id: str | None
    created_at: str
    updated_at: str
    spec_batch_id: str | None = None


@dataclass(frozen=True, slots=True)
class ProgressRow:
    task_id: str
    completed: int
    total: int
    last_label: str | None
    updated_at: str


@dataclass(frozen=True, slots=True)
class EventRow:
    event_id: str
    run_id: str | None
    task_id: str | None
    timestamp: str
    sequence: int
    event_type: str
    severity: str
    schema_version: int
    payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class TaskFailureRow:
    id: int
    task_id: str
    run_id: str | None
    attempt_number: int
    failure_type: str
    failure_stage: str
    error_summary: str
    error_detail: str | None
    files_touched: list[str]
    will_retry: bool
    next_action: str
    timestamp: str


@dataclass(frozen=True, slots=True)
class RunRow:
    run_id: str
    status: str
    harness: str
    permission_mode: str
    max_turns: int
    base_branch: str
    pause_reason: str | None
    stop_reason: str | None
    started_at: str
    updated_at: str
    stopped_at: str | None


@dataclass(frozen=True, slots=True)
class ProjectRow:
    project_id: str
    target_path: str
    harness: str
    project_template: str | None
    initialized_at: str


def _task_from_row(row: sqlite3.Row) -> TaskRow:
    return TaskRow(
        task_id=row["task_id"],
        spec_path=row["spec_path"],
        depends_on=json.loads(row["depends_on"]),
        priority=row["priority"],
        status=row["status"],
        attempt_count=row["attempt_count"],
        max_attempts=row["max_attempts"],
        last_error=row["last_error"],
        blocked_reason=row["blocked_reason"],
        allow_test_edits=bool(row["allow_test_edits"]),
        worktree_path=row["worktree_path"],
        session_id=row["session_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        spec_batch_id=row["spec_batch_id"],
    )


def list_tasks(db_path: Path, *, status: str | None = None) -> list[TaskRow]:
    if not db_path.exists():
        return []
    conn = connect_reader(db_path)
    try:
        if status is not None:
            rows = conn.execute(
                "SELECT * FROM task_queue WHERE status = ? ORDER BY priority DESC, created_at",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM task_queue ORDER BY priority DESC, created_at"
            ).fetchall()
        return [_task_from_row(r) for r in rows]
    finally:
        conn.close()


def get_task(db_path: Path, task_id: str) -> TaskRow | None:
    if not db_path.exists():
        return None
    conn = connect_reader(db_path)
    try:
        row = conn.execute("SELECT * FROM task_queue WHERE task_id = ?", (task_id,)).fetchone()
        return _task_from_row(row) if row is not None else None
    finally:
        conn.close()


def get_progress(db_path: Path, task_id: str) -> ProgressRow | None:
    if not db_path.exists():
        return None
    conn = connect_reader(db_path)
    try:
        row = conn.execute("SELECT * FROM task_progress WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        return ProgressRow(
            task_id=row["task_id"],
            completed=row["completed"],
            total=row["total"],
            last_label=row["last_label"],
            updated_at=row["updated_at"],
        )
    finally:
        conn.close()


def list_events(
    db_path: Path,
    *,
    run_id: str | None = None,
    task_id: str | None = None,
    severity: str | None = None,
    event_type: str | None = None,
    limit: int = 50,
) -> list[EventRow]:
    if not db_path.exists():
        return []
    conn = connect_reader(db_path)
    try:
        clauses: list[str] = []
        params: list[str] = []
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        if task_id is not None:
            clauses.append("task_id = ?")
            params.append(task_id)
        if severity is not None:
            clauses.append("severity = ?")
            params.append(severity)
        if event_type is not None:
            clauses.append("event_type = ?")
            params.append(event_type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM events {where} ORDER BY timestamp DESC, sequence DESC LIMIT ?"
        rows = conn.execute(sql, (*params, limit)).fetchall()
        return [
            EventRow(
                event_id=r["event_id"],
                run_id=r["run_id"],
                task_id=r["task_id"],
                timestamp=r["timestamp"],
                sequence=r["sequence"],
                event_type=r["event_type"],
                severity=r["severity"],
                schema_version=r["schema_version"],
                payload=json.loads(r["payload"]),
            )
            for r in rows
        ]
    finally:
        conn.close()


def list_task_failures(
    db_path: Path, task_id: str, *, run_id: str | None = None
) -> list[TaskFailureRow]:
    """Feeds spec 6.3's informed retries: `record_task_failure` already
    persists everything the retry prompt needs (`error_detail`, which stage,
    which attempt), so the state machine reads it back here rather than
    threading a `GateResult`/failure detail across the retry boundary by
    hand. `run_id` (Phase 8) narrows to failures recorded during one run --
    the circuit breaker's per-task `environment_error` tally needs exactly
    that scope, not a task's full cross-run history."""
    if not db_path.exists():
        return []
    conn = connect_reader(db_path)
    try:
        if run_id is not None:
            rows = conn.execute(
                "SELECT * FROM task_failures WHERE task_id = ? AND run_id = ? ORDER BY id",
                (task_id, run_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM task_failures WHERE task_id = ? ORDER BY id", (task_id,)
            ).fetchall()
        return [
            TaskFailureRow(
                id=r["id"],
                task_id=r["task_id"],
                run_id=r["run_id"],
                attempt_number=r["attempt_number"],
                failure_type=r["failure_type"],
                failure_stage=r["failure_stage"],
                error_summary=r["error_summary"],
                error_detail=r["error_detail"],
                files_touched=json.loads(r["files_touched"]),
                will_retry=bool(r["will_retry"]),
                next_action=r["next_action"],
                timestamp=r["timestamp"],
            )
            for r in rows
        ]
    finally:
        conn.close()


def get_run(db_path: Path, run_id: str) -> RunRow | None:
    if not db_path.exists():
        return None
    conn = connect_reader(db_path)
    try:
        row = conn.execute("SELECT * FROM run_state WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return RunRow(
            run_id=row["run_id"],
            status=row["status"],
            harness=row["harness"],
            permission_mode=row["permission_mode"],
            max_turns=row["max_turns"],
            base_branch=row["base_branch"],
            pause_reason=row["pause_reason"],
            stop_reason=row["stop_reason"],
            started_at=row["started_at"],
            updated_at=row["updated_at"],
            stopped_at=row["stopped_at"],
        )
    finally:
        conn.close()


def latest_run_id(db_path: Path) -> str | None:
    """The most recently started run -- `cosmo report`'s default target
    when `--run` is omitted (Phase 9)."""
    if not db_path.exists():
        return None
    conn = connect_reader(db_path)
    try:
        row = conn.execute(
            "SELECT run_id FROM run_state ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        return str(row["run_id"]) if row is not None else None
    finally:
        conn.close()


def get_run_cost(db_path: Path, run_id: str) -> float:
    if not db_path.exists():
        return 0.0
    conn = connect_reader(db_path)
    try:
        row = conn.execute(
            "SELECT total_cost_usd FROM run_cost WHERE run_id = ?", (run_id,)
        ).fetchone()
        return float(row["total_cost_usd"]) if row is not None else 0.0
    finally:
        conn.close()


def get_task_cost(db_path: Path, task_id: str) -> float:
    if not db_path.exists():
        return 0.0
    conn = connect_reader(db_path)
    try:
        row = conn.execute(
            "SELECT total_cost_usd FROM task_cost WHERE task_id = ?", (task_id,)
        ).fetchone()
        return float(row["total_cost_usd"]) if row is not None else 0.0
    finally:
        conn.close()


def find_project_by_path(db_path: Path, target_path: str) -> ProjectRow | None:
    if not db_path.exists():
        return None
    conn = connect_reader(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM projects WHERE target_path = ?", (target_path,)
        ).fetchone()
        return _project_from_row(row) if row is not None else None
    finally:
        conn.close()


def list_projects(db_path: Path) -> list[ProjectRow]:
    if not db_path.exists():
        return []
    conn = connect_reader(db_path)
    try:
        rows = conn.execute("SELECT * FROM projects ORDER BY initialized_at").fetchall()
        return [_project_from_row(r) for r in rows]
    finally:
        conn.close()


def _project_from_row(row: sqlite3.Row) -> ProjectRow:
    return ProjectRow(
        project_id=row["project_id"],
        target_path=row["target_path"],
        harness=row["harness"],
        project_template=row["project_template"],
        initialized_at=row["initialized_at"],
    )
