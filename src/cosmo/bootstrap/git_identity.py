"""`cosmo init`'s target-repo git identity step (spec 3.4, extended).

Worktree creation (Phase 5) never sets a local git identity, and a fresh
host may have no global `~/.gitconfig` either (found by hand, Phase 5) --
without this step, the *implementer's* own ad hoc `git commit` during
IMPLEMENTING (not one of Cosmo's own `-c user.name=...`-flagged invocations)
would fail outright on such a host with no identity to fall back to. This
module's job is only to read/write the target repo's own *local* git
config; it never touches global config, mirroring `GitConfig.
commit_author_name`'s own "never written to global git config" discipline.

The interactive decision (warn if one already exists, ask whether to define
a new one, prompt for it) lives in `cli.main.init` -- this module stays pure
subprocess mechanics so it's testable without stdin.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

_TIMEOUT = 10.0


@dataclass(frozen=True, slots=True)
class GitIdentity:
    name: str
    email: str


def read_configured_identity(target: Path) -> GitIdentity | None:
    """The identity `git commit` would actually use right now in `target` --
    local config if set, else global, else `None`. `git config --get` (no
    `--local`/`--global`) resolves in that same order on its own, so this
    reflects what a plain `git commit` would do, not just one scope."""
    name = _config_get(target, "user.name")
    email = _config_get(target, "user.email")
    if name and email:
        return GitIdentity(name=name, email=email)
    return None


def _config_get(target: Path, key: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(target), "config", "--get", key],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        check=False,
    )
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def set_local_identity(target: Path, identity: GitIdentity) -> None:
    """Writes `user.name`/`user.email` into `target`'s own local
    `.git/config`, overwriting whatever was there (local or inherited from
    global) -- the caller (`cli.main.init`) is what decides whether
    overwriting is actually warranted."""
    for key, value in (("user.name", identity.name), ("user.email", identity.email)):
        subprocess.run(
            ["git", "-C", str(target), "config", "--local", key, value],
            check=True,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
        )
