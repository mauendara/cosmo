"""Spec 11's "guarding against note rot": a hard size cap on every knowledge
file, enforced deterministically by Cosmo rather than trusted to the LLM --
`templates/harness/claude/CLAUDE.md` already tells the agent to *say so in
its summary instead of trimming itself* when a file would exceed the cap,
so this module is the enforcement side of that contract.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def _is_docs_markdown(path: str) -> bool:
    # A plain prefix/suffix check rather than a `docs/**/*.md`-shaped glob:
    # `fnmatch`'s `**` is not a recursive-directory wildcard (it is just two
    # `*`s, so it still requires a literal `/` between them and the
    # trailing `*.md`) -- found by hand, it silently rejected
    # `docs/architecture.md` itself, only matching a file at least one
    # subdirectory deep. `pathlib.PurePosixPath.match` has the same
    # "**/" pitfall pre-3.13. A prefix/suffix check has no such edge case.
    return path.startswith("docs/") and path.endswith(".md")


def docs_md_files(worktree_path: Path, base_branch: str, task_branch: str) -> list[str]:
    """Every `docs/**/*.md` path the task's own commits (already made by the
    harness during `IMPLEMENTING`, per `CLAUDE.md`'s "Project knowledge"
    instructions) touched relative to `base_branch` -- computed fresh via
    `git diff`, not threaded through from `HarnessResult.files_changed`, so
    `COMMITTING` doesn't depend on the specific harness call that preceded
    it and can be re-checked identically on an informed retry.
    """
    result = subprocess.run(
        ["git", "-C", str(worktree_path), "diff", "--name-only", f"{base_branch}...{task_branch}"],
        capture_output=True,
        text=True,
        timeout=60.0,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [path for path in result.stdout.splitlines() if path and _is_docs_markdown(path)]


def files_over_cap(worktree_path: Path, relative_paths: list[str], max_lines: int) -> list[str]:
    """Which of `relative_paths` (read from the worktree, as they stand
    right now) exceed `max_lines`. Missing files are skipped rather than
    treated as a violation -- a file the diff named but that no longer
    exists (renamed, deleted) has nothing to enforce a cap on."""
    over: list[str] = []
    for rel in relative_paths:
        path = worktree_path / rel
        if not path.is_file():
            continue
        line_count = sum(1 for _ in path.open("r", encoding="utf-8", errors="replace"))
        if line_count > max_lines:
            over.append(rel)
    return over
