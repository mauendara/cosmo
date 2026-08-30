"""The gitleaks pre-commit hook (spec 6.1) -- one of three secret-handling
layers (`permissions.deny` on secret paths is Phase 4's; a gate-side scan is
Phase 6's backstop).

Git hooks live in the repository's *common* `.git/hooks/` directory, which
every linked worktree shares -- `git rev-parse --git-path hooks` resolves to
the exact same path from any worktree of the repo (confirmed by hand; there
is no such thing as a genuinely per-worktree hook). Spec 6.1's "a gitleaks
pre-commit hook in each worktree" is therefore satisfied by installing once,
idempotently, on every worktree creation: cheap, and self-healing if the
file is ever deleted or a worktree is created before this has run.
"""

from __future__ import annotations

import json
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

HOOK_MARKER = "# cosmo:gitleaks-pre-commit -- managed by Cosmo, safe to overwrite"

_HOOK_SCRIPT = f"""#!/bin/sh
{HOOK_MARKER}
# Spec 6.1: a local, bypassable (--no-verify) defense-in-depth layer against
# committing a secret. `commit_integrity_guard.py` (Phase 4) denies the
# agent's own `git commit --no-verify`; a gate-side gitleaks scan (Phase 6)
# is the backstop for anything that reaches a commit anyway.
#
# Fails closed: a missing gitleaks binary blocks the commit rather than
# silently skipping the scan, mirroring the Phase 4 test-path-guard hook's
# fail-closed posture (`cosmo doctor` checks for gitleaks so this is a
# preflight-visible surprise, not a silent one).
if ! command -v gitleaks >/dev/null 2>&1; then
    echo "cosmo: gitleaks not found on PATH -- refusing to commit without a secret scan" >&2
    echo "cosmo: install gitleaks, or see 'cosmo doctor'" >&2
    exit 1
fi
exec gitleaks protect --staged --no-banner --redact -s "$(git rev-parse --show-toplevel)"
"""


class HookInstallError(RuntimeError):
    """Raised when the hooks directory itself cannot be resolved or written."""


@dataclass(frozen=True, slots=True)
class HookInstallResult:
    path: Path
    status: str  # "created" | "refreshed" | "skipped_conflict"


def _hooks_dir(repo_path: Path) -> Path:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "--git-path", "hooks"],
            capture_output=True,
            text=True,
            timeout=10.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HookInstallError(f"could not resolve hooks directory: {exc}") from exc
    if result.returncode != 0:
        raise HookInstallError(
            f"git rev-parse --git-path hooks failed: {(result.stderr or result.stdout).strip()}"
        )
    raw = result.stdout.strip()
    # From the main worktree this is repo-relative (".git/hooks"); from a
    # linked worktree it is already absolute, pointing at the common dir
    # (confirmed by hand). `repo_path` is always the main checkout here, but
    # resolving both cases costs nothing and removes the assumption.
    path = Path(raw)
    return path if path.is_absolute() else (repo_path / path).resolve()


def install_gitleaks_pre_commit_hook(repo_path: Path) -> HookInstallResult:
    """Idempotent: refreshes a hook this function itself wrote (detected by
    `HOOK_MARKER`), never clobbers a hook that predates Cosmo -- the same
    refresh-not-clobber posture `bootstrap/symlinks.py` uses for root links,
    since a `pre-commit` hook at this path may belong to the developer's own
    tooling (husky, pre-commit-framework, ...)."""
    hooks_dir = _hooks_dir(repo_path)
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hooks_dir / "pre-commit"

    if hook_path.exists():
        existing = hook_path.read_text()
        if HOOK_MARKER not in existing:
            return HookInstallResult(path=hook_path, status="skipped_conflict")
        status = "refreshed"
    else:
        status = "created"

    hook_path.write_text(_HOOK_SCRIPT)
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return HookInstallResult(path=hook_path, status=status)


@dataclass(frozen=True, slots=True)
class GitleaksFinding:
    file: str
    rule_id: str
    line: int | None


@dataclass(frozen=True, slots=True)
class GitleaksScanResult:
    clean: bool
    findings: list[GitleaksFinding] = field(default_factory=list)
    ran: bool = True  # False when gitleaks itself was unavailable (environment_error)


def run_gitleaks_scan(worktree_path: Path, *, gitleaks_bin: str = "gitleaks") -> GitleaksScanResult:
    """Spec 6.1's gate-side backstop -- the second, non-bypassable secret
    layer alongside the pre-commit hook, since local hooks are bypassable
    (`--no-verify`). Runs against the worktree's current file contents
    (`--no-git`), not the commit history: the pre-commit hook already
    enforces the per-commit boundary, so this backstop's job is only "does
    the final state of this task's work contain a secret," not re-scanning
    every commit already on `develop`. Detection only -- "any secret that
    reaches a commit is treated as compromised and requires rotation";
    remediation is a human's job, never automated here."""
    if shutil.which(gitleaks_bin) is None:
        return GitleaksScanResult(clean=False, ran=False)

    with tempfile.TemporaryDirectory() as tmp:
        report_path = Path(tmp) / "gitleaks-report.json"
        result = subprocess.run(
            [
                gitleaks_bin,
                "detect",
                "--no-git",
                "--no-banner",
                "--redact",
                "-f",
                "json",
                "-r",
                str(report_path),
                "-s",
                str(worktree_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return GitleaksScanResult(clean=True)

        findings: list[GitleaksFinding] = []
        try:
            raw = json.loads(report_path.read_text()) if report_path.exists() else []
        except json.JSONDecodeError:
            raw = []

    for item in raw:
        findings.append(
            GitleaksFinding(
                file=item.get("File", "unknown"),
                rule_id=item.get("RuleID", "unknown"),
                line=item.get("StartLine"),
            )
        )
    return GitleaksScanResult(clean=False, findings=findings)
