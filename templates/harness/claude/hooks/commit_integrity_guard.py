#!/usr/bin/env python3
"""PreToolUse guard: blocks git commands that bypass integrity controls or
that are Cosmo's job, not the agent's (spec 2.5).

Blocked:
  - `git commit ... --no-verify`  -- bypasses the local pre-commit hook,
    including secret scanning (spec 6.1).
  - `git push ...` (any form)     -- pushing is Cosmo's job; this also covers
    every force-push variant (--force, -f, --force-with-lease) as a subset,
    since the whole subcommand is blocked, not just the force flags.
  - `git reset --hard ...`        -- can silently discard work.

Matched on `Bash` via settings.json. Regex search over the whole command
string, not shell-aware parsing -- catches these inside compound commands
(`&&`, `;`, `|`) for free, and adversarial evasion (e.g. quoting tricks) is
out of scope for a hook budgeted under 2s; this is prevention-layer defense
in depth (spec 6.1), not the only layer.
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hooklib import allow, deny, read_hook_input  # noqa: E402

RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("git commit --no-verify", re.compile(r"\bgit\s+commit\b[^&;|]*--no-verify\b")),
    ("git push", re.compile(r"\bgit\s+push\b")),
    ("git reset --hard", re.compile(r"\bgit\s+reset\b[^&;|]*--hard\b")),
)


def main() -> None:
    payload = read_hook_input()
    if payload.get("tool_name") != "Bash":
        allow()

    command = str((payload.get("tool_input") or {}).get("command") or "")
    if not command:
        allow()

    for label, pattern in RULES:
        if pattern.search(command):
            deny(
                f"commit-integrity guard: {label!r} is never permitted from "
                f"the harness (spec 2.5). Command: {command!r}"
            )
            return

    allow()


if __name__ == "__main__":
    main()
