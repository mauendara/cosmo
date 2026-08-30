"""Progress and liveness watching (spec 4).

Two signals, per spec, stored separately and never as one combined percent:
`task_progress` (numerator/denominator/last label) and `task_heartbeat`
(last-activity timestamp plus an explicit `source`). Both are UPSERTs, one
row per task, written through `StoreWriter.submit()`/`drain()` -- Phase 1's
cross-thread handoff, exercised by `test_store_writer.py` but with no real
background-thread caller until now.

`HeartbeatSource.STREAM` is never produced here. Nothing in the current
`HarnessAdapter` ABC exposes a live per-event callback during a blocking
`implement()`/`propose()` call (an adapter's own structured-stream parsing,
where it has one, is entirely internal to that one call) -- see
`docs/v3-implementation-state.md`'s
Phase 7 section. `FILE` is used for a `watchdog`-detected write to the
change's `tasks.md`; `MTIME` is used for the periodic poll fallback (spec
4's own "5-10s" cadence) -- including, by extension, polling an adapter's
*native* `get_progress()` (`HarnessCapabilities.reports_native_progress`),
since that path is also poll-driven and the schema has no fourth source
value for it.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from cosmo.events.emitter import EventEmitter
from cosmo.events.envelope import EventType
from cosmo.proc.timers import LivenessTimers
from cosmo.store.clock import utcnow_iso
from cosmo.store.enums import HeartbeatSource, Severity
from cosmo.store.writer import StoreWriter

if TYPE_CHECKING:
    from watchdog.observers.api import BaseObserver

_CHECKBOX_RE = re.compile(r"^-\s\[([ xX])\]\s+(.+?)\s*$")

ReadProgress = Callable[[], tuple[int, int, str | None]]


def parse_tasks_md(text: str) -> tuple[int, int, str | None]:
    """`- [ ] N.M Description` / `- [x] N.M Description` (the literal format
    `templates/harness/claude/skills/openspec-workflow/SKILL.md` documents
    as the contract). Returns (completed, total, label of the last checked
    box in document order) -- never a precomputed percent (spec 4)."""
    completed = 0
    total = 0
    last_label: str | None = None
    for line in text.splitlines():
        m = _CHECKBOX_RE.match(line)
        if m is None:
            continue
        total += 1
        if m.group(1) in ("x", "X"):
            completed += 1
            last_label = m.group(2)
    return completed, total, last_label


def read_progress_from_file(tasks_md_path: Path) -> tuple[int, int, str | None]:
    if not tasks_md_path.is_file():
        return (0, 0, None)
    return parse_tasks_md(tasks_md_path.read_text(encoding="utf-8"))


class ProgressWatcher:
    """Bound to one task for the lifetime of one `IMPLEMENTING` attempt.
    `check()` is the only thing that writes state, and is safe to call from
    any thread (it goes through `writer.submit()`, never the connection
    directly) -- both the periodic `on_tick` caller
    (`task.timeouts.run_with_liveness_timeout`) and the `watchdog` observer
    thread (file mode only) call it.
    """

    def __init__(
        self,
        *,
        task_id: str,
        run_id: str | None,
        state: str,
        writer: StoreWriter,
        emitter: EventEmitter,
        read_progress: ReadProgress,
        tasks_md_path: Path | None = None,
        timers: LivenessTimers | None = None,
    ) -> None:
        self._task_id = task_id
        self._run_id = run_id
        self._state = state
        self._writer = writer
        self._emitter = emitter
        self._read_progress = read_progress
        self._tasks_md_path = tasks_md_path
        self._timers = timers
        self._last_seen: tuple[int, int, str | None] | None = None
        self._observer: BaseObserver | None = None

    def start(self) -> None:
        if self._tasks_md_path is None:
            return
        handler = _TasksMdHandler(self._tasks_md_path, self)
        observer = Observer()
        # watchdog watches a directory, not a single file -- the change's
        # own directory may not exist yet at the moment IMPLEMENTING starts
        # (the harness creates tasks.md itself), so watch the parent and
        # filter events to the exact path in the handler.
        watch_dir = self._tasks_md_path.parent
        watch_dir.mkdir(parents=True, exist_ok=True)
        observer.schedule(handler, str(watch_dir), recursive=False)
        observer.start()
        self._observer = observer

    def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5.0)
            self._observer = None

    def check(self, source: HeartbeatSource) -> None:
        """Safe to call from any thread: every write -- both the SQL
        UPSERTs and the event emissions -- goes through `writer.submit()`,
        never the connection or the emitter directly. `EventEmitter.emit`
        reads/writes `self._writer.connection` internally with no locking of
        its own (spec 8's single-writer discipline assumes one thread calls
        it), so calling it eagerly from the `watchdog` observer's own thread
        -- a real bug an earlier version of this method had, caught by
        `test_watchdog_observer_detects_a_real_write_to_tasks_md` raising
        `sqlite3.ProgrammingError` -- would violate that discipline exactly
        the way a second write connection would."""
        current = self._read_progress()
        now = utcnow_iso()
        changed = current != self._last_seen
        self._last_seen = current
        completed, total, last_label = current

        if changed:
            self._writer.submit(
                _progress_job(self._emitter, self._run_id, self._task_id, current, now)
            )
            if self._timers is not None:
                self._timers.poke()

        self._writer.submit(
            _heartbeat_job(self._emitter, self._run_id, self._task_id, self._state, source, now)
        )


def _progress_job(
    emitter: EventEmitter,
    run_id: str | None,
    task_id: str,
    current: tuple[int, int, str | None],
    now: str,
) -> Callable[[sqlite3.Connection], None]:
    completed, total, last_label = current

    def _job(conn: sqlite3.Connection) -> None:
        with conn:
            conn.execute(
                """
                INSERT INTO task_progress (task_id, completed, total, last_label, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    completed = excluded.completed,
                    total = excluded.total,
                    last_label = excluded.last_label,
                    updated_at = excluded.updated_at
                """,
                (task_id, completed, total, last_label, now),
            )
        emitter.emit(
            event_type=EventType.TASK_PROGRESS,
            severity=Severity.INFO,
            run_id=run_id,
            task_id=task_id,
            payload={"completed": completed, "total": total, "last_label": last_label},
        )

    return _job


def _heartbeat_job(
    emitter: EventEmitter,
    run_id: str | None,
    task_id: str,
    state: str,
    source: HeartbeatSource,
    now: str,
) -> Callable[[sqlite3.Connection], None]:
    """`state_entered_at` resets only when `state` actually changes from
    whatever `task_heartbeat` already holds -- SQLite's upsert lets the SET
    clause reference the pre-existing row (`task_heartbeat.state`) alongside
    the incoming one (`excluded.state`), so this needs no Python-side
    "is this the first check for this state" tracking, which would be wrong
    anyway: `task_heartbeat` is one row per *task*, not per attempt, and a
    fresh `ProgressWatcher` is built for every `IMPLEMENTING` attempt."""

    def _job(conn: sqlite3.Connection) -> None:
        with conn:
            conn.execute(
                """
                INSERT INTO task_heartbeat (
                    task_id, state, state_entered_at, last_activity_at, source, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    state = excluded.state,
                    state_entered_at = CASE
                        WHEN task_heartbeat.state = excluded.state
                        THEN task_heartbeat.state_entered_at
                        ELSE excluded.state_entered_at
                    END,
                    last_activity_at = excluded.last_activity_at,
                    source = excluded.source,
                    updated_at = excluded.updated_at
                """,
                (task_id, state, now, now, source.value, now),
            )
        emitter.emit(
            event_type=EventType.TASK_HEARTBEAT,
            severity=Severity.INFO,
            run_id=run_id,
            task_id=task_id,
            payload={"state": state, "source": source.value},
        )

    return _job


class _TasksMdHandler(FileSystemEventHandler):
    def __init__(self, tasks_md_path: Path, watcher: ProgressWatcher) -> None:
        self._path = tasks_md_path.resolve()
        self._watcher = watcher

    def on_modified(self, event: FileSystemEvent) -> None:
        self._dispatch(event)

    def on_created(self, event: FileSystemEvent) -> None:
        self._dispatch(event)

    def _dispatch(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        if Path(str(event.src_path)).resolve() != self._path:
            return
        self._watcher.check(HeartbeatSource.FILE)
