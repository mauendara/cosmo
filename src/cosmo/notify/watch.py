"""`cosmo notify watch` (v5 improvements plan part 3): a small, separate,
always-on process that polls the `events` table and forwards anything worth
a phone notification to a `Sink`. Deliberately never runs inside the
run-loop process -- a sink call inline in `EventEmitter.emit()` cannot
notify about the run loop's *own* crash, since whatever would send that
message dies with the process.

Also the one thing that can raise a crash-like alarm *before* the next
`cosmo run` invocation's own startup reconciliation (`run.recovery.
reconcile_interrupted_tasks`) ever gets a chance to: if the `events` table
has gone quiet for `stale_after_seconds` while the run hasn't reached a
terminal `run_state` status, that silence is itself the signal -- there is
no row to read (nothing is being written by a dead process), so the
watcher constructs the alert message itself rather than waiting for a
payload field on an event that will never arrive.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cosmo.config.model import NotifyConfig
from cosmo.events.envelope import EVENT_SCHEMA_VERSION, Event, EventType
from cosmo.notify.sink import Sink
from cosmo.store.clock import utcnow_iso
from cosmo.store.enums import Severity
from cosmo.store.reader import EventRow, latest_event_rowid, list_events_after

_SEVERITY_ORDER = {"info": 0, "warning": 1, "error": 2, "critical": 3}
_ALWAYS_NOTIFY_TYPES = frozenset({EventType.RUN_SUMMARY.value, EventType.RUN_STOPPED.value})


def _should_notify(row: EventRow, min_severity: Severity) -> bool:
    """`RUN_SUMMARY`/`RUN_STOPPED` are always notification-worthy even
    though they're emitted at `severity=info` (a run ending matters
    regardless); everything else is gated on `min_severity` (part 3's
    `[notify]` config, `WARNING`+ by default)."""
    if row.event_type in _ALWAYS_NOTIFY_TYPES:
        return True
    return _SEVERITY_ORDER.get(row.severity, 0) >= _SEVERITY_ORDER[min_severity.value]


def _event_row_to_event(row: EventRow) -> Event:
    return Event(
        event_id=row.event_id,
        run_id=row.run_id,
        task_id=row.task_id,
        timestamp=row.timestamp,
        sequence=row.sequence,
        event_type=row.event_type,
        severity=Severity(row.severity),
        schema_version=row.schema_version,
        payload=row.payload,
    )


def _stale_event(stale_after_seconds: int) -> Event:
    return Event(
        event_id=str(uuid.uuid4()),
        run_id=None,
        task_id=None,
        timestamp=utcnow_iso(),
        sequence=0,
        event_type="watch.stale",
        severity=Severity.WARNING,
        schema_version=EVENT_SCHEMA_VERSION,
        payload={"stale_after_seconds": stale_after_seconds},
    )


@dataclass(slots=True)
class WatchState:
    """Carried across polls as a plain object (not closure locals) so one
    poll (`watch_once`) is independently testable without driving a whole
    `run_watch_loop`."""

    since_rowid: int
    last_activity_monotonic: float
    alerted_stale: bool = False


def watch_once(
    *, db_path: Path, sink: Sink, config: NotifyConfig, state: WatchState, now_monotonic: float
) -> None:
    """One poll: forward any new, notification-worthy events; raise (once,
    not on every subsequent tick) a staleness alert if nothing has landed
    for `config.stale_after_seconds`. Mutates `state` in place."""
    new_rows = list_events_after(db_path, state.since_rowid)
    if new_rows:
        state.last_activity_monotonic = now_monotonic
        state.alerted_stale = False
    for rowid, row in new_rows:
        state.since_rowid = rowid
        if _should_notify(row, config.min_severity):
            sink.send(_event_row_to_event(row))

    if (
        not state.alerted_stale
        and now_monotonic - state.last_activity_monotonic >= config.stale_after_seconds
    ):
        sink.send(_stale_event(config.stale_after_seconds))
        state.alerted_stale = True


def run_watch_loop(
    *,
    db_path: Path,
    sink: Sink,
    config: NotifyConfig,
    poll_interval_seconds: float = 15.0,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    max_iterations: int | None = None,
) -> None:
    """Runs forever (`cosmo notify watch`'s own process is the lifetime
    boundary) unless `max_iterations` is given -- a test-only escape hatch,
    matching `task.timeouts`'s own injectable-clock testing posture."""
    state = WatchState(since_rowid=latest_event_rowid(db_path), last_activity_monotonic=monotonic())
    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        sleep(poll_interval_seconds)
        watch_once(
            db_path=db_path, sink=sink, config=config, state=state, now_monotonic=monotonic()
        )
        iterations += 1
