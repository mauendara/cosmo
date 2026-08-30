"""Canonical event payload builders shared by every transition writer.

`emit_state_changed` is the one place `task.state_changed`'s payload shape
gets built (Phase 7) -- before this, `cli/main.py`'s `queue_add`/`queue_retry`
commands each hand-built a slightly different payload inline (one included
`attempt_number`, the other didn't), which is exactly the drift spec 9.1's
`schema_version` field exists to let us fix without an archaeology project.
Every `task_queue.status` writer in `store.writer.StoreWriter` returns a
`TransitionResult` precisely so its caller can pass it straight here.
"""

from __future__ import annotations

from cosmo.events.emitter import EventEmitter
from cosmo.events.envelope import Event, EventType
from cosmo.store.enums import Severity
from cosmo.store.writer import TransitionResult


def emit_state_changed(emitter: EventEmitter, result: TransitionResult) -> Event:
    return emitter.emit(
        event_type=EventType.TASK_STATE_CHANGED,
        severity=Severity.INFO,
        run_id=result.run_id,
        task_id=result.task_id,
        payload={
            "from_state": result.from_state,
            "to_state": result.to_state,
            "attempt_number": result.attempt_number,
        },
    )
