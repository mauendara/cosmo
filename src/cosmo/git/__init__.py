"""Worktree lifecycle and git operations (spec 3.2, 3.4, plan Phase 5)."""

from __future__ import annotations

from cosmo.git.merge import (
    GateRerun,
    MergeCommandError,
    MergeOutcome,
    MergeResult,
    attempt_merge_ladder,
    merge_task,
)
from cosmo.git.secrets import HookInstallError, HookInstallResult, install_gitleaks_pre_commit_hook
from cosmo.git.worktree import (
    SweepOutcome,
    WorktreeError,
    WorktreeInfo,
    create_worktree,
    remove_worktree,
    sweep_stale_worktrees,
)

__all__ = [
    "GateRerun",
    "MergeCommandError",
    "MergeOutcome",
    "MergeResult",
    "attempt_merge_ladder",
    "merge_task",
    "HookInstallError",
    "HookInstallResult",
    "install_gitleaks_pre_commit_hook",
    "SweepOutcome",
    "WorktreeError",
    "WorktreeInfo",
    "create_worktree",
    "remove_worktree",
    "sweep_stale_worktrees",
]
