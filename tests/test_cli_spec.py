"""`cosmo spec add` / `cosmo spec queue` (v4 workflow changes, see
`docs/v4-changes-to-workflow-plan.md`): the raw-spec front door. Manually
smoke-tested end to end against the real CLI before this file was written
(see `docs/v3-implementation-state.md`'s v4 section); these pin the same
behavior for regression.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from cosmo.cli.main import app
from cosmo.config import load_config
from cosmo.store import StoreWriter
from cosmo.store.reader import list_tasks

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COSMO_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


def _db_path() -> Path:
    return load_config().paths.db_path


def _register(repo: Path, *, harness: str = "claude") -> None:
    """`spec add`/`spec queue` validate `--repo` against a real
    registration (`cli.main._resolve_project_repo`) -- register it
    directly, matching `cosmo init`'s own `str(path.resolve())` storage
    convention."""
    writer = StoreWriter(_db_path())
    try:
        writer.register_project(target_path=str(repo.resolve()), harness=harness)
    finally:
        writer.close()


def _write_task_file(repo: Path, spec_name: str, filename: str, body: str) -> Path:
    tasks_dir = repo / "docs" / "specs" / f"{spec_name}-spec" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    path = tasks_dir / filename
    path.write_text(body, encoding="utf-8")
    return path


def test_spec_add_on_an_unregistered_repo_fails_loudly_without_touching_the_filesystem(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "target"
    repo.mkdir()

    result = runner.invoke(app, ["spec", "add", "demo", "--repo", str(repo), "--harness", "fake"])

    assert result.exit_code == 1
    assert "not a Cosmo-orchestrated project" in result.stderr.replace("\n", "")
    assert "cosmo init" in result.stderr
    assert not (repo / "docs").exists()


def test_spec_queue_on_an_unregistered_repo_fails_loudly(tmp_path: Path) -> None:
    repo = tmp_path / "target"
    repo.mkdir()

    result = runner.invoke(app, ["spec", "queue", "demo", "--repo", str(repo)])

    assert result.exit_code == 1
    assert "not a Cosmo-orchestrated project" in result.stderr.replace("\n", "")


def test_spec_add_and_queue_default_repo_to_the_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Running from inside a registered target repo needs no `--repo` at
    all -- only invoking from somewhere else does."""
    repo = tmp_path / "target"
    repo.mkdir()
    _register(repo)
    _write_task_file(
        repo, "demo", "backend-task.md", "---\ntask_id: demo-backend\ndepends_on: []\n---\n\nbody\n"
    )
    monkeypatch.chdir(repo)

    result = runner.invoke(app, ["spec", "queue", "demo"])

    assert result.exit_code == 0, result.stdout
    assert {t.task_id for t in list_tasks(_db_path())} == {"demo-backend"}


def test_spec_add_without_a_raw_spec_file_or_from_fails_loudly(tmp_path: Path) -> None:
    repo = tmp_path / "target"
    repo.mkdir()
    _register(repo)
    result = runner.invoke(app, ["spec", "add", "demo", "--repo", str(repo), "--harness", "fake"])
    assert result.exit_code == 1
    assert "does not exist" in result.stderr


def test_spec_add_copies_in_a_raw_spec_via_from_and_reports_when_the_harness_writes_nothing(
    tmp_path: Path,
) -> None:
    """`fake` writes no files at all (`FakeHarnessAdapter.probe` never
    touches the filesystem), so `spec add` should still succeed at the
    copy-in step and then report the "nothing decomposed" case cleanly --
    not crash trying to render an empty preview."""
    repo = tmp_path / "target"
    repo.mkdir()
    _register(repo)
    raw = tmp_path / "raw.md"
    raw.write_text("# Demo\nAdd a health check endpoint.\n", encoding="utf-8")

    result = runner.invoke(
        app,
        ["spec", "add", "demo", "--repo", str(repo), "--from", str(raw), "--harness", "fake"],
    )

    assert result.exit_code == 1
    assert (repo / "docs" / "specs" / "demo-spec.md").is_file()
    assert "no *-task.md files were written" in result.stderr


def test_spec_add_with_existing_task_files_and_declined_confirmation_skips_the_harness(
    tmp_path: Path,
) -> None:
    """Regression: `spec add` used to always re-invoke the harness even when
    `tasks_dir` already had files from a prior run -- real, billed usage for
    a no-op. Declining the confirmation must reuse the existing files
    untouched and never reach harness resolution at all."""
    repo = tmp_path / "target"
    repo.mkdir()
    _register(repo)
    (repo / "docs" / "specs").mkdir(parents=True)
    (repo / "docs" / "specs" / "demo-spec.md").write_text("# Demo\n", encoding="utf-8")
    body = "---\ntask_id: demo-backend\ndepends_on: []\ntitle: Backend\n---\n\nbody\n"
    task_path = _write_task_file(repo, "demo", "backend-task.md", body)

    result = runner.invoke(
        app, ["spec", "add", "demo", "--repo", str(repo), "--harness", "fake"], input="n\n"
    )

    assert result.exit_code == 0, result.stdout
    assert "task file(s) already exist" in result.stdout
    assert "harness not run" in result.stdout
    assert "harness:" not in result.stdout
    assert task_path.read_text(encoding="utf-8") == body


