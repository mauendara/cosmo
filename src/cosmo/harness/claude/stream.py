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
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
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


_MAX_ACTIVITY_LINE = 100

# Structured-field extraction only, same discipline as `_has_tool_call`
# above (spec 4's "prose parsing is prohibited as a signal") -- this is
# display, not classification, but it stays keyed off the same `name`/
# `input` fields Claude Code itself defines, never off free text.
_TOOL_INPUT_KEYS: dict[str, str] = {
    "Bash": "command",
    "Edit": "file_path",
    "Write": "file_path",
    "Read": "file_path",
    "Glob": "pattern",
    "Grep": "pattern",
}


def describe_tool_call(payload: dict[str, Any], *, cwd: Path | None = None) -> str | None:
    """A short, human-readable line for a `ClassifiedKind.TOOL_CALL` event's
    payload -- live terminal feedback during `cosmo run` (item 3), never a
    decision signal. Returns `None` if the payload has no `tool_use` block
    to describe (e.g. a `tool_result`-only turn).

    `cwd`, when given, is the task's worktree root. `detail` (a `file_path`,
    a `Bash` command, ...) is very often that same absolute path or a
    command embedding it (`/home/.../work/<run_id>/<task_id>/frontend/...`)
    -- found by hand watching a real `cosmo run`: at 100+ characters, that
    prefix alone ate the entire `_MAX_ACTIVITY_LINE` cap below, truncating
    every single line before the actual filename ever appeared. Collapsing
    the worktree prefix to `.` (accurate -- it *is* the harness's own cwd)
    must happen before the cap is applied, not after, or the useful part of
    the line is already gone by the time there's anything left to shorten."""
    message = payload.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if not isinstance(content, list):
        return None

    for block in content:
        if not (isinstance(block, dict) and block.get("type") == "tool_use"):
            continue
        name = block.get("name")
        if not isinstance(name, str):
            continue
        tool_input = block.get("input")
        key = _TOOL_INPUT_KEYS.get(name)
        detail = tool_input.get(key) if key and isinstance(tool_input, dict) else None
        if not isinstance(detail, str) or not detail:
            return name
        if cwd is not None:
            detail = detail.replace(str(cwd), ".")
        line = f"{name}: {detail}"
        return line if len(line) <= _MAX_ACTIVITY_LINE else line[: _MAX_ACTIVITY_LINE - 1] + "…"

    return None


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
    `_finalize`), so no lock is needed here either. `on_event`, if given, is
    therefore also called from that same thread, live as each line arrives
    -- item 3's live activity feed, purely a display hook, never consulted
    for any classification/retry decision.
    """

    def __init__(self, *, on_event: Callable[[ClassifiedEvent], None] | None = None) -> None:
        self._lines = NdjsonLineBuffer()
        self._on_event = on_event
        self.events: list[ClassifiedEvent] = []
        self.terminal_result: ClassifiedEvent | None = None
        self.latest_rate_limit: ClassifiedEvent | None = None
        self.session_id: str | None = None
        self.tool_call_count = 0
        """Spec 7.2's wall-clock quota heuristic keys on "no tool calls
        executed"; counted here rather than re-scanning `events` later
        since this is the one place that already sees every line once."""

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
        elif event.kind is ClassifiedKind.TOOL_CALL:
            self.tool_call_count += 1
        if self._on_event is not None:
            self._on_event(event)
        return event


def extract_quota_signal(reader: StreamReader) -> tuple[str | None, str | None]:
    """Spec 7.1/7.2's primary quota signal, normalized from whichever of the
    two observed wire shapes `reader.latest_rate_limit` last held (see this
    module's docstring on `rate_limit_event` vs. `system/api_retry`).
    Returns `(window, resets_at_iso)`; `(None, None)` if no rate-limit-shaped
    event was seen on this call at all.

    Deliberately returns whatever was last observed regardless of
    `HarnessResult.success` -- the fixture behind
    `test_api_retry_is_the_primary_quota_signal_in_both_observed_shapes`
    shows a real capture where the CLI's own internal retry absorbed a rate
    limit and the call still succeeded. Whether an observed signal is
    *actionable* (the call ultimately failed) is a policy question for
    `cosmo.run.quota`, not a parsing question for this module.
    """
    event = reader.latest_rate_limit
    if event is None:
        return None, None

    info = event.payload.get("rate_limit_info")
    if isinstance(info, dict):
        # A `rate_limit_event` fires routinely, once per session, purely as
        # informational telemetry about the current window -- `status` is
        # `"allowed"` on every ordinary call, carrying a real `resetsAt` the
        # window will naturally reach regardless of whether anything was
        # ever actually rate-limited. Only a `status` other than `"allowed"`
        # is real evidence this call was impacted. Found by hand: a real
        # `error_max_turns` failure (nothing to do with quota) got reported
        # as a *confirmed* quota exhaustion because this branch trusted
        # `rateLimitType`/`resetsAt` unconditionally -- the lone
        # `rate_limit_event` on that call had `status: "allowed"` the whole
        # time. See docs/v3-implementation-state.md for the full capture.
        if info.get("status") == "allowed":
            return None, None
        window = _normalize_window(info.get("rateLimitType"))
        resets_epoch = info.get("resetsAt")
        resets_at = (
            _epoch_seconds_to_iso(resets_epoch) if isinstance(resets_epoch, int | float) else None
        )
        return window, resets_at

    # The `system/api_retry` shape (`{"type": "system", "subtype":
    # "api_retry", "retry_after_ms": ...}`) carries no window or reset-ETA
    # field at all -- `retry_after_ms` is a short internal backoff (30s in
    # the captured fixture), not a real quota reset time, so it is never
    # used as `resets_at`. Still a real signal that *some* rate limit was
    # touched; default to the shorter, safer window with an unknown reset
    # time -- the caller falls back to its own configured default delay.
    return "five_hour", None


def _normalize_window(raw: object) -> str:
    # Spec 7.1 names exactly two windows. `rateLimitType` values beyond
    # "five_hour" are treated as the weekly cap -- the only other window
    # the spec defines -- rather than matched against a specific string,
    # since no real capture of the weekly-cap shape exists yet (see
    # `QuotaConfig`'s docstring).
    return "five_hour" if raw == "five_hour" else "weekly"


def _epoch_seconds_to_iso(value: float) -> str:
    return datetime.fromtimestamp(value, tz=UTC).isoformat(timespec="milliseconds")
