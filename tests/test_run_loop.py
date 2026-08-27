"""`run.loop.run_queue` against `FakeHarnessAdapter` + `FakeGate` over a real
git repo (plan Phase 8 exit criteria): a multi-task DAG executed in
dependency order, the circuit breaker tripping the run to `PAUSED` on
repeated distinct-task `environment_error`, the 5-hour quota pause
auto-resuming within the same invocation, a weekly cap beyond the run's
remaining wall-clock budget stopping rather than idling, a per-task cost
ceiling blocking one task while the queue continues, and the run-level wall
clock requeuing an in-flight task and stopping with `max_time`. Mirrors
`test_task_machine.py`'s own fixture style -- this is Phase 7's own
per-task test file's natural sibling, one level up."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from cosmo.config import CosmoConfig, load_config
from cosmo.config.model import CostConfig
from cosmo.events import EventEmitter
from cosmo.gate.fake import FakeGate, ScriptedGateResult
from cosmo.gate.types import GateResult
from cosmo.harness.fake import FakeHarnessAdapter, FakeOutcome, ScriptedCall
from cosmo.run.loop import run_queue
from cosmo.store import StoreWriter
from cosmo.store.enums import RunStatus, StopReason
from cosmo.store.reader import get_task, list_events

NO_USER_CONFIG = Path("/nonexistent/config.toml")


def _fast_config(tmp_path: Path, **overrides: object) -> CosmoConfig:
    cfg = load_config(config_path=NO_USER_CONFIG)
    paths = cfg.paths.model_copy(
        update={
            "data_dir": tmp_path / "data",
            "work_dir": tmp_path / "work",
            "log_dir": tmp_path / "logs",
        }
    )
    retries = cfg.retries.model_copy(update={"delay_min": 0, "delay_max": 0})
    # Phase 9's pre-run disk check (`run.loop.run_queue`) is real, not
    # injectable -- it calls `shutil.disk_usage` against wherever `tmp_path`
    # actually lives. This host's own `/tmp` is a small tmpfs close to the
    # spec default 10 GB floor (see docs/handoff.md's known environment
    # noise), so every test here would otherwise trip `disk_low` depending
    # on how much else is running concurrently. Tests isolate from real
    # environment state (see CLAUDE conventions); the disk check's own
    # mechanics get a dedicated real test instead (test_run_disk_check.py).
    disk = cfg.disk.model_copy(update={"min_free_gb": 0.001})
    # v4 workflow changes: see test_task_machine.py's own _fast_config comment
    # -- REVIEWING defaults on and FakeHarnessAdapter's reused SUCCESS script
    # writes no verdict file, so it's disabled here for the same reason.
    review = cfg.review.model_copy(update={"enabled": False})
    updates: dict[str, object] = {
        "paths": paths,
        "retries": retries,
        "disk": disk,
        "review": review,
    }
    updates.update(overrides)
    return cfg.model_copy(update=updates)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )


def _repo_on_develop(tmp_path: Path) -> Path:
    repo = tmp_path / "target-repo"
    repo.mkdir()
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "init", "-q")
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", "README.md")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "base")
    _git(repo, "branch", "-M", "develop")
    return repo


def _gate_runner(gate: FakeGate) -> Callable[..., GateResult]:
    def _run(*, task_id: str, **_kwargs: object) -> GateResult:
        return gate.validate(task_id)

    return _run


class _FakeClock:
    """A monotonic clock that advances by `step` on every call -- lets a
    test force the run-level wall clock to expire deterministically,
    without a real sleep."""

    def __init__(self, *, start: float = 0.0, step: float = 1.0) -> None:
        self._t = start
        self._step = step

    def __call__(self) -> float:
        self._t += self._step
        return self._t


def test_multi_task_dag_executes_in_dependency_order_to_completion(tmp_path: Path) -> None:
    cfg = _fast_config(tmp_path)
    repo = _repo_on_develop(tmp_path)
    writer = StoreWriter(cfg.paths.db_path)
    # Inserted out of dependency order on purpose: "b" depends on "a" but
    # is queued first, so a correct result proves the DAG constraint wins
    # over insertion/priority order.
    writer.queue_add(task_id="b", spec_path="openspec/changes/b", depends_on=["a"], max_attempts=2)
    writer.queue_add(task_id="a", spec_path="openspec/changes/a", max_attempts=2)
    emitter = EventEmitter(writer)
    adapter = FakeHarnessAdapter(cfg, script=ScriptedCall(outcome=FakeOutcome.SUCCESS))
    gate = FakeGate(ScriptedGateResult(passed=True))

    try:
        outcome = run_queue(
            config=cfg,
            writer=writer,
            emitter=emitter,
            adapter=adapter,
            repo_path=repo,
            base_branch="develop",
            harness_name="claude",
            gate_runner=_gate_runner(gate),
        )
    finally:
        writer.close()

    assert outcome.status is RunStatus.STOPPED
    assert outcome.stop_reason is StopReason.QUEUE_EMPTY
    assert outcome.execution_order == ["a", "b"]
    assert outcome.summary.completed == 2

    for task_id in ("a", "b"):
        row = get_task(cfg.paths.db_path, task_id)
        assert row is not None
        assert row.status == "done"


def test_breaker_trips_on_repeated_distinct_task_environment_error(tmp_path: Path) -> None:
    cfg = _fast_config(tmp_path)  # default consecutive_blocked_threshold=3
    repo = _repo_on_develop(tmp_path)
    writer = StoreWriter(cfg.paths.db_path)
    for task_id in ("t1", "t2", "t3", "t4"):
        writer.queue_add(task_id=task_id, spec_path=f"openspec/changes/{task_id}", max_attempts=2)
    emitter = EventEmitter(writer)
    # Every propose()/implement() call fails -- PROPOSING's own bounded
    # local retry (spec 3.3: "retry once, then BLOCKED") blocks each task
    # with blocked_reason=environment after 2 attempts, never reaching the
    # gate at all.
    adapter = FakeHarnessAdapter(cfg, script=ScriptedCall(outcome=FakeOutcome.ENVIRONMENT_FAILURE))
    gate = FakeGate(ScriptedGateResult(passed=True))

    try:
        outcome = run_queue(
            config=cfg,
            writer=writer,
            emitter=emitter,
            adapter=adapter,
            repo_path=repo,
            base_branch="develop",
            harness_name="claude",
            gate_runner=_gate_runner(gate),
        )
    finally:
        writer.close()

    assert outcome.status is RunStatus.PAUSED
    assert len(outcome.execution_order) == 3  # the breaker stopped the run before t4 ever started
    assert outcome.summary.blocked_by_reason.get("environment") == 3

    run_row_events = list_events(cfg.paths.db_path, run_id=outcome.run_id, limit=1000)
    assert any(e.event_type == "run.paused" for e in run_row_events)

    remaining = get_task(cfg.paths.db_path, "t4")
    assert remaining is not None
    assert remaining.status == "queued"  # never attempted


def test_quota_five_hour_pause_auto_resumes_within_the_same_run(tmp_path: Path) -> None:
    cfg = _fast_config(tmp_path)
    repo = _repo_on_develop(tmp_path)
    writer = StoreWriter(cfg.paths.db_path)
    writer.queue_add(task_id="solo", spec_path="openspec/changes/solo", max_attempts=2)
    emitter = EventEmitter(writer)
    adapter = FakeHarnessAdapter(
        cfg,
        script=[
            ScriptedCall(outcome=FakeOutcome.SUCCESS),  # propose
            ScriptedCall(outcome=FakeOutcome.RATE_LIMIT),  # implement #1 -- fails, quota signal
            ScriptedCall(outcome=FakeOutcome.SUCCESS),  # propose (after resume)
            ScriptedCall(outcome=FakeOutcome.SUCCESS),  # implement #2 -- succeeds
        ],
    )
    gate = FakeGate(ScriptedGateResult(passed=True))
    sleeps: list[float] = []

    try:
        outcome = run_queue(
            config=cfg,
            writer=writer,
            emitter=emitter,
            adapter=adapter,
            repo_path=repo,
            base_branch="develop",
            harness_name="claude",
            gate_runner=_gate_runner(gate),
            sleep=sleeps.append,
        )
    finally:
        writer.close()

    assert outcome.status is RunStatus.STOPPED
    assert outcome.stop_reason is StopReason.QUEUE_EMPTY
    assert outcome.summary.completed == 1
    assert outcome.summary.retried == 1  # the RATE_LIMIT-caused failed_retry, before the requeue
    assert len(sleeps) == 1  # exactly one pause/resume cycle, never a real sleep

    events = [
        e.event_type for e in list_events(cfg.paths.db_path, run_id=outcome.run_id, limit=1000)
    ]
    assert "run.paused" in events
    assert "run.resumed" in events


def test_quota_weekly_beyond_run_budget_stops_rather_than_idling(tmp_path: Path) -> None:
    cfg = _fast_config(tmp_path)  # default timeouts.run_wall (10h) is far short of the reset below
    repo = _repo_on_develop(tmp_path)
    writer = StoreWriter(cfg.paths.db_path)
    writer.queue_add(task_id="solo", spec_path="openspec/changes/solo", max_attempts=2)
    emitter = EventEmitter(writer)
    adapter = FakeHarnessAdapter(
        cfg,
        script=[
            ScriptedCall(outcome=FakeOutcome.SUCCESS),
            ScriptedCall(
                outcome=FakeOutcome.RATE_LIMIT,
                quota_window="weekly",
                quota_resets_at="2099-01-01T00:00:00+00:00",
            ),
        ],
    )
    gate = FakeGate(ScriptedGateResult(passed=True))

    def _never_sleep(_seconds: float) -> None:
        raise AssertionError("a weekly cap beyond budget must stop, never idle the process")

    try:
        outcome = run_queue(
            config=cfg,
            writer=writer,
            emitter=emitter,
            adapter=adapter,
            repo_path=repo,
            base_branch="develop",
            harness_name="claude",
            gate_runner=_gate_runner(gate),
            sleep=_never_sleep,
        )
    finally:
        writer.close()

    assert outcome.status is RunStatus.STOPPED
    assert outcome.stop_reason is StopReason.QUOTA_EXHAUSTED_WEEKLY


def test_per_task_cost_ceiling_blocks_one_task_and_the_queue_continues(tmp_path: Path) -> None:
    cfg = _fast_config(
        tmp_path,
        cost=CostConfig(max_cost_per_run_usd=0.0, max_cost_per_task_usd=1.0, warn_at_fraction=0.8),
    )
    repo = _repo_on_develop(tmp_path)
    writer = StoreWriter(cfg.paths.db_path)
    writer.queue_add(task_id="expensive", spec_path="openspec/changes/expensive", max_attempts=2)
    writer.queue_add(task_id="cheap", spec_path="openspec/changes/cheap", max_attempts=2)
    emitter = EventEmitter(writer)
    adapter = FakeHarnessAdapter(
        cfg,
        script=[
            # "expensive": propose succeeds but already reports more than
            # the $1 task ceiling -- IMPLEMENTING is never even attempted.
            ScriptedCall(outcome=FakeOutcome.SUCCESS, total_cost_usd=2.0),
            ScriptedCall(outcome=FakeOutcome.SUCCESS),  # "cheap": propose
            ScriptedCall(outcome=FakeOutcome.SUCCESS),  # "cheap": implement
        ],
    )
    gate = FakeGate(ScriptedGateResult(passed=True))

    try:
        outcome = run_queue(
            config=cfg,
            writer=writer,
            emitter=emitter,
            adapter=adapter,
            repo_path=repo,
            base_branch="develop",
            harness_name="claude",
            gate_runner=_gate_runner(gate),
        )
    finally:
        writer.close()

    assert outcome.status is RunStatus.STOPPED
    assert outcome.stop_reason is StopReason.QUEUE_EMPTY
    assert outcome.summary.completed == 1
    assert outcome.summary.blocked_by_reason.get("cost") == 1

    expensive = get_task(cfg.paths.db_path, "expensive")
    assert expensive is not None
    assert expensive.status == "blocked"
    assert expensive.blocked_reason == "cost"

    cheap = get_task(cfg.paths.db_path, "cheap")
    assert cheap is not None
    assert cheap.status == "done"


def test_queue_empty_reports_queued_tasks_stalled_on_an_unmet_dependency(tmp_path: Path) -> None:
    """A task can be `queued` yet permanently unschedulable -- its
    `depends_on` names a task that will never reach `done` (here, one
    `blocked` by the per-task cost ceiling). `QUEUE_EMPTY` alone doesn't
    distinguish that from a genuinely empty queue; `stalled_queued_tasks`
    is what lets a caller tell the two apart without a separate `queue ls`."""
    cfg = _fast_config(
        tmp_path,
        cost=CostConfig(max_cost_per_run_usd=0.0, max_cost_per_task_usd=1.0, warn_at_fraction=0.8),
    )
    repo = _repo_on_develop(tmp_path)
    writer = StoreWriter(cfg.paths.db_path)
    writer.queue_add(task_id="expensive", spec_path="openspec/changes/expensive", max_attempts=2)
    writer.queue_add(
        task_id="downstream",
        spec_path="openspec/changes/downstream",
        depends_on=["expensive"],
        max_attempts=2,
    )
    emitter = EventEmitter(writer)
    adapter = FakeHarnessAdapter(
        cfg,
        # "expensive" is blocked on cost before "downstream" is ever
        # attempted -- it stays `queued`, stuck behind a dependency that
        # will never become `done`.
        script=ScriptedCall(outcome=FakeOutcome.SUCCESS, total_cost_usd=2.0),
    )
    gate = FakeGate(ScriptedGateResult(passed=True))

    try:
        outcome = run_queue(
            config=cfg,
            writer=writer,
            emitter=emitter,
            adapter=adapter,
            repo_path=repo,
            base_branch="develop",
            harness_name="claude",
            gate_runner=_gate_runner(gate),
        )
    finally:
        writer.close()

    assert outcome.status is RunStatus.STOPPED
    assert outcome.stop_reason is StopReason.QUEUE_EMPTY
    assert outcome.summary.completed == 0
    assert outcome.summary.stalled_queued_tasks == ["downstream"]

    downstream = get_task(cfg.paths.db_path, "downstream")
    assert downstream is not None
    assert downstream.status == "queued"


def test_run_wall_clock_expiry_requeues_the_in_flight_task_and_stops(tmp_path: Path) -> None:
    short_wall_timeouts = load_config(config_path=NO_USER_CONFIG).timeouts.model_copy(
        update={"run_wall": 3}
    )
    cfg = _fast_config(tmp_path, timeouts=short_wall_timeouts)
    repo = _repo_on_develop(tmp_path)
    writer = StoreWriter(cfg.paths.db_path)
    writer.queue_add(task_id="solo", spec_path="openspec/changes/solo", max_attempts=2)
    emitter = EventEmitter(writer)
    # implement() is never reached -- the wall clock expires before the
    # first IMPLEMENTING attempt even starts (see the clock's step below).
    adapter = FakeHarnessAdapter(cfg, script=ScriptedCall(outcome=FakeOutcome.SUCCESS))
    gate = FakeGate(ScriptedGateResult(passed=True))
    clock = _FakeClock(start=0.0, step=1.0)

    try:
        outcome = run_queue(
            config=cfg,
            writer=writer,
            emitter=emitter,
            adapter=adapter,
            repo_path=repo,
            base_branch="develop",
            harness_name="claude",
            gate_runner=_gate_runner(gate),
            monotonic=clock,
        )
    finally:
        writer.close()

    assert outcome.status is RunStatus.STOPPED
    assert outcome.stop_reason is StopReason.MAX_TIME
    assert outcome.summary.requeued == 1
    assert outcome.summary.completed == 0

    solo = get_task(cfg.paths.db_path, "solo")
    assert solo is not None
    assert solo.status == "queued"  # returned to QUEUED, not blocked


def test_a_task_blocked_in_one_run_and_retried_in_a_later_run_gets_a_fresh_worktree(
    tmp_path: Path,
) -> None:
    # Regression: worktree reuse (the wall-clock/quota requeue case above)
    # must only apply *within* the run that created it. A task blocked in
    # run 1, then `queue retry`'d and driven by a brand new `cosmo run`
    # (run 2), still carries run 1's `worktree_path` in the DB -- reusing
    # that path blindly would either point at a stale/removed directory or,
    # worse, silently resurrect run 1's leftover state under run 2.
    cfg = _fast_config(tmp_path)
    repo = _repo_on_develop(tmp_path)
    writer = StoreWriter(cfg.paths.db_path)
    writer.queue_add(task_id="solo", spec_path="openspec/changes/solo", max_attempts=2)
    emitter = EventEmitter(writer)
    gate = FakeGate(ScriptedGateResult(passed=True))

    failing_adapter = FakeHarnessAdapter(
        cfg, script=ScriptedCall(outcome=FakeOutcome.ENVIRONMENT_FAILURE)
    )
    try:
        first_outcome = run_queue(
            config=cfg,
            writer=writer,
            emitter=emitter,
            adapter=failing_adapter,
            repo_path=repo,
            base_branch="develop",
            harness_name="claude",
            gate_runner=_gate_runner(gate),
        )
    finally:
        writer.close()

    assert first_outcome.status is RunStatus.STOPPED  # breaker threshold not hit by one task
    blocked = get_task(cfg.paths.db_path, "solo")
    assert blocked is not None
    assert blocked.status == "blocked"
    first_run_worktree = blocked.worktree_path
    assert first_run_worktree is not None
    assert first_run_worktree.split("/")[-2] == first_outcome.run_id

    writer = StoreWriter(cfg.paths.db_path)
    try:
        writer.queue_retry("solo")
    finally:
        writer.close()

    succeeding_adapter = FakeHarnessAdapter(cfg, script=ScriptedCall(outcome=FakeOutcome.SUCCESS))
    writer = StoreWriter(cfg.paths.db_path)
    try:
        emitter = EventEmitter(writer)
        second_outcome = run_queue(
            config=cfg,
            writer=writer,
            emitter=emitter,
            adapter=succeeding_adapter,
            repo_path=repo,
            base_branch="develop",
            harness_name="claude",
            gate_runner=_gate_runner(gate),
        )
    finally:
        writer.close()

    assert second_outcome.status is RunStatus.STOPPED
    assert second_outcome.stop_reason is StopReason.QUEUE_EMPTY
    assert second_outcome.summary.completed == 1
    assert second_outcome.run_id != first_outcome.run_id

    done = get_task(cfg.paths.db_path, "solo")
    assert done is not None
    assert done.status == "done"


def test_run_queue_sweeps_a_stale_worktree_left_by_a_crashed_prior_process(
    tmp_path: Path,
) -> None:
    """Spec 3.2's startup sweep (`git.worktree.sweep_stale_worktrees`), now
    wired into `run_queue` itself -- a worktree directory sitting under
    `work_dir` with no matching `BLOCKED` task (e.g. left by a process that
    crashed mid-attempt, or a systemd restart) gets pruned before this run's
    own first task even starts. A worktree belonging to a currently
    `BLOCKED` task is retained, same as `sweep_stale_worktrees` always did
    -- this test only wires the call, it doesn't re-test the sweep's own
    retain/prune logic (see test_git_worktree.py for that)."""
    cfg = _fast_config(tmp_path)
    repo = _repo_on_develop(tmp_path)

    # A directory shaped like a leftover worktree from an earlier,
    # unrelated run -- no task in the queue references it at all.
    stale = cfg.paths.work_dir / "dead-run" / "orphan-task"
    stale.mkdir(parents=True)
    (stale / "leftover.txt").write_text("crash residue\n")

    writer = StoreWriter(cfg.paths.db_path)
    writer.queue_add(task_id="a", spec_path="openspec/changes/a", max_attempts=2)
    emitter = EventEmitter(writer)
    adapter = FakeHarnessAdapter(cfg, script=ScriptedCall(outcome=FakeOutcome.SUCCESS))
    gate = FakeGate(ScriptedGateResult(passed=True))

    try:
        run_queue(
            config=cfg,
            writer=writer,
            emitter=emitter,
            adapter=adapter,
            repo_path=repo,
            base_branch="develop",
            harness_name="claude",
            gate_runner=_gate_runner(gate),
        )
    finally:
        writer.close()

    assert not stale.exists()
