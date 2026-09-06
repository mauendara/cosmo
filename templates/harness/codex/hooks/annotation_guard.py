#!/usr/bin/env python3
"""Deny newly inserted test-disable annotations."""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hooklib import (  # noqa: E402
    added_patch_lines,
    allow,
    deny,
    patch_command,
    read_hook_input,
    shell_command,
    shell_is_mutating,
)

FORBIDDEN = (
    ("@Disabled", re.compile(r"@Disabled\b")),
    ("@Ignore", re.compile(r"@Ignore\b")),
    ("test.skip", re.compile(r"\btest\.skip\b")),
    ("it.skip", re.compile(r"\bit\.skip\b")),
    ("describe.skip", re.compile(r"\bdescribe\.skip\b")),
    ("xit(", re.compile(r"\bxit\s*\(")),
)


def main() -> None:
    payload = read_hook_input()
    tool_name = payload.get("tool_name")
    if tool_name == "apply_patch":
        candidate = "\n".join(added_patch_lines(patch_command(payload)))
    elif tool_name == "Bash":
        command = shell_command(payload)
        if not shell_is_mutating(command):
            allow()
        candidate = command
    else:
        allow()
    for label, pattern in FORBIDDEN:
        if pattern.search(candidate):
            deny(f"annotation guard: this mutation introduces {label!r}")
    allow()


if __name__ == "__main__":
    main()
