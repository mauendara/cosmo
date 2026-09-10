#!/usr/bin/env python3
"""PreToolUse guard: blocks edits under protected test paths (spec 2.5, 6.1
layer 1). Bypassed only when the task's queue row has `allow_test_edits: true`
-- or, since G4 (docs/v15-fixes-after-wa-chat-run.md), when the specific
edit being requested structurally qualifies for the same allow-list
`gate.diffgate` checks post-commit (doesn't reduce assertions, doesn't
introduce a skip annotation, doesn't represent a full-file deletion).

Matched on `Edit`/`Write`/`NotebookEdit` via settings.json. Protected
patterns (spec 2.5's literal list, plus `.tsx`/`.jsx` -- found writing the
`vite-react-local` project template: a React test file that renders JSX
must itself be `.tsx`, so `**/*.test.ts` alone leaves every component test
in a TS+JSX project unprotected. This is a project-agnostic widening, not
something specific to one template -- any TS/JS + JSX codebase hits the
same gap):
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

import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hooklib import (  # noqa: E402
    allow,
    deny,
    project_dir,
    read_hook_input,
    relative_path,
    task_allows_test_edits,
)
from _structural_checks import count_assertions, find_skip_annotation, is_test_path  # noqa: E402

GUARDED_TOOLS = frozenset({"Edit", "Write", "NotebookEdit"})

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

# G4: mirrors `config/defaults.toml`'s `diff_gate_skip_annotations`/
# `diff_gate_loc_drop_threshold` real defaults. This hook runs standalone,
# inside the target repo, with no `cosmo` package or config file on the
# path (`_hooklib.py`'s own docstring) -- same reason `PROTECTED_PATTERNS`
# above is already a hardcoded literal rather than read from
# `gate.diff_gate_test_path_patterns`. If either default is ever retuned in
# `config/defaults.toml`, update this literal to match by hand.
SKIP_ANNOTATIONS = (
    "@Disabled",
    "@Ignore",
    ".skip(",
    ".only(",
    "xit(",
    "xdescribe(",
    "test.skip(",
    "describe.skip(",
)
LOC_DROP_THRESHOLD = 20


def _read_current_content(file_path: str) -> str | None:
    try:
        with open(file_path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def _structural_denial_reason(
    tool_name: str, tool_input: dict[str, Any], file_path: str
) -> str | None:
    """`None` means this specific edit structurally qualifies for the
    allow-list even without `allow_test_edits` -- otherwise, a short reason
    to include in the deny message. Mirrors `gate.diffgate.run_diff_gate`'s
    own real-diff checks (assertion count, skip annotations, LOC drop)
    against just the one call being requested, since this fires before a
    commit -- there is no real git diff yet to compute."""
    if tool_name == "Write":
        new_content = tool_input.get("content")
        if not isinstance(new_content, str):
            return "the edit could not be inspected"
        new_lines = new_content.splitlines()
        old_content = _read_current_content(file_path)
        if old_content is None:
            # diffgate's own carve-out: a newly-added test file is exactly
            # what a well-behaved agent is expected to produce for new work
            # -- still suspicious if it's born already-disabled.
            hit = find_skip_annotation(new_lines, SKIP_ANNOTATIONS)
            return f"introduces {hit!r} in a new test file" if hit else None
        old_lines = old_content.splitlines()
    elif tool_name == "Edit":
        old_string = tool_input.get("old_string")
        new_string = tool_input.get("new_string")
        if not isinstance(old_string, str) or not isinstance(new_string, str):
            return "the edit could not be inspected"
        old_lines = old_string.splitlines()
        new_lines = new_string.splitlines()
    else:
        # NotebookEdit: a notebook's JSON structure makes line-level
        # assertion-counting unreliable, so it has no structural allow-list
        # -- allow_test_edits (set in advance) remains the only bypass.
        return "this tool has no structural allow-list"

    net_assertions = count_assertions(new_lines) - count_assertions(old_lines)
    if net_assertions < 0:
        return f"would decrease the assertion count by {-net_assertions}"
    hit = find_skip_annotation(new_lines, SKIP_ANNOTATIONS)
    if hit is not None:
        return f"introduces {hit!r}"
    net_loc = len(new_lines) - len(old_lines)
    if -net_loc > LOC_DROP_THRESHOLD:
        return f"would drop {-net_loc} lines (over the {LOC_DROP_THRESHOLD}-line threshold)"
    return None


def _is_protected(rel_path: str) -> str | None:
    return is_test_path(rel_path, PROTECTED_PATTERNS)


def main() -> None:
    payload = read_hook_input()
    tool_name = payload.get("tool_name")
    if tool_name not in GUARDED_TOOLS:
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

    reason = _structural_denial_reason(str(tool_name), tool_input, str(file_path))
    if reason is None:
        allow()
        return

    deny(
        f"test-path guard: {rel!r} matches protected pattern {matched!r} (spec 2.5) "
        f"and {reason}. Set allow_test_edits on the task's queue row to bypass."
    )


if __name__ == "__main__":
    main()
