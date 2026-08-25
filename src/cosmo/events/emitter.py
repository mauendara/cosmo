"""Event emission with transactional sequence allocation (spec 9.1).

`sequence` is monotonic per scope (a run, or '' for run-less project-level
events) and is bumped in the same transaction as the event row it numbers, so
a crash between the two is impossible: either both land, or neither does.
That is the whole of the "no gaps or duplicates" guarantee -- it falls out of
using one `UPDATE ... RETURNING` and one `INSERT` inside a single
`sqlite3` transaction, not from any extra bookkeeping.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from cosmo.events.envelope import EVENT_SCHEMA_VERSION, Event, EventType
from cosmo.store.clock import utcnow_iso
from cosmo.store.enums import Severity
from cosmo.store.writer import StoreWriter


class EventEmitter:
    """Bound to one `StoreWriter` -- and therefore to the single write
    connection the main loop owns (spec 8) -- for its whole lifetime."""

    def __init__(self, writer: StoreWriter) -> None:
        self._writer = writer

    def emit(
        self,
        *,
        event_type: EventType,
        severity: Severity,
        run_id: str | None = None,
        task_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Event:
        conn = self._writer.connection
        scope = run_id or ""
        event_id = str(uuid.uuid4())
        timestamp = utcnow_iso()
        body = payload or {}
        with conn:
            cur = conn.execute(
                """
                INSERT INTO event_sequence (scope, next_value) VALUES (?, 1)
                ON CONFLICT(scope) DO UPDATE SET next_value = next_value + 1
                RETURNING next_value
                """,
                (scope,),
            )
            row = cur.fetchone()
            sequence = int(row[0])
            conn.execute(
                """
                INSERT INTO events (
                    event_id, run_id, task_id, timestamp, sequence,
                    event_type, severity, schema_version, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    run_id,
                    task_id,
                    timestamp,
                    sequence,
                    event_type.value,
                    severity.value,
                    EVENT_SCHEMA_VERSION,
                    json.dumps(body),
                ),
            )
        return Event(
            event_id=event_id,
            run_id=run_id,
            task_id=task_id,
            timestamp=timestamp,
            sequence=sequence,
            event_type=event_type.value,
            severity=severity,
            schema_version=EVENT_SCHEMA_VERSION,
            payload=body,
        )
