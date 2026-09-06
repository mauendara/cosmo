#!/usr/bin/env python3
"""Deny reads of repository-local paths commonly containing secrets."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hooklib import (  # noqa: E402
    SECRET_PATTERNS,
    allow,
    deny,
    matches,
    paths_mentioned,
    project_dir,
    read_hook_input,
    relative_path,
    shell_command,
)


def main() -> None:
    payload = read_hook_input()
    root = project_dir(payload)
    tool_name = payload.get("tool_name")
    if tool_name == "Read":
        tool_input = payload.get("tool_input")
        if not isinstance(tool_input, dict):
            allow()
        raw = tool_input.get("file_path") or tool_input.get("path")
        paths = [relative_path(str(raw), root)] if raw else []
    elif tool_name == "Bash":
        paths = paths_mentioned(shell_command(payload), root)
    else:
        allow()
    for path in paths:
        pattern = matches(path, SECRET_PATTERNS)
        if pattern:
            deny(f"secret-read guard: access to {path!r} is not permitted")
    allow()


if __name__ == "__main__":
    main()
