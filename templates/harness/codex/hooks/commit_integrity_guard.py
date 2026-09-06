#!/usr/bin/env python3
"""Deny Git operations that bypass integrity checks or belong to Cosmo."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hooklib import (  # noqa: E402
    allow,
    deny,
    patch_command,
    patch_paths,
    paths_mentioned,
    project_dir,
    read_hook_input,
    shell_command,
    shell_is_mutating,
)

RULES = (
    ("git commit --no-verify", re.compile(r"\bgit\s+commit\b[^;&|]*--no-verify\b")),
    ("git push", re.compile(r"\bgit(?:\s+-\S+(?:\s+\S+)?)?\s+push\b")),
    ("git reset --hard", re.compile(r"\bgit\s+reset\b[^;&|]*--hard\b")),
    ("git clean", re.compile(r"\bgit\s+clean\b[^;&|]*-[^;&|]*[fdxX]")),
    ("git checkout --", re.compile(r"\bgit\s+checkout\b[^;&|]*\s--(?:\s|$)")),
)


def main() -> None:
    payload = read_hook_input()
    tool_name = payload.get("tool_name")
    root = project_dir(payload)
    if tool_name == "apply_patch":
        if any(_is_git_metadata(path) for path in patch_paths(patch_command(payload), root)):
            deny("commit-integrity guard: direct patches to Git metadata are not permitted")
        allow()
    if tool_name != "Bash":
        allow()
    command = shell_command(payload)
    if shell_is_mutating(command) and any(
        _is_git_metadata(path) for path in paths_mentioned(command, root)
    ):
        deny("commit-integrity guard: direct shell mutations of Git metadata are not permitted")
    for label, pattern in RULES:
        if pattern.search(command):
            deny(f"commit-integrity guard: {label!r} is not permitted")
    allow()


def _is_git_metadata(path: str) -> bool:
    return ".git" in Path(path).parts


if __name__ == "__main__":
    main()
