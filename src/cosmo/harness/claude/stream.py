"""NDJSON `stream-json` reader and classifier (spec 4, plan Phase 3).

Lives beside the adapter, not in core -- this wire format is Claude Code's own
(§2.1's rationale for the harness abstraction in the first place), so parsing
it here rather than in `cosmo.harness.base` is what keeps a future non-Claude
adapter from ever having to know it exists.

**Prose parsing is prohibited as a signal (spec 4).** Every classification
below keys off structured fields Claude Code itself defines (`type`,
`subtype`, content-block `type`) -- never off the human-readable `text` a
message actually contains. `test_prose_content_is_never_inspected_for_signal`
in `tests/test_harness_claude_stream.py` pins this by feeding a message whose
*text* looks like a rate-limit notice and asserting it classifies as an
ordinary heartbeat.

Field shapes below were captured from a real `claude -p --output-format
stream-json --verbose` run (CLI 2.1.207) rather than guessed, per the
project's "check with a real invocation" convention -- see the Phase 3 state
doc for the raw capture and the one place it disagrees with the spec text
(`rate_limit_event` vs. the spec's `system/api_retry`).
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass, field
from typing import Any


class ClassifiedKind(enum.Enum):
    HEARTBEAT = "heartbeat"
    TOOL_CALL = "tool_call"
    RATE_LIMIT = "rate_limit"
    RESULT = "result"
    MALFORMED = "malformed"


@dataclass(frozen=True, slots=True)
class ClassifiedEvent:
    kind: ClassifiedKind
    session_id: str | None
    payload: dict[str, Any] = field(default_factory=dict)
    raw_line: bytes = b""


class NdjsonLineBuffer:
    """Splits arbitrary byte chunks into complete, newline-terminated lines.

    Fed directly from `ManagedProcess`'s stdout drain thread (one chunk per
    `os.read`, not one line), so a line routinely arrives split across two or
    more `feed()` calls. Any trailing partial line -- including a stream that
    ends mid-line, e.g. the process was killed or crashed -- is buffered and
    simply never yielded, rather than raising. That tolerance is what the
    "truncated stream" fixture exercises.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> list[bytes]:
        self._buffer.extend(chunk)
        lines: list[bytes] = []
        while True:
            idx = self._buffer.find(b"\n")
            if idx == -1:
                break
            lines.append(bytes(self._buffer[:idx]))
            del self._buffer[: idx + 1]
        return lines


def classify_line(raw_line: bytes) -> ClassifiedEvent:
    """Parse and classify one NDJSON line. Never raises: a line that isn't
    valid JSON, or isn't a JSON object, classifies as `MALFORMED` rather than
    propagating -- "non-JSON noise" (plan Phase 3) is expected, not exceptional.
    """
    stripped = raw_line.strip()
    if not stripped:
        return ClassifiedEvent(kind=ClassifiedKind.MALFORMED, session_id=None, raw_line=raw_line)

    try:
        obj = json.loads(stripped)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ClassifiedEvent(kind=ClassifiedKind.MALFORMED, session_id=None, raw_line=raw_line)

    if not isinstance(obj, dict):
        return ClassifiedEvent(kind=ClassifiedKind.MALFORMED, session_id=None, raw_line=raw_line)

    session_id = obj.get("session_id")
    session_id = session_id if isinstance(session_id, str) else None
    event_type = obj.get("type")

    if event_type == "result":
        return ClassifiedEvent(
            kind=ClassifiedKind.RESULT, session_id=session_id, payload=obj, raw_line=raw_line
        )

    # Spec 7.2 names the primary quota signal `system/api_retry`. The
    # installed CLI (2.1.207) instead emits a dedicated top-level
    # `rate_limit_event` -- observed on a real probe run, not documented
    # anywhere Cosmo could have read first. Both are handled: whichever a
    # given CLI version actually emits, the ETA-bearing signal is caught.
    if event_type == "rate_limit_event" or (
        event_type == "system" and obj.get("subtype") == "api_retry"
    ):
        return ClassifiedEvent(
            kind=ClassifiedKind.RATE_LIMIT, session_id=session_id, payload=obj, raw_line=raw_line
        )

    if event_type in ("assistant", "user") and _has_tool_call(obj):
        return ClassifiedEvent(
            kind=ClassifiedKind.TOOL_CALL, session_id=session_id, payload=obj, raw_line=raw_line
        )

    # Every other well-formed line -- system/init, hook events, plain
    # assistant/user turns with no tool call, etc. -- is still a heartbeat:
    # spec 4 wants liveness independent of whether the agent writes a file.
    return ClassifiedEvent(
        kind=ClassifiedKind.HEARTBEAT, session_id=session_id, payload=obj, raw_line=raw_line
    )


def _has_tool_call(obj: dict[str, Any]) -> bool:
    message = obj.get("message")
    if not isinstance(message, dict):
        return False
    content = message.get("content")
    if not isinstance(content, list):
        return False
    return any(
        isinstance(block, dict) and block.get("type") in ("tool_use", "tool_result")
        for block in content
    )


class StreamReader:
    """Stateful glue between `NdjsonLineBuffer`/`classify_line` and
    `ManagedProcess`'s `on_stdout_chunk` tee. One instance per invocation.

    `feed()` runs on `ManagedProcess`'s stdout-drain thread (see the comment
    there); the adapter reads `.events` / `.terminal_result` / `.session_id`
    only after joining that thread (via `ManagedProcess.cancel()`'s
    `_finalize`), so no lock is needed here either.
    """

    def __init__(self) -> None:
        self._lines = NdjsonLineBuffer()
        self.events: list[ClassifiedEvent] = []
        self.terminal_result: ClassifiedEvent | None = None
        self.latest_rate_limit: ClassifiedEvent | None = None
        self.session_id: str | None = None

    def feed(self, chunk: bytes) -> None:
        for line in self._lines.feed(chunk):
            self.feed_line(line)

    def feed_line(self, line: bytes) -> ClassifiedEvent:
        """Classify one already-split line. Exposed directly so tests can
        replay a recorded fixture file line-by-line without re-deriving the
        chunking `feed()` does for a live pipe."""
        event = classify_line(line)
        self.events.append(event)
        if event.session_id is not None:
            self.session_id = event.session_id
        if event.kind is ClassifiedKind.RESULT:
            self.terminal_result = event
        elif event.kind is ClassifiedKind.RATE_LIMIT:
            self.latest_rate_limit = event
        return event
