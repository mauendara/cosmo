"""Shared helpers for Cosmo's PreToolUse guardrail hooks (spec 2.5).

Copied wholesale alongside the hooks that import it (`sync_harness_assets`
copies the whole `hooks/` directory as one unit), so this stays a plain
same-directory import rather than a package dependency -- these scripts run
standalone, inside an arbitrary target repo, invoked directly by `python3`
with no `cosmo` package on the path.

Stdlib only. No network, no subprocess, no LLM calls -- the hook budget is
under 2s (5000ms hard ceiling), and a hook that times out does not block.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from typing import Any


def read_hook_input() -> dict[str, Any]:
    """The PreToolUse payload Claude Code writes to stdin. Malformed or empty
    input is treated as "nothing to say" rather than crashing the hook --
    a hook that raises produces a non-blocking error (spec 2.5: only a
    deliberate deny or a clean exit are meaningful outcomes here)."""
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def project_dir(payload: dict[str, Any]) -> str:
    """Best-known repo root: Claude Code exports CLAUDE_PROJECT_DIR into the
    hook's own environment (verified empirically, not just as a command-string
    substitution); `cwd` on the payload is the fallback."""
    return os.environ.get("CLAUDE_PROJECT_DIR") or str(payload.get("cwd") or ".")


def relative_path(file_path: str, root: str) -> str:
    """POSIX-style path of `file_path` relative to `root`, for matching
    repo-root-anchored glob patterns (e.g. `src/test/**`)."""
    try:
        rel = os.path.relpath(file_path, root)
    except ValueError:
        # Different drives on Windows -- not a real deployment target (spec 1:
        # WSL2 only), but relpath raising is worse than a conservative fallback.
        rel = file_path
    return rel.replace(os.sep, "/")


def deny(reason: str) -> None:
    """Emit a PreToolUse deny decision and exit 0 -- spec 2.5's documented
    deny shape. A deny decision overrides the permission mode entirely."""
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )
    sys.exit(0)


def allow() -> None:
    """No opinion -- exit 0 with no output lets the normal permission system
    decide. Never used to *grant* access; only to step aside."""
    sys.exit(0)


def task_allows_test_edits(db_path: str | None, task_id: str | None) -> bool:
    """Read-only lookup of `task_queue.allow_test_edits` for the running task.

    Fails closed (returns False) on any uncertainty -- missing env vars, a
    missing database, a missing row, or a query error -- because a guard that
    cannot verify permission and defaults to allowing it is not a guard. This
    mirrors the harness's own dontAsk posture (spec 2.3): fail closed and fail
    loud, never silently permissive.
    """
    if not db_path or not task_id or not os.path.isfile(db_path):
        return False
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return False
    try:
        row = conn.execute(
            "SELECT allow_test_edits FROM task_queue WHERE task_id = ?", (task_id,)
        ).fetchone()
    except sqlite3.Error:
        return False
    finally:
        conn.close()
    return row is not None and bool(row[0])
