"""The spec 3.4 merge-conflict ladder.

`repo_path` is Cosmo's own dedicated checkout of `base_branch` -- never a
developer's interactive working directory. Task work always happens in an
isolated linked worktree (`git.worktree`); `repo_path` itself stays on
`base_branch` at all times so the merge/rebase steps below can run directly
against it. This is a deliberate Phase 5 design decision, not an oversight:
an earlier design attempted the merge in a *second*, ephemeral worktree
checked out on `base_branch` so `repo_path` would never be touched at all,
but git refuses to check out a branch that is already checked out in another
worktree -- confirmed by hand -- and `base_branch` is already checked out in
`repo_path`. There is no way to keep the merge fully outside `repo_path`
without abandoning `base_branch`'s working-tree state there entirely.

`gate_rerun` is a Phase-6 seam: a full validation-gate re-run, injected as a
plain callable. This module never imports `cosmo.harness` at all (asserted
by `tests/test_git_boundary.py`), which is what makes spec 3.4 step 2 --
"the conflict is never handed back to the agent to resolve blind" --
structural rather than a matter of convention: there is no harness adapter
anywhere in scope for this code path to hand a conflict to.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cosmo.events import EventEmitter, EventType, Severity, emit_state_changed
from cosmo.git.worktree import remove_worktree
from cosmo.store.enums import BlockedReason
from cosmo.store.writer import StoreWriter

GateRerun = Callable[[], bool]


class MergeCommandError(RuntimeError):
    """A git invocation in the ladder failed for a reason other than an
    ordinary content conflict (git missing, a timeout, a precondition
    violated) -- these are environment errors, not merge conflicts, and are
    deliberately never folded into `MergeOutcome.blocked_reason`."""


@dataclass(frozen=True, slots=True)
class MergeOutcome:
    merged: bool
    rebase_attempted: bool
    blocked_reason: BlockedReason | None


def _git(
    cwd: Path, *args: str, author: tuple[str, str] | None, timeout: float = 300.0
) -> subprocess.CompletedProcess[str]:
    # `author=None` means unified_identity: no `-c` override at all, so this
    # invocation inherits whatever git identity is configured locally in
    # `cwd` (the same one the implementer's own ad hoc commits already use)
    # instead of Cosmo's own distinct commit_author_name/email.
    identity_flags = []
    if author is not None:
        name, email = author
        identity_flags = ["-c", f"user.name={name}", "-c", f"user.email={email}"]
    try:
        return subprocess.run(
            ["git", "-C", str(cwd), *identity_flags, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MergeCommandError(f"could not run git {' '.join(args)}: {exc}") from exc


def _assert_ready(repo_path: Path, base_branch: str, author: tuple[str, str] | None) -> None:
    """Spec 3.4 assumes `repo_path` is sitting cleanly on `base_branch`
    before any of this runs; a violated precondition should fail loudly
    rather than merge into the wrong branch or clobber uncommitted state."""
    current = _git(repo_path, "branch", "--show-current", author=author)
    if current.stdout.strip() != base_branch:
        raise MergeCommandError(
            f"{repo_path} is on {current.stdout.strip()!r}, not {base_branch!r} -- "
            f"refusing to merge (spec 3.4 assumes repo_path is always on base_branch)"
        )
    status = _git(repo_path, "status", "--porcelain", author=author)
    if status.stdout.strip():
        raise MergeCommandError(f"{repo_path} has uncommitted changes -- refusing to merge")


def attempt_merge_ladder(
    *,
    repo_path: Path,
    worktree_path: Path,
    branch: str,
    base_branch: str,
    gate_rerun: GateRerun,
    author: tuple[str, str] | None,
) -> MergeOutcome:
    """Pure git mechanics -- no `StoreWriter`/`EventEmitter` here, mirroring
    `proc.orphans.sweep()` (mechanics) vs `proc.reap.cancel_and_reap()` (ties
    mechanics to persisted state): `merge_task` below is this module's
    `cancel_and_reap`.
    """
    _assert_ready(repo_path, base_branch, author)

    merged = _git(repo_path, "merge", "--no-edit", branch, author=author)
    if merged.returncode == 0:
        return MergeOutcome(merged=True, rebase_attempted=False, blocked_reason=None)

    # Conflict on the first attempt. Spec 3.4 step 2: never handed back to
    # the agent to resolve blind -- abort cleanly and fall through to the
    # one allowed automated recovery.
    _git(repo_path, "merge", "--abort", author=author)

    rebase = _git(worktree_path, "rebase", base_branch, author=author)
    if rebase.returncode != 0:
        _git(worktree_path, "rebase", "--abort", author=author)
        return MergeOutcome(
            merged=False, rebase_attempted=True, blocked_reason=BlockedReason.MERGE_CONFLICT
        )

    if not gate_rerun():
        # Spec 3.4's "otherwise" covers this too: a rebase that succeeds but
        # a gate that then fails is still routed to BLOCKED/merge_conflict,
        # not treated as a fresh code_error -- the spec's own choice, kept
        # as written rather than reinterpreted.
        return MergeOutcome(
            merged=False, rebase_attempted=True, blocked_reason=BlockedReason.MERGE_CONFLICT
        )

    retry = _git(repo_path, "merge", "--no-edit", branch, author=author)
    if retry.returncode != 0:
        _git(repo_path, "merge", "--abort", author=author)
        return MergeOutcome(
            merged=False, rebase_attempted=True, blocked_reason=BlockedReason.MERGE_CONFLICT
        )
    return MergeOutcome(merged=True, rebase_attempted=True, blocked_reason=None)


@dataclass(frozen=True, slots=True)
class MergeResult:
    outcome: MergeOutcome
    worktree_removed: bool


def merge_task(
    *,
    repo_path: Path,
    worktree_path: Path,
    branch: str,
    base_branch: str,
    task_id: str,
    run_id: str | None,
    writer: StoreWriter,
    emitter: EventEmitter,
    gate_rerun: GateRerun,
    author: tuple[str, str] | None,
) -> MergeResult:
    """Ties `attempt_merge_ladder` to persisted state and events: `DONE`
    removes the worktree and deletes the branch (spec 3.2); `BLOCKED`
    retains both for inspection and emits `task.blocked` at `severity =
    warning` (spec 3.4 step 4) -- `merge_conflict` is excluded from the
    circuit-breaker tally (spec 3.4, 6.5; there is no breaker yet to feed,
    Phase 8, but this is where that exclusion will matter).
    """
    outcome = attempt_merge_ladder(
        repo_path=repo_path,
        worktree_path=worktree_path,
        branch=branch,
        base_branch=base_branch,
        gate_rerun=gate_rerun,
        author=author,
    )

    if outcome.merged:
        remove_worktree(repo_path=repo_path, worktree_path=worktree_path, branch=branch)
        transition = writer.queue_complete(task_id, run_id=run_id)
        emitter.emit(
            event_type=EventType.TASK_COMPLETED,
            severity=Severity.INFO,
            run_id=run_id,
            task_id=task_id,
            payload={"rebase_attempted": outcome.rebase_attempted},
        )
        emit_state_changed(emitter, transition)
    else:
        # attempt_merge_ladder always sets blocked_reason when merged=False.
        assert outcome.blocked_reason is not None
        transition = writer.queue_block(
            task_id,
            outcome.blocked_reason,
            run_id=run_id,
            note="automated merge/rebase recovery did not succeed",
        )
        emitter.emit(
            event_type=EventType.TASK_BLOCKED,
            severity=Severity.WARNING,
            run_id=run_id,
            task_id=task_id,
            payload={
                "blocked_reason": outcome.blocked_reason.value,
                "rebase_attempted": outcome.rebase_attempted,
            },
        )
        emit_state_changed(emitter, transition)

    return MergeResult(outcome=outcome, worktree_removed=outcome.merged)
