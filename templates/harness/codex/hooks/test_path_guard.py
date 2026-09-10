#!/usr/bin/env python3
"""Deny protected test mutations through apply_patch or shell commands."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hooklib import (  # noqa: E402
    PROTECTED_PATTERNS,
    allow,
    deny,
    matches,
    patch_command,
    patch_paths,
    paths_mentioned,
    project_dir,
    read_hook_input,
    shell_command,
    shell_is_mutating,
    task_allows_test_edits,
)


def main() -> None:
    payload = read_hook_input()
    tool_name = payload.get("tool_name")
    root = project_dir(payload)
    if tool_name == "apply_patch":
        patch = patch_command(payload)
        paths = patch_paths(patch, root)
        if patch and not paths:
            deny("test-path guard: could not identify paths in apply_patch input")
    elif tool_name == "Bash":
        command = shell_command(payload)
        if not shell_is_mutating(command):
            allow()
        paths = paths_mentioned(command, root)
    else:
        allow()

    for path in paths:
        pattern = matches(path, PROTECTED_PATTERNS)
        if pattern and not task_allows_test_edits():
            deny(
                f"test-path guard: {path!r} matches protected pattern {pattern!r}; "
                "the queued task does not allow test edits"
            )
    allow()


if __name__ == "__main__":
    main()
