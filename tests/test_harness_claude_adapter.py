"""`ClaudeCodeAdapter` (spec 2.1-2.3, plan Phase 3).

The real `claude` binary is never invoked here -- `fixtures/fake_claude.sh`
stands in for it, the same "fake the external process, test the mechanics"
stance Phase 2 took with `docker`. The one real invocation is the
`cosmo harness probe` integration exit criterion, run manually/by CI outside
this test module.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cosmo.config import CosmoConfig, load_config
from cosmo.events import EventEmitter
from cosmo.harness.claude.adapter import ClaudeCodeAdapter
from cosmo.proc.orphans import SweepResult
from cosmo.store import StoreWriter

FAKE_CLAUDE = Path(__file__).resolve().parent / "fixtures" / "fake_claude.sh"
STREAM_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "stream_json"
NO_USER_CONFIG = Path("/nonexistent/config.toml")


def _config(tmp_path: Path) -> CosmoConfig:
    cfg = load_config(config_path=NO_USER_CONFIG)
    paths = cfg.paths.model_copy(
        update={"data_dir": tmp_path, "work_dir": tmp_path / "work", "log_dir": tmp_path / "logs"}
    )
    return cfg.model_copy(update={"paths": paths})


def _adapter(tmp_path: Path) -> ClaudeCodeAdapter:
    return ClaudeCodeAdapter(_config(tmp_path), cwd=tmp_path, binary=str(FAKE_CLAUDE))


class _StubProcess:
    """Mirrors `tests/test_proc_reap.py`'s stub -- a process double is the
    established way to test cancel routing without real subprocess timing."""

    def __init__(self, *, cancel_result: bool = True) -> None:
        self._cancel_result = cancel_result
        self.cancel_calls: list[float] = []

    def cancel(self, *, grace_s: float) -> bool:
        self.cancel_calls.append(grace_s)
        return self._cancel_result


def test_argv_never_contains_dangerously_skip_permissions(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    argv = adapter._build_argv("hello")  # noqa: SLF001 -- exactly what this test pins

    assert "--dangerously-skip-permissions" not in argv
    assert "bypassPermissions" not in argv


def test_argv_carries_max_turns_and_permission_mode_from_config(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    argv = adapter._build_argv("hello")  # noqa: SLF001

    assert "--max-turns" in argv
    assert argv[argv.index("--max-turns") + 1] == str(adapter.config.harness.max_turns)
    assert "--permission-mode" in argv
    assert argv[argv.index("--permission-mode") + 1] == adapter.config.harness.permission_mode
    assert "--output-format" in argv
    assert argv[argv.index("--output-format") + 1] == "stream-json"


def test_argv_carries_model_from_config(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    argv = adapter._build_argv("hello")  # noqa: SLF001

    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == adapter.config.harness.model


def test_propose_prompt_pins_the_change_name_to_spec_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Found live: `_do_finishing`'s `openspec archive` and `_do_proposing`'s
    own reused-worktree check both assume the change a propose session
    creates is named `spec_id` (`Path(spec_path).stem`) -- but nothing ever
    told the propose session that. Left to its own judgment it picked a
    different, reasonable-looking name (stripping a task file's `-task`
    suffix), and the archive step failed non-fatally on every single task
    (`Change 'scaffold-app-task' not found. Available changes: scaffold-app`).
    The prompt must name the exact required change id, not just describe the
    change's location."""
    adapter = _adapter(tmp_path)
    captured: dict[str, object] = {}

    def _fake_invoke(
        self: ClaudeCodeAdapter, *, task_id: str, prompt: str, on_activity: object
    ) -> None:
        captured["task_id"] = task_id
        captured["prompt"] = prompt
        return None

    monkeypatch.setattr(ClaudeCodeAdapter, "_invoke", _fake_invoke)

    adapter.propose(
        Path("docs/specs/habit-tracker-spec/tasks/scaffold-app-task.md"),
        {"task_id": "habit-tracker-scaffold-app", "spec_id": "scaffold-app"},
    )

    assert "openspec new change scaffold-app" in str(captured["prompt"])
    assert "openspec new change scaffold-app-task" not in str(captured["prompt"])


