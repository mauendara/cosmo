"""`cosmo init`'s target-repo git-init + base-branch bootstrap.

Mirrors `bootstrap.git_identity`'s split: this module stays pure subprocess
mechanics (testable without stdin, no interactivity), while `cli.main.init`
decides what to print about the outcome.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_TIMEOUT = 10.0


def is_git_repo(target: Path) -> bool:
    return (target / ".git").exists()


def init_repo(target: Path) -> None:
    subprocess.run(
        ["git", "init"], cwd=target, check=True, capture_output=True, text=True, timeout=_TIMEOUT
    )


def branch_exists(target: Path, branch: str) -> bool:
    """A real ref, i.e. `branch` has at least one commit. Deliberately not
    sufficient on its own to mean "nothing to do" -- see `current_branch`:
    a branch with zero commits (e.g. right after `checkout -b`, which is
    exactly what `create_and_checkout_branch` below produces, since `cosmo
    init` never commits anything itself) has no ref at all yet, only a
    symbolic HEAD pointing at it."""
    result = subprocess.run(
        ["git", "-C", str(target), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        check=False,
    )
    return result.returncode == 0


def current_branch(target: Path) -> str | None:
    """The branch HEAD is on, even if unborn (zero commits) -- unlike
    `branch_exists`, this reflects `checkout -b`'s effect immediately.
    `None` if HEAD is detached or the call otherwise fails."""
    result = subprocess.run(
        ["git", "-C", str(target), "symbolic-ref", "--short", "-q", "HEAD"],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        check=False,
    )
    name = result.stdout.strip()
    return name if result.returncode == 0 and name else None


def working_tree_is_clean(target: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(target), "status", "--porcelain"],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        check=True,
    )
    return result.stdout.strip() == ""


def create_and_checkout_branch(target: Path, branch: str) -> None:
    subprocess.run(
        ["git", "-C", str(target), "checkout", "-b", branch],
        check=True,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
    )


def checkout_branch(target: Path, branch: str) -> None:
    """Plain `git checkout <branch>` (no `-b`) -- for `cosmo_branch` mode's
    idempotent re-run, where the branch already exists as a ref and just
    needs to become HEAD."""
    subprocess.run(
        ["git", "-C", str(target), "checkout", branch],
        check=True,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
    )


def has_commits(target: Path) -> bool:
    """Whether HEAD is born (at least one commit exists anywhere in
    history) -- `git stash` refuses outright ("You do not have the initial
    commit yet") on an unborn HEAD, since it has no commit to diff against
    or attach a stash entry to."""
    result = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "--verify", "-q", "HEAD"],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        check=False,
    )
    return result.returncode == 0


def stash_all(target: Path, message: str) -> bool:
    """`git stash push -u` if the working tree is dirty, so `cosmo_branch`
    mode never needs `direct` mode's `SKIPPED_DIRTY` bailout -- dirtiness is
    always resolved this way before the fork, not surfaced as a blocker.
    Returns whether anything was actually stashed -- `False` (a no-op) on an
    already-clean tree, and also on an unborn HEAD (see `has_commits`):
    there is nothing to protect there either, since whatever the working
    tree holds becomes part of `cosmo_branch` mode's own first real commit
    regardless of which branch it lands on."""
    if working_tree_is_clean(target) or not has_commits(target):
        return False
    subprocess.run(
        ["git", "-C", str(target), "stash", "push", "-u", "-m", message],
        check=True,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
    )
    return True


def create_branch_from(target: Path, new_branch: str, from_branch: str) -> None:
    subprocess.run(
        ["git", "-C", str(target), "checkout", "-b", new_branch, from_branch],
        check=True,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
    )


def commit_bootstrap_output(target: Path) -> bool:
    """Commits whatever `run_init`'s own steps (`openspec/`, `docs/`,
    `.agent/<harness>/`, root symlinks) just wrote or changed in `target`'s
    working tree. Returns `False` (no-op) when there's nothing to commit --
    e.g. re-running `cosmo init` against an already-committed, unchanged
    tree.

    Found live: `cosmo init` never committed its own output on its own (see
    this module's own `branch_exists` docstring, which already names the
    fact) -- a freshly initialized repo's `openspec/`, `docs/`, `.agent/`
    sat untracked/uncommitted from the moment `cosmo init` returned. The
    very first task ever run against such a repo then hit `MERGING`'s
    `_assert_ready` refusing to merge onto a dirty `repo_path`, identical in
    shape to `task.machine._do_finishing`'s own now-fixed bug (deviation
    68) but one step earlier: nothing had committed the tree even once.
    Re-running `cosmo init` later, after `templates/harness/claude/`
    changed upstream, reproduces the same symptom via `sync_harness_assets`'
    unconditional re-sync -- committing here closes both at once, since
    both leave real, committable changes in the same working tree this
    function already scans. Requires a configured git identity in `target`
    (`cli.main.init` calls this only after `_ensure_git_identity`)."""
    if working_tree_is_clean(target):
        return False
    subprocess.run(
        ["git", "-C", str(target), "add", "-A"],
        check=True,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
    )
    subprocess.run(
        ["git", "-C", str(target), "commit", "-m", "cosmo: init bootstrap"],
        check=True,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
    )
    return True
