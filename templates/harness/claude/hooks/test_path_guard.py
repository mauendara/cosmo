#!/usr/bin/env python3
"""PreToolUse guard: blocks edits under protected test paths (spec 2.5, 6.1
layer 1). Bypassed only when the task's queue row has `allow_test_edits: true`.

Matched on `Edit`/`Write` via settings.json. Protected patterns (spec 2.5's
literal list, plus `.tsx`/`.jsx` -- found writing the `vite-react-local`
project template: a React test file that renders JSX must itself be `.tsx`,
so `**/*.test.ts` alone leaves every component test in a TS+JSX project
unprotected. This is a project-agnostic widening, not something specific to
one template -- any TS/JS + JSX codebase hits the same gap):
  - src/test/**       (repo-root anchored)
  - e2e/**            (repo-root anchored)
  - **/*.spec.ts      (anywhere)
  - **/*.test.ts      (anywhere)
  - **/*.spec.tsx     (anywhere)
  - **/*.test.tsx     (anywhere)
  - **/*.spec.jsx     (anywhere)
  - **/*.test.jsx     (anywhere)
"""

from __future__ import annotations

import fnmatch
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hooklib import (  # noqa: E402
    allow,
    deny,
    project_dir,
    read_hook_input,
    relative_path,
    task_allows_test_edits,
)

GUARDED_TOOLS = frozenset({"Edit", "Write", "NotebookEdit"})

# fnmatch's `*` already matches path separators (it has no concept of path
# segments), so `**` behaves the same as a single `*` here -- sufficient for
# these four literal patterns without pulling in a globbing library.
PROTECTED_PATTERNS = (
    "src/test/**",
    "e2e/**",
    "**/*.spec.ts",
    "**/*.test.ts",
    "**/*.spec.tsx",
    "**/*.test.tsx",
    "**/*.spec.jsx",
    "**/*.test.jsx",
)


def _is_protected(rel_path: str) -> str | None:
    for pattern in PROTECTED_PATTERNS:
        if fnmatch.fnmatch(rel_path, pattern):
            return pattern
    return None


def main() -> None:
    payload = read_hook_input()
    if payload.get("tool_name") not in GUARDED_TOOLS:
        allow()

    tool_input = payload.get("tool_input") or {}
    file_path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not file_path:
        allow()

    rel = relative_path(str(file_path), project_dir(payload))
    matched = _is_protected(rel)
    if matched is None:
        allow()
        return

    task_id = os.environ.get("COSMO_TASK_ID")
    db_path = os.environ.get("COSMO_DB_PATH")
    if task_allows_test_edits(db_path, task_id):
        allow()
        return

    deny(
        f"test-path guard: {rel!r} matches protected pattern {matched!r} "
        f"(spec 2.5). Set allow_test_edits on the task's queue row to bypass."
    )


if __name__ == "__main__":
    main()
