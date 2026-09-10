#!/usr/bin/env python3
"""Deny shell constructs that can outlive a one-shot Codex invocation."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hooklib import (  # noqa: E402
    allow,
    deny,
    read_hook_input,
    shell_command,
    shell_has_background_operator,
    shell_tokens,
)


def main() -> None:
    payload = read_hook_input()
    if payload.get("tool_name") != "Bash":
        allow()
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict) and tool_input.get("run_in_background"):
        deny("background-task guard: run_in_background is not permitted")
    command = shell_command(payload)
    tokens = shell_tokens(command)
    if command and not tokens:
        deny("background-task guard: malformed shell input cannot be checked")
    if shell_has_background_operator(command) or any(
        Path(token).name in {"nohup", "disown"} for token in tokens
    ):
        deny("background-task guard: detached/background shell execution is not permitted")
    allow()


if __name__ == "__main__":
    main()