def test_spec_add_with_existing_task_files_and_confirmed_reruns_the_harness(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "target"
    repo.mkdir()
    _register(repo)
    (repo / "docs" / "specs").mkdir(parents=True)
    (repo / "docs" / "specs" / "demo-spec.md").write_text("# Demo\n", encoding="utf-8")
    _write_task_file(
        repo, "demo", "backend-task.md", "---\ntask_id: demo-backend\ndepends_on: []\n---\n\nb\n"
    )

    result = runner.invoke(
        app, ["spec", "add", "demo", "--repo", str(repo), "--harness", "fake"], input="y\n"
    )

    assert result.exit_code == 0, result.stdout
    assert "task file(s) already exist" in result.stdout
    assert "harness:" in result.stdout


def test_spec_queue_inserts_one_task_per_file_with_the_right_batch_id(tmp_path: Path) -> None:
    repo = tmp_path / "target"
    repo.mkdir()
    _register(repo)
    _write_task_file(
        repo,
        "demo",
        "backend-task.md",
        "---\ntask_id: demo-backend\ndepends_on: []\npriority: 1\ntitle: Backend\n---\n\nbody\n",
    )
    _write_task_file(
        repo,
        "demo",
        "frontend-task.md",
        "---\ntask_id: demo-frontend\ndepends_on: [demo-backend]\ntitle: Frontend\n---\n\nbody\n",
    )

    result = runner.invoke(app, ["spec", "queue", "demo", "--repo", str(repo)])

    assert result.exit_code == 0, result.stdout
    assert "queued 2 task(s) from demo-spec" in result.stdout

    tasks = {t.task_id: t for t in list_tasks(_db_path())}
    assert set(tasks) == {"demo-backend", "demo-frontend"}
    assert tasks["demo-frontend"].depends_on == ["demo-backend"]
    assert tasks["demo-backend"].spec_batch_id == "demo-spec"
    assert tasks["demo-frontend"].spec_batch_id == "demo-spec"
    assert tasks["demo-backend"].priority == 1
    assert tasks["demo-frontend"].priority == 0
    assert tasks["demo-backend"].allow_test_edits is False
    assert tasks["demo-frontend"].allow_test_edits is False


def test_spec_queue_threads_allow_test_edits_through_from_frontmatter(tmp_path: Path) -> None:
    """Found live: `cosmo spec queue` used to insert every task with
    `allow_test_edits=False` unconditionally, no matter what the *-task.md
    frontmatter said -- there was no field for it to say anything at all.
    A task whose deliverable lived entirely under a guardrailed `e2e/` path
    then had every write denied, submitted an empty implementation, and was
    rejected by review three times running before a human traced it back to
    the missing flag."""
    repo = tmp_path / "target"
    repo.mkdir()
    _register(repo)
    _write_task_file(
        repo,
        "demo",
        "e2e-task.md",
        "---\ntask_id: demo-e2e\nallow_test_edits: true\ntitle: E2E\n---\n\nbody\n",
    )

    result = runner.invoke(app, ["spec", "queue", "demo", "--repo", str(repo)])

    assert result.exit_code == 0, result.stdout
    tasks = {t.task_id: t for t in list_tasks(_db_path())}
    assert tasks["demo-e2e"].allow_test_edits is True


def test_spec_queue_with_no_task_files_fails_loudly(tmp_path: Path) -> None:
    repo = tmp_path / "target"
    repo.mkdir()
    _register(repo)
    result = runner.invoke(app, ["spec", "queue", "demo", "--repo", str(repo)])
    assert result.exit_code == 1
    assert "no *-task.md files found" in result.stderr


def test_spec_queue_rejects_a_cycle_atomically_before_inserting_anything(tmp_path: Path) -> None:
    repo = tmp_path / "target"
    repo.mkdir()
    _register(repo)
    _write_task_file(
        repo, "cyclic", "a-task.md", "---\ntask_id: cyc-a\ndepends_on: [cyc-b]\n---\n\na\n"
    )
    _write_task_file(
        repo, "cyclic", "b-task.md", "---\ntask_id: cyc-b\ndepends_on: [cyc-a]\n---\n\nb\n"
    )

    result = runner.invoke(app, ["spec", "queue", "cyclic", "--repo", str(repo)])

    assert result.exit_code == 1
    assert "depends_on cycle" in result.stderr
    assert list_tasks(_db_path()) == []


def test_spec_queue_rejects_a_malformed_task_file(tmp_path: Path) -> None:
    repo = tmp_path / "target"
    repo.mkdir()
    _register(repo)
    _write_task_file(repo, "bad", "only-task.md", "no frontmatter here at all\n")

    result = runner.invoke(app, ["spec", "queue", "bad", "--repo", str(repo)])

    assert result.exit_code == 1
    assert "missing YAML frontmatter" in result.stderr


def test_spec_queue_on_a_duplicate_task_id_fails_loudly(tmp_path: Path) -> None:
    repo = tmp_path / "target"
    repo.mkdir()
    _register(repo)
    runner.invoke(
        app, ["queue", "add", "openspec/changes/demo-backend", "--task-id", "demo-backend"]
    )
    _write_task_file(
        repo, "demo", "backend-task.md", "---\ntask_id: demo-backend\ndepends_on: []\n---\n\nbody\n"
    )

    result = runner.invoke(app, ["spec", "queue", "demo", "--repo", str(repo)])

    assert result.exit_code == 1
    assert "already queued" in result.stderr
