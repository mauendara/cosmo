"""Incremental parser for ``codex exec --json`` JSONL output.

The event shapes are from real Codex CLI 0.153.0 captures recorded under
``tests/fixtures/codex_jsonl``.  Classification uses only tool-defined fields;
agent-authored message text is retained as raw evidence but is never a signal.
"""

from __future__ import annotations

import enum
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ClassifiedKind(enum.Enum):
    HEARTBEAT = "heartbeat"
    TOOL_CALL = "tool_call"
    TERMINAL = "terminal"
    ERROR = "error"
    MALFORMED = "malformed"


@dataclass(frozen=True, slots=True)
class ClassifiedEvent:
    kind: ClassifiedKind
    event_type: str | None
    item_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    raw_line: bytes = b""


class JsonlLineBuffer:
    """Yield complete lines from arbitrary byte chunks.

    A final partial line is intentionally left buffered. A killed or crashed
    process must not turn an otherwise useful stream into a parser exception.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> list[bytes]:
        self._buffer.extend(chunk)
        lines: list[bytes] = []
        while True:
            newline = self._buffer.find(b"\n")
            if newline == -1:
                return lines
            lines.append(bytes(self._buffer[:newline]))
            del self._buffer[: newline + 1]


_TOOL_ITEM_TYPES = frozenset(
    {
        "command_execution",
        "file_change",
        "mcp_tool_call",
        "web_search",
    }
)


def classify_line(raw_line: bytes) -> ClassifiedEvent:
    stripped = raw_line.strip()
    if not stripped:
        return ClassifiedEvent(ClassifiedKind.MALFORMED, None, raw_line=raw_line)
    try:
        decoded = json.loads(stripped)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ClassifiedEvent(ClassifiedKind.MALFORMED, None, raw_line=raw_line)
    if not isinstance(decoded, dict):
        return ClassifiedEvent(ClassifiedKind.MALFORMED, None, raw_line=raw_line)

    payload: dict[str, Any] = decoded
    raw_type = payload.get("type")
    event_type = raw_type if isinstance(raw_type, str) else None
    item = payload.get("item")
    item_payload = item if isinstance(item, dict) else None
    raw_item_id = item_payload.get("id") if item_payload is not None else None
    item_id = raw_item_id if isinstance(raw_item_id, str) else None

    if event_type in {"turn.completed", "turn.failed"}:
        kind = ClassifiedKind.TERMINAL
    elif event_type == "error" or (
        item_payload is not None and item_payload.get("type") == "error"
    ):
        kind = ClassifiedKind.ERROR
    elif (
        event_type in {"item.started", "item.updated", "item.completed"}
        and item_payload is not None
        and item_payload.get("type") in _TOOL_ITEM_TYPES
    ):
        kind = ClassifiedKind.TOOL_CALL
    else:
        kind = ClassifiedKind.HEARTBEAT
    return ClassifiedEvent(kind, event_type, item_id, payload, raw_line)


_MAX_ACTIVITY_LINE = 100


def describe_activity(event: ClassifiedEvent, *, cwd: Path | None = None) -> str | None:
    """Describe structured tool fields for display, never for control flow."""
    if event.kind is not ClassifiedKind.TOOL_CALL:
        return None
    item = event.payload.get("item")
    if not isinstance(item, dict):
        return None
    item_type = item.get("type")
    detail: str | None = None
    if item_type == "command_execution":
        command = item.get("command")
        detail = command if isinstance(command, str) else None
    elif item_type == "file_change":
        changes = item.get("changes")
        if isinstance(changes, list):
            paths = [change.get("path") for change in changes if isinstance(change, dict)]
            detail = ", ".join(path for path in paths if isinstance(path, str)) or None
    if detail is None:
        return item_type if isinstance(item_type, str) else None
    if cwd is not None:
        detail = detail.replace(str(cwd), ".")
    line = f"{item_type}: {detail}"
    return line if len(line) <= _MAX_ACTIVITY_LINE else line[: _MAX_ACTIVITY_LINE - 1] + "…"


class StreamReader:
    """Collect classified events and invocation-level structured state."""

    def __init__(self, *, on_event: Callable[[ClassifiedEvent], None] | None = None) -> None:
        self._lines = JsonlLineBuffer()
        self._on_event = on_event
        self._seen_tool_item_ids: set[str] = set()
        self.events: list[ClassifiedEvent] = []
        self.terminal_event: ClassifiedEvent | None = None
        self.latest_error: ClassifiedEvent | None = None
        self.session_id: str | None = None
        self.tool_call_count = 0

    def feed(self, chunk: bytes) -> None:
        for line in self._lines.feed(chunk):
            self.feed_line(line)

    def feed_line(self, line: bytes) -> ClassifiedEvent:
        event = classify_line(line)
        self.events.append(event)
        if event.event_type == "thread.started":
            thread_id = event.payload.get("thread_id")
            if isinstance(thread_id, str):
                self.session_id = thread_id
        if event.kind is ClassifiedKind.TERMINAL:
            self.terminal_event = event
        elif event.kind is ClassifiedKind.ERROR:
            self.latest_error = event
        elif event.kind is ClassifiedKind.TOOL_CALL:
            if event.item_id is None:
                self.tool_call_count += 1
            elif event.item_id not in self._seen_tool_item_ids:
                self._seen_tool_item_ids.add(event.item_id)
                self.tool_call_count += 1
        if self._on_event is not None:
            self._on_event(event)
        return event
