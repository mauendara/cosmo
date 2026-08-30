"""`templates/harness/claude/hooks/background_task_guard.py` (spec 2.5)."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

HOOK = (
    Path(__file__).resolve().parents[1]
    / "templates"
    / "harness"
    / "claude"
    / "hooks"
    / "background_task_guard.py"
)


def _run(
    tool_input: dict[str, object], tool_name: str = "Bash"
) -> subprocess.CompletedProcess[str]:
    payload = {"tool_name": tool_name, "tool_input": tool_input}
    return subprocess.run(
        ["python3", str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
        timeout=5,
        check=False,
    )


def _is_denied(result: subprocess.CompletedProcess[str]) -> bool:
    if not result.stdout.strip():
        return False
    decision = json.loads(result.stdout)
    return bool(decision["hookSpecificOutput"]["permissionDecision"] == "deny")


def test_run_in_background_true_is_denied() -> None:
    result = _run({"command": "npm install", "run_in_background": True})
    assert _is_denied(result)


def test_run_in_background_false_is_allowed() -> None:
    result = _run({"command": "npm install", "run_in_background": False})
    assert result.stdout.strip() == ""


def test_run_in_background_absent_is_allowed() -> None:
    result = _run({"command": "npm install"})
    assert result.stdout.strip() == ""


def test_non_bash_tool_is_ignored_even_with_the_flag() -> None:
    result = _run({"run_in_background": True}, tool_name="Edit")
    assert result.stdout.strip() == ""


def test_completes_well_under_the_2s_budget() -> None:
    started = time.monotonic()
    _run({"command": "npm install", "run_in_background": True})
    assert time.monotonic() - started < 2.0
