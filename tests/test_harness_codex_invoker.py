"""Codex invocation mechanics, exercised only through a fake executable."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from cosmo.config import CosmoConfig, load_config
from cosmo.harness.codex.invoker import CodexInvoker, stderr_log_path

FIXTURES = Path(__file__).resolve().parent / "fixtures"
JSONL_FIXTURES = FIXTURES / "codex_jsonl"
FAKE_CODEX = FIXTURES / "fake_codex.sh"
NO_USER_CONFIG = Path("/nonexistent/config.toml")


def _config(tmp_path: Path) -> CosmoConfig:
    config = load_config(config_path=NO_USER_CONFIG)
    paths = config.paths.model_copy(
        update={"data_dir": tmp_path, "work_dir": tmp_path / "work", "log_dir": tmp_path / "logs"}
    )
    return config.model_copy(update={"paths": paths})


def _invoker(tmp_path: Path) -> CodexInvoker:
    return CodexInvoker(_config(tmp_path), cwd=tmp_path, binary=str(FAKE_CODEX))


def test_argv_is_deterministic_and_fail_closed(tmp_path: Path) -> None:
    invoker = _invoker(tmp_path)
    argv = invoker.build_argv("do the work", "gpt-test")

    assert argv[:10] == [
        str(FAKE_CODEX),
        "exec",
        "--json",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--strict-config",
        "--sandbox",
        "workspace-write",
        "--dangerously-bypass-hook-trust",
    ]
    assert argv[-1] == "do the work"
    assert argv[argv.index("--model") + 1] == "gpt-test"
    assert 'approval_policy="never"' in argv
    assert 'web_search="disabled"' in argv
    assert "apps._default.enabled=false" in argv
    for feature in (
        "apps",
        "plugins",
        "multi_agent",
        "browser_use",
        "computer_use",
        "image_generation",
        "skill_mcp_dependency_install",
    ):
        assert ["--disable", feature] == argv[argv.index(feature) - 1 : argv.index(feature) + 1]
    assert "--dangerously-bypass-approvals-and-sandbox" not in argv
    assert "danger-full-access" not in argv


def test_argv_injects_only_absolute_audited_hook_commands(tmp_path: Path) -> None:
    argv = _invoker(tmp_path).build_argv("hello", "gpt-test")
    hooks = next(value for value in argv if value.startswith("hooks.PreToolUse="))

    assert str(tmp_path / ".agent" / "codex" / "hooks") in hooks
    assert "test_path_guard.py" in hooks
    assert "annotation_guard.py" in hooks
    assert "commit_integrity_guard.py" in hooks
    assert "background_task_guard.py" in hooks
    assert "review_write_guard.py" in hooks
    assert "secret_read_guard.py" in hooks
    assert "$(" not in hooks


def test_environment_preserves_auth_home_but_scrubs_api_billing_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", "/saved/auth/home")
    monkeypatch.setenv("CODEX_API_KEY", "must-not-reach-child")

    env = _invoker(tmp_path).build_env("task-1", "review")

    assert env["CODEX_HOME"] == "/saved/auth/home"
    assert "CODEX_API_KEY" not in env
    assert env["COSMO_TASK_ID"] == "task-1"
    assert env["COSMO_HARNESS_ROLE"] == "review"
    assert env["COSMO_DB_PATH"] == str(_config(tmp_path).paths.db_path)


def test_exact_environment_reaches_the_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call_log = tmp_path / "call.log"
    monkeypatch.setenv("FAKE_CODEX_LOG", str(call_log))
    monkeypatch.setenv("CODEX_API_KEY", "must-not-reach-child")

    _invoker(tmp_path).invoke(task_id="task-1", prompt="hello", model="gpt-test", role="review")

    logged = call_log.read_text()
    assert "task:task-1" in logged
    assert "role:review" in logged
    assert f"db:{_config(tmp_path).paths.db_path}" in logged
    assert "CODEX_API_KEY_WAS_SET" not in logged


@pytest.mark.parametrize(("exit_code", "expected"), [(0, True), (1, False), (19, False)])
def test_success_is_exactly_exit_code_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exit_code: int,
    expected: bool,
) -> None:
    monkeypatch.setenv("FAKE_CODEX_LOG", str(tmp_path / "call.log"))
    monkeypatch.setenv("FAKE_CODEX_EXIT_CODE", str(exit_code))
    monkeypatch.setenv("FAKE_CODEX_STREAM_FILE", str(JSONL_FIXTURES / "success.ndjson"))

    result = _invoker(tmp_path).invoke(
        task_id="task-1", prompt="hello", model="gpt-test", role="probe"
    )

    assert result.success is expected
    assert result.exit_code == exit_code
    assert result.output_summary == "turn completed"
    assert result.total_cost_usd is None


def test_failed_file_change_and_success_sounding_prose_do_not_override_exit_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CODEX_LOG", str(tmp_path / "call.log"))
    monkeypatch.setenv("FAKE_CODEX_STREAM_FILE", str(JSONL_FIXTURES / "file_change_failed.ndjson"))

    result = _invoker(tmp_path).invoke(
        task_id="task-1", prompt="hello", model="gpt-test", role="implement"
    )

    assert result.success is True
    assert result.output_summary == "turn completed"
    assert result.tool_call_count == 1
    assert result.files_changed == []


def test_failure_summary_comes_from_structured_terminal_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CODEX_LOG", str(tmp_path / "call.log"))
    monkeypatch.setenv("FAKE_CODEX_EXIT_CODE", "1")
    monkeypatch.setenv("FAKE_CODEX_STREAM_FILE", str(JSONL_FIXTURES / "api_failure.ndjson"))

    result = _invoker(tmp_path).invoke(
        task_id="task-1", prompt="hello", model="gpt-test", role="probe"
    )

    assert result.output_summary.startswith("turn failed: The requested model")


def test_malformed_output_is_tolerated_and_exit_code_remains_authoritative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CODEX_LOG", str(tmp_path / "call.log"))
    monkeypatch.setenv("FAKE_CODEX_STREAM_FILE", str(JSONL_FIXTURES / "truncated.ndjson"))

    result = _invoker(tmp_path).invoke(
        task_id="task-1", prompt="hello", model="gpt-test", role="probe"
    )

    assert result.success is True
    assert result.output_summary == "exit code 0"
    assert result.tool_call_count == 1


def test_stdout_jsonl_and_stderr_are_retained_separately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CODEX_LOG", str(tmp_path / "call.log"))
    monkeypatch.setenv("FAKE_CODEX_STREAM_FILE", str(JSONL_FIXTURES / "hook_denial.ndjson"))
    monkeypatch.setenv("FAKE_CODEX_STDERR_FILE", str(JSONL_FIXTURES / "hook_denial.stderr"))

    result = _invoker(tmp_path).invoke(
        task_id="task-1", prompt="hello", model="gpt-test", role="implement"
    )

    assert result.raw_log_path is not None
    assert result.raw_log_path.read_bytes() == (JSONL_FIXTURES / "hook_denial.ndjson").read_bytes()
    assert (
        stderr_log_path(result.raw_log_path).read_bytes()
        == (JSONL_FIXTURES / "hook_denial.stderr").read_bytes()
    )


def test_activity_is_relayed_from_structured_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stream = tmp_path / "command.ndjson"
    stream.write_text(
        '{"type":"thread.started","thread_id":"thread-1"}\n'
        '{"type":"item.started","item":{"id":"i1","type":"command_execution",'
        '"command":"git status","status":"in_progress"}}\n'
        '{"type":"item.completed","item":{"id":"i1","type":"command_execution",'
        '"command":"git status","status":"completed"}}\n'
        '{"type":"turn.completed","usage":{}}\n'
    )
    monkeypatch.setenv("FAKE_CODEX_LOG", str(tmp_path / "call.log"))
    monkeypatch.setenv("FAKE_CODEX_STREAM_FILE", str(stream))
    activity: list[str] = []

    result = _invoker(tmp_path).invoke(
        task_id="task-1",
        prompt="hello",
        model="gpt-test",
        role="implement",
        on_activity=activity.append,
    )

    assert activity == [
        "session started",
        "command_execution: git status",
        "command_execution: git status",
    ]
    assert result.session_id == "thread-1"
    assert result.tool_call_count == 1


def test_cancel_kills_a_sigterm_ignoring_descendant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "survived"
    monkeypatch.setenv("FAKE_CODEX_LOG", str(tmp_path / "call.log"))
    monkeypatch.setenv("FAKE_CODEX_SURVIVAL_MARKER", str(marker))
    config = _config(tmp_path)
    config = config.model_copy(
        update={"timeouts": config.timeouts.model_copy(update={"kill_grace": 1})}
    )
    invoker = CodexInvoker(config, cwd=tmp_path, binary=str(FAKE_CODEX))
    finished: list[bool] = []

    thread = threading.Thread(
        target=lambda: finished.append(
            invoker.invoke(
                task_id="task-1", prompt="hang", model="gpt-test", role="implement"
            ).success
        )
    )
    thread.start()
    deadline = time.monotonic() + 3
    while "task-1" not in invoker._running and time.monotonic() < deadline:  # noqa: SLF001
        time.sleep(0.01)
    invoker.cancel("task-1")
    thread.join(timeout=5)
    time.sleep(0.2)

    assert not thread.is_alive()
    assert finished == [False]
    assert not marker.exists()
