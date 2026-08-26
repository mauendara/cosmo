"""`bootstrap.git_identity` -- `cosmo init`'s target-repo git identity step
(spec 3.4 extended). Pure subprocess mechanics against a real `tmp_path` git
repo, no interactivity (that lives in `cli.main.init`, tested separately)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from cosmo.bootstrap.git_identity import GitIdentity, read_configured_identity, set_local_identity


def _git_repo(tmp_path: Path) -> Path:
    target = tmp_path / "target-repo"
    target.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    return target


@pytest.fixture(autouse=True)
def _no_global_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # An isolated HOME with no global gitconfig, so every test here reflects
    # only the repo's own local config, not whatever identity this dev box
    # happens to have configured globally.
    empty_home = tmp_path / "empty-home"
    empty_home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(empty_home))


def test_read_configured_identity_returns_none_when_nothing_is_set(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)

    assert read_configured_identity(repo) is None


def test_set_then_read_round_trips(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    set_local_identity(repo, GitIdentity(name="Test Author", email="test@example.com"))

    identity = read_configured_identity(repo)

    assert identity == GitIdentity(name="Test Author", email="test@example.com")


def test_set_local_identity_writes_local_config_not_global(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    set_local_identity(repo, GitIdentity(name="Local Only", email="local@example.com"))

    local = subprocess.run(
        ["git", "-C", str(repo), "config", "--local", "--get", "user.name"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert local.stdout.strip() == "Local Only"

    # A fresh repo elsewhere never sees this identity -- confirms it wasn't
    # written to global config.
    other = tmp_path / "other-repo"
    other.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=other, check=True)
    assert read_configured_identity(other) is None


def test_set_local_identity_overwrites_an_existing_one(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    set_local_identity(repo, GitIdentity(name="First", email="first@example.com"))
    set_local_identity(repo, GitIdentity(name="Second", email="second@example.com"))

    identity = read_configured_identity(repo)

    assert identity == GitIdentity(name="Second", email="second@example.com")


def test_read_configured_identity_requires_both_name_and_email(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    subprocess.run(
        ["git", "-C", str(repo), "config", "--local", "user.name", "Only Name"],
        check=True,
        capture_output=True,
    )

    assert read_configured_identity(repo) is None
