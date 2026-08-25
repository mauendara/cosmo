#!/usr/bin/env python3
"""PreToolUse guard: blocks introducing a test-skip/disable annotation
(spec 2.5, 6.1). Weakening a test this way is functionally identical to
deleting it, and deleting it is already caught by the test-path guard --
this hook is what catches the same failure mode inside a file the test-path
guard doesn't own (e.g. a `@Disabled` added to a source file's inline test,
or a skip introduced in a file not under a protected path).

"Introducing" is judged by comparing counts before/after the proposed edit,
not by a flat substring search -- a file that already legitimately contains
one of these tokens (e.g. quoted in a comment, or a pre-existing skip nobody
touched) must not block an unrelated edit to the same file.
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hooklib import allow, deny, read_hook_input  # noqa: E402

GUARDED_TOOLS = frozenset({"Edit", "Write"})

# Spec 2.5's literal list. Word-boundary/paren-anchored so common substrings
# (e.g. "exit" containing "xit") don't false-positive.
FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("@Disabled", re.compile(r"@Disabled\b")),
    ("@Ignore", re.compile(r"@Ignore\b")),
    ("test.skip", re.compile(r"\btest\.skip\b")),
    ("it.skip", re.compile(r"\bit\.skip\b")),
    ("describe.skip", re.compile(r"\bdescribe\.skip\b")),
    ("xit(", re.compile(r"\bxit\s*\(")),
)


def _read_existing(file_path: str) -> str:
    try:
        with open(file_path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        # Unreadable/nonexistent baseline -- treat as empty so any occurrence
        # in the proposed content reads as newly introduced. Conservative by
        # design (fail toward flagging), matching this codebase's fail-closed
        # posture elsewhere in the guardrail layer.
        return ""


def main() -> None:
    payload = read_hook_input()
    tool_name = payload.get("tool_name")
    if tool_name not in GUARDED_TOOLS:
        allow()

    tool_input = payload.get("tool_input") or {}
    file_path = tool_input.get("file_path")
    if not file_path:
        allow()

    if tool_name == "Edit":
        before = str(tool_input.get("old_string", ""))
        after = str(tool_input.get("new_string", ""))
    else:  # Write
        before = _read_existing(str(file_path))
        after = str(tool_input.get("content", ""))

    for label, pattern in FORBIDDEN_PATTERNS:
        if len(pattern.findall(after)) > len(pattern.findall(before)):
            deny(
                f"annotation guard: this edit introduces {label!r} in "
                f"{file_path!r} (spec 2.5) -- weakening a test is treated the "
                f"same as deleting it."
            )
            return

    allow()


if __name__ == "__main__":
    main()