def test_propose_prompt_falls_back_to_spec_path_stem_without_spec_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller that doesn't thread `spec_id` through (a raw `queue add`
    task, not a spec-batch one) still gets a deterministic, pinned name --
    the same fallback `task_id` already uses one line above."""
    adapter = _adapter(tmp_path)
    captured: dict[str, object] = {}

    def _fake_invoke(
        self: ClaudeCodeAdapter, *, task_id: str, prompt: str, on_activity: object
    ) -> None:
        captured["prompt"] = prompt
        return None

    monkeypatch.setattr(ClaudeCodeAdapter, "_invoke", _fake_invoke)

    adapter.propose(Path("openspec/changes/add-foo"), {"task_id": "add-foo"})

    assert "openspec new change add-foo" in str(captured["prompt"])


def test_argv_restricts_setting_sources_to_project_only(tmp_path: Path) -> None:
    """Regression pin for the Phase 3 finding: a headless run must not
    inherit the operator's global ~/.claude hooks/plugins. Verified against
    the real CLI by hand (Phase 4 state doc) -- this only pins the flag's
    presence in the constructed argv."""
    adapter = _adapter(tmp_path)
    argv = adapter._build_argv("hello")  # noqa: SLF001

    assert "--setting-sources" in argv
    assert argv[argv.index("--setting-sources") + 1] == "project"


def test_argv_carries_allowed_tools_regardless_of_settings_json(tmp_path: Path) -> None:
    """Regression pin for a real bug found in `cosmo spec add`: on a
    directory that has never been through Claude Code's interactive
    workspace-trust dialog -- true of every headless worktree, created fresh
    per task -- the CLI silently ignores `permissions.allow` from
    `.claude/settings.json` entirely and denies Write/Edit/Bash outright,
    with no error the adapter can see in the stream-json output. Verified by
    a real invocation both ways: settings.json's allow list alone denied
    every Write/Bash call in a fresh directory; the same list passed via
    `--allowedTools` executed normally, unaffected by workspace trust. This
    only pins the flag's presence in the constructed argv."""
    adapter = _adapter(tmp_path)
    argv = adapter._build_argv("hello")  # noqa: SLF001

    assert "--allowedTools" in argv
    idx = argv.index("--allowedTools")
    assert argv[idx + 1 : idx + 4] == ["Write", "Edit", "Bash"]


def test_env_carries_task_id_and_db_path_for_the_guardrail_hooks(tmp_path: Path) -> None:
    """The test-path guard hook (templates/harness/claude/hooks/
    test_path_guard.py) is a separate OS process with no other way to ask
    Cosmo's state -- it reads these two env vars to look up
    task_queue.allow_test_edits read-only."""
    adapter = _adapter(tmp_path)
    env = adapter._build_env("task-42")  # noqa: SLF001

    assert env["COSMO_TASK_ID"] == "task-42"
    assert env["COSMO_DB_PATH"] == str(adapter.config.paths.db_path)


def test_env_scrubs_anthropic_api_key_even_when_set_in_the_parent_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-never-reach-the-child")
    log = tmp_path / "calls.log"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))

    result = _adapter(tmp_path).probe("hello")

    assert result.exit_code == 0
    assert "ANTHROPIC_API_KEY_WAS_SET" not in log.read_text()


@pytest.mark.parametrize(("exit_code", "expected_success"), [(0, True), (1, False), (17, False)])
def test_branches_on_zero_vs_nonzero_exit_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exit_code: int, expected_success: bool
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "calls.log"))
    monkeypatch.setenv("FAKE_CLAUDE_EXIT_CODE", str(exit_code))

    result = _adapter(tmp_path).probe("hello")

    assert result.success is expected_success
    assert result.exit_code == exit_code


def test_probe_parses_the_terminal_result_from_the_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "calls.log"))
    monkeypatch.setenv("FAKE_CLAUDE_STREAM_FILE", str(STREAM_FIXTURES / "normal_run.ndjson"))

    result = _adapter(tmp_path).probe("print hello")

    assert result.success is True
    assert result.session_id == "f4f79cd3-194e-4084-875e-ecf47b933e5f"
    assert result.total_cost_usd == 0.0733296
    assert result.output_summary == "success"
    assert result.raw_log_path is not None and result.raw_log_path.is_file()
    assert result.files_changed == []


def test_a_failed_run_has_no_terminal_result_but_still_reports_the_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "calls.log"))
    monkeypatch.setenv("FAKE_CLAUDE_EXIT_CODE", "1")

    result = _adapter(tmp_path).probe("hello")

    assert result.success is False
    assert result.total_cost_usd is None
    assert result.session_id is None
    assert result.output_summary == "exit code 1"


def test_running_process_is_tracked_and_untracked_around_a_call(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    assert adapter._running == {}  # noqa: SLF001

    adapter.probe("hello")

    assert adapter._running == {}  # noqa: SLF001 -- cleared once _invoke's finally runs


def test_cancel_on_an_untracked_task_is_a_no_op(tmp_path: Path) -> None:
    _adapter(tmp_path).cancel("no-such-task")  # must not raise


def test_cancel_without_an_emitter_falls_back_to_a_bare_process_cancel(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    stub = _StubProcess(cancel_result=True)
    adapter._running["t1"] = stub  # type: ignore[assignment]  # noqa: SLF001

    adapter.cancel("t1")

    assert stub.cancel_calls == [adapter.config.timeouts.kill_grace]


def test_cancel_with_an_emitter_routes_through_cancel_and_reap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "cosmo.proc.reap.sweep",
        lambda run_id, task_id, worktree_path, **kw: SweepResult(
            removed_containers=[], worktree_holder_pids=[]
        ),
    )
    cfg = _config(tmp_path)
    writer = StoreWriter(cfg.paths.db_path)
    emitter = EventEmitter(writer)
    adapter = ClaudeCodeAdapter(
        cfg, cwd=tmp_path, binary=str(FAKE_CLAUDE), run_id="run-1", emitter=emitter
    )
    stub = _StubProcess(cancel_result=True)
    adapter._running["t1"] = stub  # type: ignore[assignment]  # noqa: SLF001

    adapter.cancel("t1")

    assert stub.cancel_calls == [cfg.timeouts.kill_grace]
    row = writer.connection.execute("SELECT COUNT(*) AS n FROM events").fetchone()
    assert row["n"] == 0  # a clean reap emits nothing, same as test_proc_reap.py
    writer.close()
