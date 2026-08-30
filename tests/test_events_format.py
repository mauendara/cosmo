"""`events.format.event_detail` -- the human-readable-phrase builder shared
by `cli.main._print_emit` (the live terminal) and `notify.telegram.
format_event` (Telegram). Tested directly here so both consumers can trust
it rather than re-verifying payload shapes themselves."""

from __future__ import annotations

from cosmo.events.envelope import EVENT_SCHEMA_VERSION, Event, EventType
from cosmo.events.format import WATCH_STALE_EVENT_TYPE, event_detail
from cosmo.store.enums import Severity


def _event(event_type: str, payload: dict[str, object], task_id: str | None = None) -> Event:
    return Event(
        event_id="e1",
        run_id="run-1",
        task_id=task_id,
        timestamp="2026-01-01T00:00:00+00:00",
        sequence=1,
        event_type=event_type,
        severity=Severity.INFO,
        schema_version=EVENT_SCHEMA_VERSION,
        payload=payload,
    )


def test_unknown_event_type_returns_empty_string() -> None:
    assert event_detail(_event("some.unrecognized.type", {"a": 1})) == ""


def test_validation_result_passing_has_no_failures_pointer() -> None:
    payload = {
        "passed": True,
        "unit": {"passed": True, "passed_count": 4, "failed_count": 0, "skipped_count": 0},
        "e2e": {"passed": True, "passed_count": 2, "failed_count": 0, "skipped_count": 0},
    }
    detail = event_detail(_event(EventType.TASK_VALIDATION_RESULT.value, payload, "t1"))
    assert "passed=True" in detail
    assert "unit=pass (4p/0f/0s)" in detail
    assert "e2e=pass (2p/0f/0s)" in detail
    assert "queue failures" not in detail


def test_validation_result_failing_points_at_queue_failures() -> None:
    payload = {
        "passed": False,
        "unit": {"passed": True, "passed_count": 1, "failed_count": 0, "skipped_count": 0},
        "e2e": {"passed": False, "passed_count": 0, "failed_count": 1, "skipped_count": 0},
    }
    detail = event_detail(_event(EventType.TASK_VALIDATION_RESULT.value, payload, "t1"))
    assert "e2e=FAIL (0p/1f/0s)" in detail
    assert "cosmo queue failures t1" in detail


def test_task_state_changed_shows_transition() -> None:
    payload = {"from_state": "implementing", "to_state": "failed_retry", "attempt_number": 0}
    detail = event_detail(_event(EventType.TASK_STATE_CHANGED.value, payload))
    assert detail == "implementing -> failed_retry"


def test_run_paused_shows_reason_and_resume_eta() -> None:
    payload = {"reason": "quota_exhausted_5h", "resume_delay_seconds": 3600.0}
    detail = event_detail(_event(EventType.RUN_PAUSED.value, payload))
    assert "reason=quota_exhausted_5h" in detail
    assert "resume at" in detail
    assert "UTC" in detail


def test_run_paused_with_no_delay_still_shows_reason() -> None:
    payload: dict[str, object] = {"reason": "circuit_breaker", "triggering_task": "t1"}
    detail = event_detail(_event(EventType.RUN_PAUSED.value, payload))
    assert detail == "reason=circuit_breaker"


def test_task_blocked_shows_reason_and_note_when_present() -> None:
    detail = event_detail(
        _event(EventType.TASK_BLOCKED.value, {"blocked_reason": "cost", "note": None})
    )
    assert detail == "reason=cost"

    detail_with_note = event_detail(
        _event(
            EventType.TASK_BLOCKED.value,
            {"blocked_reason": "environment", "note": "docker daemon unreachable"},
        )
    )
    assert detail_with_note == "reason=environment, note=docker daemon unreachable"


def test_task_finishing_failed_shows_spec_and_error() -> None:
    payload: dict[str, object] = {"spec_id": "habit-tracker", "error": "Change 'x' not found"}
    detail = event_detail(_event(EventType.TASK_FINISHING_FAILED.value, payload))
    assert detail == "spec=habit-tracker: Change 'x' not found"


def test_run_cost_warning_shows_spend_against_limit() -> None:
    payload: dict[str, object] = {"total_cost_usd": 41.2, "limit_usd": 50.0}
    detail = event_detail(_event(EventType.RUN_COST_WARNING.value, payload))
    assert detail == "$41.20 / $50.00 limit"


def test_run_summary_shows_full_breakdown() -> None:
    payload: dict[str, object] = {
        "completed": 5,
        "blocked": 0,
        "retried": 4,
        "total_duration_seconds": 12186.0,
        "total_cost_usd": 24.21,
    }
    detail = event_detail(_event(EventType.RUN_SUMMARY.value, payload))
    assert "completed=5" in detail
    assert "blocked=0" in detail
    assert "retried=4" in detail
    assert "duration=203.1min" in detail
    assert "cost=$24.21" in detail


def test_watch_stale_shows_threshold() -> None:
    detail = event_detail(_event(WATCH_STALE_EVENT_TYPE, {"stale_after_seconds": 1800}))
    assert detail == "no events for 1800s -- run loop may be dead"


def test_task_interrupted_shows_previous_status() -> None:
    detail = event_detail(_event(EventType.TASK_INTERRUPTED.value, {"previous_status": "merging"}))
    assert "merging" in detail
    assert "requeued" in detail
