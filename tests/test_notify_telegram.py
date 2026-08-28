"""`notify.telegram.format_event` -- the message-shaping half of
`TelegramSink` that doesn't require a real network call. The Bot API call
itself (`TelegramSink.send`) is exactly the kind of real invocation this
codebase's own convention defers to a real, opt-in verification pass (a
real bot token/chat id) rather than mocking `urllib` -- see the v5
improvements plan's own "Verification" section."""

from __future__ import annotations

from cosmo.events.envelope import EVENT_SCHEMA_VERSION, Event
from cosmo.notify.telegram import format_event
from cosmo.store.enums import Severity


def _event(**overrides: object) -> Event:
    base: dict[str, object] = {
        "event_id": "e1",
        "run_id": "run-1",
        "task_id": None,
        "timestamp": "2026-01-01T00:00:00Z",
        "sequence": 1,
        "event_type": "run.paused",
        "severity": Severity.WARNING,
        "schema_version": EVENT_SCHEMA_VERSION,
        "payload": {},
    }
    base.update(overrides)
    return Event(**base)  # type: ignore[arg-type]


def test_format_includes_event_type_and_severity() -> None:
    text = format_event(_event())
    assert "run.paused" in text
    assert "warning" in text


def test_format_includes_run_and_task_ids_when_present() -> None:
    text = format_event(_event(task_id="task-1"))
    assert "run-1" in text
    assert "task-1" in text


def test_format_omits_task_line_when_absent() -> None:
    text = format_event(_event(task_id=None))
    assert "task:" not in text


def test_format_includes_the_payload() -> None:
    text = format_event(_event(payload={"resets_at": "2026-01-01T05:00:00Z"}))
    assert "resets_at" in text


def test_format_uses_the_shared_human_readable_detail_when_recognized() -> None:
    """A `task.validation_result` payload gets the same human phrase
    `cli.main._print_emit` shows in the live terminal, not a raw JSON dump
    -- both go through `events.format.event_detail`."""
    text = format_event(
        _event(
            event_type="task.validation_result",
            task_id="t1",
            payload={
                "passed": False,
                "unit": {"passed": True, "passed_count": 1, "failed_count": 0, "skipped_count": 0},
                "e2e": {
                    "passed": False,
                    "passed_count": 0,
                    "failed_count": 1,
                    "skipped_count": 0,
                },
            },
        )
    )
    assert "passed=False" in text
    assert "e2e=FAIL (0p/1f/0s)" in text
    assert "cosmo queue failures t1" in text
    assert '"passed"' not in text  # no raw JSON when a human phrase exists


def test_format_falls_back_to_raw_payload_for_an_unrecognized_event_type() -> None:
    text = format_event(_event(event_type="some.future.event_type", payload={"weight": 7}))
    assert '"weight": 7' in text
