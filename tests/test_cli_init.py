"""`cosmo init` / `cosmo templates list` (spec 10.4, 10.3, plan Phase 4 exit
criteria)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cosmo.bootstrap.git_identity import GitIdentity, read_configured_identity
from cosmo.cli.main import app
from cosmo.config import load_config
from cosmo.store.reader import find_project_by_path

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COSMO_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    # No global git identity on the host running this test, regardless of
    # what this dev box's own ~/.gitconfig actually has -- the new git
    # identity step (spec 3.4 extended) must see a clean slate.
    empty_home = tmp_path / "empty-home"
    empty_home.mkdir()
    monkeypatch.setenv("HOME", str(empty_home))


def _db_path() -> Path:
    return load_config().paths.db_path


def _git_repo(tmp_path: Path) -> Path:
    target = tmp_path / "target-repo"
    target.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    return target


def test_templates_list_shows_the_real_shipped_templates() -> None:
    result = runner.invoke(app, ["templates", "list"])
    assert result.exit_code == 0
    assert "claude" in result.stdout
    assert "_blank" in result.stdout
    assert "java-spring-react" in result.stdout


_IDENTITY_INPUT = "Test Dev\ntest@example.com\n"


@pytest.mark.skipif(
    subprocess.run(["which", "openspec"], capture_output=True, check=False).returncode != 0,
    reason="real openspec CLI not on PATH",
)
def test_init_auto_inits_a_non_git_directory(tmp_path: Path) -> None:
    target = tmp_path / "plain-dir"
    target.mkdir()
    result = runner.invoke(app, ["init", str(target)], input=_IDENTITY_INPUT)
    assert result.exit_code == 0, result.stdout
    assert (target / ".git").is_dir()
    current_branch = subprocess.run(
        ["git", "-C", str(target), "branch", "--show-current"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert current_branch == "develop"
    combined = " ".join(result.stdout.split())
    assert "git init" in combined and "develop" in combined


@pytest.mark.skipif(
    subprocess.run(["which", "openspec"], capture_output=True, check=False).returncode != 0,
    reason="real openspec CLI not on PATH",
)
def test_init_against_a_scratch_git_repo_produces_every_documented_artifact(
    tmp_path: Path,
) -> None:
    target = _git_repo(tmp_path)

    result = runner.invoke(
        app,
        ["init", str(target), "--project-template", "java-spring-react"],
        input=_IDENTITY_INPUT,
    )

    assert result.exit_code == 0, result.stdout
    assert (target / "openspec" / "changes").is_dir()
    assert (target / "docs" / "base-standards.md").is_file()
    assert (target / ".agent" / "claude" / "settings.json").is_file()
    assert (target / "CLAUDE.md").is_symlink()
    assert "registered" in result.stdout


@pytest.mark.skipif(
    subprocess.run(["which", "openspec"], capture_output=True, check=False).returncode != 0,
    reason="real openspec CLI not on PATH",
)
def test_init_commits_its_own_bootstrap_output(tmp_path: Path) -> None:
    """Found live: `cosmo init` never committed `openspec/`, `docs/`,
    `.agent/<harness>/`, or the root symlinks on its own -- confirmed by
    hand against a real scratch repo, where the very first task ever run
    against it hit `MERGING`'s `_assert_ready` refusing to merge onto a
    dirty `repo_path`, before any task-level bug had a chance to dirty
    anything. `cosmo init` must leave the target repo on a clean, real
    commit -- not just an unborn-or-dirty branch -- so the first `cosmo
    run` doesn't inherit init's own leftovers."""
    target = _git_repo(tmp_path)

    result = runner.invoke(app, ["init", str(target)], input=_IDENTITY_INPUT)

    assert result.exit_code == 0, result.stdout
    assert "committed" in result.stdout
    status = subprocess.run(
        ["git", "-C", str(target), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert status.stdout.strip() == "", f"target left dirty after init: {status.stdout!r}"
    log = subprocess.run(
        ["git", "-C", str(target), "log", "-1", "--format=%s"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "init bootstrap" in log.stdout


@pytest.mark.skipif(
    subprocess.run(["which", "openspec"], capture_output=True, check=False).returncode != 0,
    reason="real openspec CLI not on PATH",
)
def test_init_cosmo_branch_mode_flag_forks_and_commits_on_the_cosmo_branch(
    tmp_path: Path,
) -> None:
    """v14: `--base-branch-mode cosmo_branch` forks `cosmo` (default name)
    off `develop` and leaves the target checked out there -- the real
    `develop` gets no scaffolding commit at all."""
    target = _git_repo(tmp_path)

    result = runner.invoke(
        app,
        ["init", str(target), "--base-branch-mode", "cosmo_branch"],
        input=_IDENTITY_INPUT,
    )

    assert result.exit_code == 0, result.stdout
    assert "committed" in result.stdout
    current_branch = subprocess.run(
        ["git", "-C", str(target), "branch", "--show-current"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert current_branch == "cosmo"

    project = find_project_by_path(_db_path(), str(target.resolve()))
    assert project is not None
    assert project.base_branch_mode == "cosmo_branch"
    assert project.real_base_branch == "develop"
    assert project.cosmo_branch_name == "cosmo"


@pytest.mark.skipif(
    subprocess.run(["which", "openspec"], capture_output=True, check=False).returncode != 0,
    reason="real openspec CLI not on PATH",
)
def test_init_cosmo_branch_mode_stashes_dirty_work_and_reports_recovery(
    tmp_path: Path,
) -> None:
    """Unlike `direct` mode's `SKIPPED_DIRTY` bailout, `cosmo_branch` mode
    never gives up on a dirty tree -- it stashes it and reports the exact
    recovery command instead. `develop` needs a real commit first --
    `git stash` refuses on an unborn HEAD (no commit to diff against yet),
    a separate no-op case `test_bootstrap_git_branch.py` covers directly."""
    target = tmp_path / "dirty-repo"
    target.mkdir()
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "init", "-q"],
        cwd=target,
        check=True,
    )
    (target / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "README.md"], cwd=target, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.com",
            "commit",
            "-q",
            "-m",
            "base",
        ],
        cwd=target,
        check=True,
    )
    subprocess.run(["git", "branch", "-M", "develop"], cwd=target, check=True)
    develop_tip_before = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "develop"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    (target / "untracked.txt").write_text("someone's own work in progress\n")

    result = runner.invoke(
        app,
        ["init", str(target), "--base-branch-mode", "cosmo_branch", "--cosmo-branch-name", "iso"],
        input=_IDENTITY_INPUT,
    )

    assert result.exit_code == 0, result.stdout
    assert "stashed" in result.stdout
    # Console wraps long lines, so check the recovery command's pieces
    # rather than one exact contiguous substring.
    assert "checkout develop" in result.stdout
    assert "git stash pop" in result.stdout
    current_branch = subprocess.run(
        ["git", "-C", str(target), "branch", "--show-current"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert current_branch == "iso"
    assert (
        "untracked.txt"
        not in subprocess.run(
            ["git", "-C", str(target), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )

    # `develop`'s own tip is completely unchanged -- the scaffolding commit
    # landed on `iso`, not there.
    develop_tip_after = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "develop"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert develop_tip_after == develop_tip_before
    iso_log = subprocess.run(
        ["git", "-C", str(target), "log", "iso", "--format=%s"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "init bootstrap" in iso_log

    # ...and the stash recovery command actually works.
    subprocess.run(["git", "-C", str(target), "checkout", "develop"], check=True)
    subprocess.run(["git", "-C", str(target), "stash", "pop"], check=True)
    assert (target / "untracked.txt").is_file()


@pytest.mark.skipif(
    subprocess.run(["which", "openspec"], capture_output=True, check=False).returncode != 0,
    reason="real openspec CLI not on PATH",
)
def test_init_does_not_auto_commit_pre_existing_dirty_work(tmp_path: Path) -> None:
    """The `SKIPPED_DIRTY` case is a human's own unrelated in-progress work
    that predates `cosmo init` entirely -- folding it into an "init
    bootstrap" commit on their behalf would be a real surprise, not a fix."""
    target = tmp_path / "dirty-repo"
    target.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    (target / "untracked.txt").write_text("someone's own work in progress\n")

    result = runner.invoke(app, ["init", str(target)], input=_IDENTITY_INPUT)

    assert result.exit_code == 0, result.stdout
    assert "init bootstrap output" not in result.stdout
    status = subprocess.run(
        ["git", "-C", str(target), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "untracked.txt" in status.stdout


@pytest.mark.skipif(
    subprocess.run(["which", "openspec"], capture_output=True, check=False).returncode != 0,
    reason="real openspec CLI not on PATH",
)
def test_rerunning_init_reports_skipped_docs_and_refreshes_agent_dir(tmp_path: Path) -> None:
    target = _git_repo(tmp_path)
    runner.invoke(app, ["init", str(target)], input=_IDENTITY_INPUT)
    stale = target / ".agent" / "claude" / "no-longer-in-the-template.txt"
    stale.write_text("stale")

    result = runner.invoke(app, ["init", str(target)], input="n\n")

    assert result.exit_code == 0, result.stdout
    assert "skipped" in result.stdout
    assert "already registered" in result.stdout
    assert not stale.exists()


@pytest.mark.skipif(
    subprocess.run(["which", "openspec"], capture_output=True, check=False).returncode != 0,
    reason="real openspec CLI not on PATH",
)
def test_init_prompts_for_identity_when_none_exists(tmp_path: Path) -> None:
    """No config-default suggestion any more -- a human always types their
    own identity, and it gets synced into `[git]` in the user config too, so
    Cosmo's own automated commits stop defaulting to "Cosmo" as well."""
    target = _git_repo(tmp_path)

    result = runner.invoke(app, ["init", str(target)], input="Jane Dev\njane@example.com\n")

    assert result.exit_code == 0, result.stdout
    assert "No git identity configured" in result.stdout
    assert "cosmo@entropiainversa.com" not in result.stdout
    assert read_configured_identity(target) == GitIdentity(
        name="Jane Dev", email="jane@example.com"
    )
    assert load_config().git.commit_author_name == "Jane Dev"
    assert load_config().git.commit_author_email == "jane@example.com"


@pytest.mark.skipif(
    subprocess.run(["which", "openspec"], capture_output=True, check=False).returncode != 0,
    reason="real openspec CLI not on PATH",
)
def test_init_declining_to_replace_an_existing_identity_leaves_it_untouched(
    tmp_path: Path,
) -> None:
    target = _git_repo(tmp_path)
    runner.invoke(app, ["init", str(target)], input="Jane Dev\njane@example.com\n")

    result = runner.invoke(app, ["init", str(target)], input="n\n")

    assert result.exit_code == 0, result.stdout
    assert read_configured_identity(target) == GitIdentity(
        name="Jane Dev", email="jane@example.com"
    )
    # Declining still syncs the existing repo identity into `[git]` -- so
    # Cosmo's own commits match the human's real identity, not "Cosmo".
    assert load_config().git.commit_author_name == "Jane Dev"
    assert load_config().git.commit_author_email == "jane@example.com"


@pytest.mark.skipif(
    subprocess.run(["which", "openspec"], capture_output=True, check=False).returncode != 0,
    reason="real openspec CLI not on PATH",
)
def test_init_confirming_replaces_an_existing_identity_with_the_prompted_one(
    tmp_path: Path,
) -> None:
    target = _git_repo(tmp_path)
    runner.invoke(app, ["init", str(target)], input="Jane Dev\njane@example.com\n")

    result = runner.invoke(app, ["init", str(target)], input="y\nJohn Dev\njohn@example.com\n")

    assert result.exit_code == 0, result.stdout
    assert read_configured_identity(target) == GitIdentity(
        name="John Dev", email="john@example.com"
    )
    assert load_config().git.commit_author_name == "John Dev"
    assert load_config().git.commit_author_email == "john@example.com"


@pytest.mark.skipif(
    subprocess.run(["which", "openspec"], capture_output=True, check=False).returncode != 0,
    reason="real openspec CLI not on PATH",
)
def test_init_explicit_git_author_flags_skip_the_prompt_entirely(tmp_path: Path) -> None:
    target = _git_repo(tmp_path)

    result = runner.invoke(
        app,
        [
            "init",
            str(target),
            "--git-author-name",
            "CI Bot",
            "--git-author-email",
            "ci@example.com",
        ],
        # No input at all -- if this hit a prompt, CliRunner would error on
        # end-of-input rather than silently succeed.
        input="",
    )

    assert result.exit_code == 0, result.stdout
    assert read_configured_identity(target) == GitIdentity(name="CI Bot", email="ci@example.com")


def test_init_without_target_path_or_interactive_fails_clean() -> None:
    result = runner.invoke(app, ["init"])

    assert result.exit_code == 2
    assert "TARGET_PATH" in result.stderr
    assert "--interactive" in result.stderr


@pytest.mark.skipif(
    subprocess.run(["which", "openspec"], capture_output=True, check=False).returncode != 0,
    reason="real openspec CLI not on PATH",
)
def test_init_interactive_wizard_accepts_every_default(tmp_path: Path) -> None:
    target = _git_repo(tmp_path)
    # harness / template / base branch / base-branch-strategy / force-docs
    # confirm / model-overrides confirm -- one blank line per prompt, each
    # falling back to its own default -- then a mandatory git-identity
    # name/email (no default is offered for that one).
    result = runner.invoke(app, ["init", str(target), "-i"], input="\n\n\n\n\n\n" + _IDENTITY_INPUT)

    assert result.exit_code == 0, result.stdout
    assert "claude" in result.stdout and "-i wizard" in result.stdout
    assert "project template: _blank" in result.stdout
    assert "registered" in result.stdout


@pytest.mark.skipif(
    subprocess.run(["which", "openspec"], capture_output=True, check=False).returncode != 0,
    reason="real openspec CLI not on PATH",
)
def test_init_interactive_skips_prompts_for_values_already_given_as_flags(
    tmp_path: Path,
) -> None:
    target = _git_repo(tmp_path)

    result = runner.invoke(
        app,
        [
            "init",
            str(target),
            "-i",
            "--harness",
            "ori-claude",
            "--project-template",
            "java-spring-react",
        ],
        # base branch / base-branch-strategy / force-docs confirm /
        # model-overrides confirm, then mandatory git-identity name/email.
        input="\n\n\n\n" + _IDENTITY_INPUT,
    )

    assert result.exit_code == 0, result.stdout
    assert "Harness (" not in result.stdout
    assert "Project template (" not in result.stdout
    assert "harness: ori-claude" in result.stdout and "-i wizard" in result.stdout
    assert "project template: java-spring-react" in result.stdout


@pytest.mark.skipif(
    subprocess.run(["which", "openspec"], capture_output=True, check=False).returncode != 0,
    reason="real openspec CLI not on PATH",
)
def test_init_interactive_reprompts_on_an_unknown_harness_name(tmp_path: Path) -> None:
    target = _git_repo(tmp_path)

    result = runner.invoke(
        app,
        ["init", str(target), "-i"],
        input="bogus-harness\nclaude\n\n\n\n\n\n" + _IDENTITY_INPUT,
    )

    assert result.exit_code == 0, result.stdout
    assert "isn't one of" in result.stdout
    assert "harness: claude" in result.stdout


@pytest.mark.skipif(
    subprocess.run(["which", "openspec"], capture_output=True, check=False).returncode != 0,
    reason="real openspec CLI not on PATH",
)
def test_init_interactive_model_overrides_only_write_the_fields_entered(
    tmp_path: Path,
) -> None:
    target = _git_repo(tmp_path)
    # harness/template/base_branch/base-branch-strategy defaults, force-docs
    # declined, model overrides accepted, only propose_model filled in, then
    # mandatory git-identity name/email.
    result = runner.invoke(
        app,
        ["init", str(target), "-i"],
        input="\n\n\n\nn\ny\n\ncustom-propose-model\n\n\n" + _IDENTITY_INPUT,
    )

    assert result.exit_code == 0, result.stdout
    assert "wrote model overrides" in result.stdout

    cfg = load_config()
    override = cfg.harness.overrides["claude"]
    assert override.propose_model == "custom-propose-model"
    assert override.model is None
    assert override.implement_model is None
    assert override.review_model is None


@pytest.mark.skipif(
    subprocess.run(["which", "openspec"], capture_output=True, check=False).returncode != 0,
    reason="real openspec CLI not on PATH",
)
def test_init_interactive_choosing_cosmo_branch_prompts_for_a_branch_name(
    tmp_path: Path,
) -> None:
    target = _git_repo(tmp_path)
    # harness/template/base_branch defaults, base-branch-strategy=cosmo_branch,
    # branch name default, force-docs/model-overrides defaults, then
    # mandatory git-identity name/email.
    result = runner.invoke(
        app,
        ["init", str(target), "-i"],
        input="\n\n\ncosmo_branch\n\n\n\n" + _IDENTITY_INPUT,
    )

    assert result.exit_code == 0, result.stdout
    current_branch = subprocess.run(
        ["git", "-C", str(target), "branch", "--show-current"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert current_branch == "cosmo"

    project = find_project_by_path(_db_path(), str(target.resolve()))
    assert project is not None
    assert project.base_branch_mode == "cosmo_branch"
    assert project.cosmo_branch_name == "cosmo"
