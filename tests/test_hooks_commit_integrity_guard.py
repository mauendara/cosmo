"""`templates/harness/claude/hooks/commit_integrity_guard.py` (spec 2.5)."""

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
    / "commit_integrity_guard.py"
)


def _run(command: str) -> subprocess.CompletedProcess[str]:
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
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


def test_git_commit_no_verify_is_denied() -> None:
    assert _is_denied(_run("git commit -m 'x' --no-verify"))


def test_git_push_is_denied() -> None:
    assert _is_denied(_run("git push origin task/foo"))


def test_git_push_force_variants_are_denied_as_a_subset_of_any_push() -> None:
    assert _is_denied(_run("git push --force origin task/foo"))
    assert _is_denied(_run("git push -f origin task/foo"))
    assert _is_denied(_run("git push --force-with-lease origin task/foo"))


def test_git_reset_hard_is_denied() -> None:
    assert _is_denied(_run("git reset --hard HEAD~1"))


def test_plain_git_commit_is_allowed() -> None:
    result = _run("git commit -m 'implement widget'")
    assert result.stdout.strip() == ""


def test_plain_git_reset_without_hard_is_allowed() -> None:
    result = _run("git reset HEAD~1")
    assert result.stdout.strip() == ""


def test_denied_pattern_inside_a_compound_command_is_still_caught() -> None:
    assert _is_denied(_run("cd /repo && git commit -m x --no-verify"))
    assert _is_denied(_run("git status; git push origin main"))


def test_unrelated_command_is_allowed() -> None:
    result = _run("ls -la")
    assert result.stdout.strip() == ""


def test_non_bash_tool_is_ignored() -> None:
    payload = {"tool_name": "Edit", "tool_input": {"command": "git push"}}
    result = subprocess.run(
        ["python3", str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
        timeout=5,
        check=False,
    )
    assert result.stdout.strip() == ""


def test_completes_well_under_the_2s_budget() -> None:
    started = time.monotonic()
    _run("git push origin main")
    assert time.monotonic() - started < 2.0
