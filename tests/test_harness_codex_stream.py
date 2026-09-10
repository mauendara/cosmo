"""Recorded-contract tests for ``codex exec --json`` parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from cosmo.harness.codex.stream import (
    ClassifiedKind,
    JsonlLineBuffer,
    StreamReader,
    classify_line,
    describe_activity,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "codex_jsonl"


def test_line_buffer_handles_every_single_split_point() -> None:
    raw = (FIXTURES / "success.ndjson").read_bytes()
    expected = raw.splitlines()
    for split_at in range(len(raw) + 1):
        buffer = JsonlLineBuffer()
        assert buffer.feed(raw[:split_at]) + buffer.feed(raw[split_at:]) == expected


def test_line_buffer_handles_one_byte_chunks() -> None:
    raw = (FIXTURES / "success.ndjson").read_bytes()
    buffer = JsonlLineBuffer()
    actual = []
    for value in raw:
        actual.extend(buffer.feed(bytes([value])))
    assert actual == raw.splitlines()


@pytest.mark.parametrize("raw", [b"", b"not json", b"[]", b"\xff"])
def test_malformed_lines_never_raise(raw: bytes) -> None:
    assert classify_line(raw).kind is ClassifiedKind.MALFORMED


def test_recorded_success_captures_thread_and_terminal_usage() -> None:
    reader = StreamReader()
    reader.feed((FIXTURES / "success.ndjson").read_bytes())

    assert reader.session_id == "01900000-0000-7000-8000-000000000001"
    assert reader.terminal_event is not None
    assert reader.terminal_event.event_type == "turn.completed"
    assert reader.terminal_event.payload["usage"]["input_tokens"] == 24429
    assert reader.tool_call_count == 0


def test_recorded_failure_retains_structured_error_and_terminal() -> None:
    reader = StreamReader()
    reader.feed((FIXTURES / "api_failure.ndjson").read_bytes())

    assert reader.latest_error is not None
    assert reader.latest_error.payload["message"].startswith("The requested model")
    assert reader.terminal_event is not None
    assert reader.terminal_event.event_type == "turn.failed"


def test_duplicate_item_lifecycle_counts_as_one_tool_call() -> None:
    reader = StreamReader()
    reader.feed((FIXTURES / "file_change_failed.ndjson").read_bytes())

    assert reader.tool_call_count == 1
    tool_events = [event for event in reader.events if event.kind is ClassifiedKind.TOOL_CALL]
    assert len(tool_events) == 2
    assert describe_activity(tool_events[0], cwd=Path("/workspace")) == (
        "file_change: ./allowed.txt"
    )


def test_malformed_line_does_not_hide_later_terminal_event() -> None:
    reader = StreamReader()
    reader.feed((FIXTURES / "malformed.ndjson").read_bytes())

    assert sum(event.kind is ClassifiedKind.MALFORMED for event in reader.events) == 1
    assert reader.terminal_event is not None
    assert reader.terminal_event.event_type == "turn.completed"


def test_truncated_final_line_is_malformed_without_synthesizing_a_terminal_event() -> None:
    reader = StreamReader()
    reader.feed((FIXTURES / "truncated.ndjson").read_bytes())

    assert [event.event_type for event in reader.events] == [
        "thread.started",
        "turn.started",
        "item.started",
        None,
    ]
    assert reader.events[-1].kind is ClassifiedKind.MALFORMED
    assert reader.tool_call_count == 1
    assert reader.terminal_event is None


def test_agent_prose_that_looks_like_a_control_signal_is_only_a_heartbeat() -> None:
    reader = StreamReader()
    reader.feed((FIXTURES / "malformed.ndjson").read_bytes())

    prose = next(
        event
        for event in reader.events
        if event.event_type == "item.completed"
        and event.payload.get("item", {}).get("type") == "agent_message"
    )
    assert prose.kind is ClassifiedKind.HEARTBEAT


def test_command_activity_uses_only_structured_command_field() -> None:
    event = classify_line(
        b'{"type":"item.started","item":{"id":"i1","type":"command_execution",'
        b'"command":"git status","status":"in_progress"}}'
    )

    assert describe_activity(event) == "command_execution: git status"
