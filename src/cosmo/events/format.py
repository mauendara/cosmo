"""One human-readable line per event type, shared by every consumer that
turns an `Event` into text for a person: `cosmo run`'s live terminal
(`cli.main._print_emit`) and the Telegram sink (`notify.telegram.
format_event`). Started as two near-identical, slowly-drifting copies of
"what does a task.validation_result payload mean" (deviation 75's terminal
fix, then the Telegram format rework) -- this module exists so there is one
answer, not two.

`event_detail` never raises and never needs the caller to know a payload's
shape: an event type it doesn't recognize, or one missing the fields it
looks for, gets `""` back, and the caller decides what to do with that (the
terminal shows nothing extra; the Telegram sink falls back to the raw
payload).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cosmo.events.envelope import Event, EventType

WATCH_STALE_EVENT_TYPE = "watch.stale"
"""Not a real `EventType` member -- `notify.watch` constructs this event
itself, synthetically, when the `events` table has gone quiet (see that
module's own docstring). Named here, not there, so this module doesn't have
to import `notify.watch` to recognize it."""


def _validation_result_detail(payload: dict[str, object], task_id: str | None) -> str:
    parts = [f"passed={payload.get('passed')}"]
    for stage_name in ("unit", "e2e"):
        stage = payload.get(stage_name)
        if not isinstance(stage, dict):
            continue
        label = "pass" if stage.get("passed") else "FAIL"
        passed_n, failed_n, skipped_n = (
            stage.get("passed_count"),
            stage.get("failed_count"),
            stage.get("skipped_count"),
        )
        counts = (
            f" ({passed_n}p/{failed_n}f/{skipped_n}s)"
            if passed_n is not None or failed_n is not None
            else ""
        )
        parts.append(f"{stage_name}={label}{counts}")
    detail = ", ".join(parts)
    if not payload.get("passed"):
        detail += f" -- see `cosmo queue failures {task_id}` for detail"
    return detail


def _run_paused_detail(payload: dict[str, object]) -> str:
    reason = payload.get("reason")
    parts = [f"reason={reason}"] if reason is not None else []
    resume_delay = payload.get("resume_delay_seconds")
    if isinstance(resume_delay, int | float):
        eta = datetime.now(UTC) + timedelta(seconds=resume_delay)
        parts.append(f"resume at {eta.strftime('%Y-%m-%d %H:%M UTC')}")
    return ", ".join(parts)


def _run_summary_detail(payload: dict[str, object]) -> str:
    duration = payload.get("total_duration_seconds")
    duration_str = f"{duration / 60:.1f}min" if isinstance(duration, int | float) else "?"
    cost = payload.get("total_cost_usd")
    cost_str = f"${cost:.2f}" if isinstance(cost, int | float) else "?"
    return (
        f"completed={payload.get('completed')}, blocked={payload.get('blocked')}, "
        f"retried={payload.get('retried')}, duration={duration_str}, cost={cost_str}"
    )


def event_detail(event: Event) -> str:
    """A short, human-readable phrase summarizing `event`'s payload -- no
    leading/trailing whitespace, no task/run id (callers already have those
    on `event` itself and place them wherever fits their own format).
    `""` means this event type has nothing type-specific to add."""
    payload = event.payload
    match event.event_type:
        case EventType.TASK_VALIDATION_RESULT.value:
            return _validation_result_detail(payload, event.task_id)
        case EventType.TASK_STATE_CHANGED.value:
            return f"{payload.get('from_state')} -> {payload.get('to_state')}"
        case EventType.RUN_PAUSED.value:
            return _run_paused_detail(payload)
        case EventType.RUN_RESUMED.value:
            return ""
        case EventType.TASK_BLOCKED.value:
            note = payload.get("note")
            base = f"reason={payload.get('blocked_reason')}"
            return f"{base}, note={note}" if note else base
        case EventType.TASK_FINISHING_FAILED.value:
            return f"spec={payload.get('spec_id')}: {payload.get('error')}"
        case EventType.TASK_COMPLETED.value:
            return ""
        case EventType.TASK_INTERRUPTED.value:
            return f"found mid-flight in status={payload.get('previous_status')}, requeued"
        case EventType.RUN_COST_WARNING.value:
            total = payload.get("total_cost_usd")
            limit = payload.get("limit_usd")
            if isinstance(total, int | float) and isinstance(limit, int | float):
                return f"${total:.2f} / ${limit:.2f} limit"
            return ""
        case EventType.QUOTA_BYPASSED.value:
            cost = payload.get("run_cost_so_far_usd")
            cost_str = f"${cost:.2f}" if isinstance(cost, int | float) else "?"
            return f"resets_at={payload.get('resets_at')}, cost_so_far={cost_str}"
        case EventType.RUN_STOPPED.value:
            reason = payload.get("reason")
            return f"reason={reason}" if reason is not None else ""
        case EventType.RUN_SUMMARY.value:
            return _run_summary_detail(payload)
        case _ if event.event_type == WATCH_STALE_EVENT_TYPE:
            return f"no events for {payload.get('stale_after_seconds')}s -- run loop may be dead"
        case _:
            return ""
