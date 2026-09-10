#!/usr/bin/env python3
"""In review mode, permit writes only to the canonical verdict file."""

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
    project_dir,
    read_hook_input,
    relative_path,
    shell_command,
    shell_is_mutating,
    shell_tokens,
)

VERDICT = ".cosmo/review-result.json"


def _shell_writes_only_verdict(command: str, root: Path) -> bool:
    tokens = shell_tokens(command)
    if not tokens:
        return False
    # Destructive and in-place rewrite commands are never needed to produce a
    # verdict, even if the command also redirects output to the allowed file.
    forbidden = {"cp", "dd", "install", "mv", "rm", "rmdir", "sed", "perl", "unlink"}
    if any(Path(token).name in forbidden for token in tokens):
        return False
    if Path(tokens[0]).name == "mkdir":
        operands = [token for token in tokens[1:] if not token.startswith("-")]
        return bool(operands) and all(relative_path(token, root) == ".cosmo" for token in operands)

    targets: list[str] = []
    for index, token in enumerate(tokens):
        if token in {">", ">>"} and index + 1 < len(tokens):
            targets.append(tokens[index + 1])
        elif token.startswith(">") and len(token) > 1:
            targets.append(token.lstrip(">"))
        elif Path(token).name in {"tee", "touch", "truncate"}:
            targets.extend(value for value in tokens[index + 1 :] if not value.startswith("-"))
    # shlex does not split redirection unless '<>' are punctuation, so also
    # capture the common `> path` textual form.
    targets.extend(re.findall(r">{1,2}\s*([^\s;&|]+)", command))
    return bool(targets) and all(relative_path(path, root) == VERDICT for path in targets)


def main() -> None:
    if os.environ.get("COSMO_HARNESS_ROLE") != "review":
        allow()
    payload = read_hook_input()
    root = project_dir(payload)
    tool_name = payload.get("tool_name")
    if tool_name == "apply_patch":
        paths = patch_paths(patch_command(payload), root)
        if paths and all(path == VERDICT for path in paths):
            allow()
        deny("review-write guard: apply_patch may only change the canonical verdict file")
    if tool_name == "Bash":
        command = shell_command(payload)
        if not shell_is_mutating(command) or _shell_writes_only_verdict(command, root):
            allow()
        deny("review-write guard: review shell writes may only create the canonical verdict")
    allow()


if __name__ == "__main__":
    main()
