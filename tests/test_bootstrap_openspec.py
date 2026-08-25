"""`ensure_openspec_initialized` (spec 10.4 step 2, plan Phase 4).

`fixtures/fake_openspec.sh` stands in for the real binary for fast unit
tests, mirroring `fake_docker.sh`/`fake_claude.sh`. The one real invocation
(`test_real_openspec_binary_initializes_a_scratch_repo`) is the integration
check the handoff calls out: `openspec init` was confirmed by hand to be a
safe, idempotent, offline, no-side-effect operation, so a real subprocess
call here is cheap rather than something to fake away.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from cosmo.bootstrap.openspec import OpenSpecInitError, ensure_openspec_initialized

FAKE_OPENSPEC = Path(__file__).resolve().parent / "fixtures" / "fake_openspec.sh"


def test_initializes_a_repo_that_has_no_openspec_dir_yet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "calls.log"
    monkeypatch.setenv("FAKE_OPENSPEC_LOG", str(log))
    target = tmp_path / "repo"
    target.mkdir()

    result = ensure_openspec_initialized(target, binary=str(FAKE_OPENSPEC))

    assert result.ran is True
    assert result.exit_code == 0
    assert (target / "openspec" / "config.yaml").is_file()
    assert f"init {target} --tools none --force" in log.read_text()


def test_skips_the_binary_entirely_when_openspec_dir_already_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "calls.log"
    monkeypatch.setenv("FAKE_OPENSPEC_LOG", str(log))
    target = tmp_path / "repo"
    (target / "openspec").mkdir(parents=True)

    result = ensure_openspec_initialized(target, binary=str(FAKE_OPENSPEC))

    assert result.ran is False
    assert not log.exists()


def test_a_nonzero_exit_raises_openspecrniterror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "calls.log"
    monkeypatch.setenv("FAKE_OPENSPEC_LOG", str(log))
    monkeypatch.setenv("FAKE_OPENSPEC_FAIL", "boom")
    target = tmp_path / "repo"
    target.mkdir()

    with pytest.raises(OpenSpecInitError, match="boom"):
        ensure_openspec_initialized(target, binary=str(FAKE_OPENSPEC))


@pytest.mark.skipif(
    subprocess.run(["which", "openspec"], capture_output=True, check=False).returncode != 0,
    reason="real openspec CLI not on PATH",
)
def test_real_openspec_binary_initializes_a_scratch_repo(tmp_path: Path) -> None:
    """Integration exit criterion, run against the real CLI (no network,
    confirmed offline and idempotent by hand -- see module docstring)."""
    target = tmp_path / "repo"
    target.mkdir()

    result = ensure_openspec_initialized(target)

    assert result.ran is True
    assert (target / "openspec" / "changes").is_dir()
    assert (target / "openspec" / "specs").is_dir()

    # Re-running is a safe no-op (this function's own "if absent" check, not
    # openspec's own idempotency -- but confirms no side effect either way).
    second = ensure_openspec_initialized(target)
    assert second.ran is False
