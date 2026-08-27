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
