"""Step 2 of `cosmo init` (spec 10.4): create `openspec/` via OpenSpec's own
CLI, if absent.

`--tools none` is deliberate, not the obvious default: `openspec init --tools
claude` was probed by hand against a scratch repo and found to write a real
`.claude/commands/` and `.claude/skills/` directory tree of its own -- which
directly conflicts with spec 10.2's `.claude -> .agent/claude` symlink (you
cannot have a real directory and a symlink at the same path). Cosmo's own
`templates/harness/claude/` is the harness-facing integration; OpenSpec's
role here is only `openspec/` itself. `--force` suppresses an interactive
legacy-file-cleanup prompt that would otherwise block unattended use; probed
by hand to confirm it does not touch an already-initialized `openspec/`
(re-running is a safe no-op either way).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

BINARY = "openspec"


class OpenSpecInitError(RuntimeError):
    """Raised when `openspec init` exits non-zero."""


@dataclass(frozen=True, slots=True)
class OpenSpecResult:
    ran: bool  # False when openspec/ already existed and the CLI was not invoked
    exit_code: int | None
    stdout: str
    stderr: str


def ensure_openspec_initialized(
    target: Path,
    *,
    binary: str = BINARY,
    timeout: float = 60.0,
) -> OpenSpecResult:
    if (target / "openspec").is_dir():
        return OpenSpecResult(ran=False, exit_code=None, stdout="", stderr="")

    try:
        result = subprocess.run(
            [binary, "init", str(target), "--tools", "none", "--force"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OpenSpecInitError(f"could not run {binary!r}: {exc}") from exc

    if result.returncode != 0:
        raise OpenSpecInitError(
            f"openspec init exited {result.returncode}: {(result.stderr or result.stdout).strip()}"
        )

    return OpenSpecResult(
        ran=True, exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr
    )
