"""One real task driven through `task.machine.run_task` against the real
Docker validation gate (plan Phase 7's integration exit criterion) --
`FakeHarnessAdapter` stands in for the harness so this doesn't spend real
Claude quota, but `VALIDATING`/`MERGING` call the real `run_validation_gate`
(the default `gate_runner`, not injected), against `tests/fixtures/gate_repo`
(Phase 6's fixture, reused rather than adding a second one).

Since `FakeHarnessAdapter.implement()` makes no real commits, `IMPLEMENTING`
leaves the task branch identical to `develop` -- the gate runs against the
fixture's own known-good baseline (the same "green run" scenario
`test_gate_fixture_e2e.py` already covers at the gate layer), so this test's
job is narrower and complementary: proving `run_task`'s own wiring to the
*real* `gate.validate_task`/`run_validation_gate`/`git.merge.merge_task`
chain works end to end, not re-verifying the gate's mechanics themselves.

**Opt-in, not on-by-default** -- same posture and same env var as
`test_gate_fixture_e2e.py`: a real gate run through Maven/npm/Playwright
containers takes minutes even warm.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from cosmo.config import CosmoConfig, load_config
from cosmo.events import EventEmitter
from cosmo.git.worktree import create_worktree
from cosmo.harness.fake import FakeHarnessAdapter, FakeOutcome, ScriptedCall
from cosmo.store import StoreWriter
from cosmo.store.enums import TaskStatus
from cosmo.store.reader import get_task
from cosmo.task.machine import run_task
from cosmo.task.types import TaskContext

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "gate_repo"
AUTHOR = ("Cosmo Test", "cosmo-test@example.com")

pytestmark = pytest.mark.skipif(
    shutil.which("docker") is None or os.environ.get("COSMO_GATE_DOCKER_E2E") != "1",
    reason="real gate run against real docker -- opt in with COSMO_GATE_DOCKER_E2E=1",
)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            f"user.name={AUTHOR[0]}",
            "-c",
            f"user.email={AUTHOR[1]}",
            *args,
        ],
        capture_output=True,
        text=True,
        check=True,
    )


def _fixture_repo_on_develop(tmp_path: Path) -> Path:
    repo = tmp_path / "gate-repo"
    shutil.copytree(FIXTURE_REPO, repo)
    _git(repo, "init", "-q", "-b", "develop")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial fixture")
    return repo


def _fast_config(tmp_path: Path) -> CosmoConfig:
    cfg = load_config(config_path=Path("/nonexistent/config.toml"))
    paths = cfg.paths.model_copy(
        update={
            "data_dir": tmp_path / "data",
            "work_dir": tmp_path / "work",
            "log_dir": tmp_path / "logs",
        }
    )
    git = cfg.git.model_copy(
        update={"commit_author_name": AUTHOR[0], "commit_author_email": AUTHOR[1]}
    )
    # v4 workflow changes: see test_task_machine.py's own _fast_config comment
    # -- this real-Docker E2E fixture predates REVIEWING and has no reviewer
    # harness call scripted, so it's disabled here rather than made to fail.
    review = cfg.review.model_copy(update={"enabled": False})
    return cfg.model_copy(update={"paths": paths, "git": git, "review": review})


def test_a_real_task_reaches_done_against_the_real_gate(tmp_path: Path) -> None:
    cfg = _fast_config(tmp_path)
    repo = _fixture_repo_on_develop(tmp_path)
    task_id = "task-e2e"

    writer = StoreWriter(cfg.paths.db_path)
    try:
        writer.queue_add(task_id=task_id, spec_path="openspec/changes/noop", max_attempts=2)
        emitter = EventEmitter(writer)
        info = create_worktree(
            repo_path=repo,
            work_dir=cfg.paths.work_dir,
            run_id="run-1",
            task_id=task_id,
            spec_id=task_id,
            base_branch="develop",
            harness="claude",
            writer=writer,
            emitter=emitter,
        )
        ctx = TaskContext(
            task_id=task_id,
            spec_path="openspec/changes/noop",
            worktree_path=info.path,
            branch=info.branch,
            base_branch="develop",
            allow_test_edits=False,
            max_attempts=2,
        )
        adapter = FakeHarnessAdapter(cfg, cwd=info.path, script=ScriptedCall(FakeOutcome.SUCCESS))

        status = run_task(
            ctx=ctx, config=cfg, writer=writer, emitter=emitter, adapter=adapter, repo_path=repo
        )

        assert status is TaskStatus.DONE
        task = get_task(cfg.paths.db_path, task_id)
        assert task is not None
        assert task.status == "done"
    finally:
        writer.close()
