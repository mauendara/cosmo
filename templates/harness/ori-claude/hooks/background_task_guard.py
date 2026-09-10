#!/usr/bin/env python3
"""PreToolUse guard: blocks `Bash` calls that background themselves via
`run_in_background: true` (spec 2.5 / CLAUDE.md's "this call is one-shot").

Found by hand, a third time, in the Phase 10 acceptance run's own database
(`scaffold-app`, run `0744f98ce0864270ab7648c08633fc6a`): `permissions.deny`
already blocks `ScheduleWakeup`/`ToolSearch`/`TaskOutput` -- the tools used
in the first two recorded incidents -- but neither rule touches the `Bash`
tool's own `run_in_background` parameter. A session backgrounded `npm
install` directly through it, then polled the PID with ordinary (already
allowed) shell commands (`kill -0`, `tail --pid`, `sleep`+`ps`) for the rest
of its `IMPLEMENTING` budget, made zero `tasks.md` progress, and was killed
by Cosmo's own stall timer (`implementing_stall`, spec 3.3) after 20 minutes
of doing nothing but waiting. Denying the tool names alone never stops this
class of failure -- the parameter that actually creates the detached
background job has to be denied too.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hooklib import allow, deny, read_hook_input  # noqa: E402


def main() -> None:
    payload = read_hook_input()
    if payload.get("tool_name") != "Bash":
        allow()

    tool_input = payload.get("tool_input") or {}
    if not tool_input.get("run_in_background"):
        allow()
        return

    command = str(tool_input.get("command") or "")
    deny(
        "background-task guard: Bash calls with run_in_background=true are never "
        "permitted from the harness (CLAUDE.md: this call is one-shot -- there is "
        "no later turn to check a background job's result). Run the command in the "
        f"foreground and let it block for as long as it actually takes. Command: {command!r}"
    )


if __name__ == "__main__":
    main()
