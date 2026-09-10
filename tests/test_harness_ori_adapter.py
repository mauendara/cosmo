"""`OriClaudeAdapter` (v13 plan Phase 3/5).

The real `ori`/`claude` binaries are never invoked here -- `fixtures/
fake_claude.sh` stands in for `ori` too (it just logs argv/env, indifferent
to which binary it's pretending to be), the same "fake the external process,
test the mechanics" stance the native adapter's tests take. The real
invocations are plan Phase 6's validations (V1-V6), run manually against a
scratch repo, never from a unit test.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from cosmo.checks import CheckStatus
from cosmo.config import CosmoConfig, load_config
from cosmo.config.model import HarnessModelOverrides
from cosmo.harness.claude.adapter import ClaudeCodeAdapter
from cosmo.harness.ori.adapter import CREDENTIAL_ENV_VAR, OriClaudeAdapter
from cosmo.proc import ManagedProcess

FAKE_ORI = Path(__file__).resolve().parent / "fixtures" / "fake_claude.sh"
SPAWN_IGNORING_GRANDCHILD = (
    Path(__file__).resolve().parent / "fixtures" / "spawn_ignoring_grandchild.sh"
)
STREAM_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "stream_json"
NO_USER_CONFIG = Path("/nonexistent/config.toml")


def _config(tmp_path: Path) -> CosmoConfig:
    cfg = load_config(config_path=NO_USER_CONFIG)
    paths = cfg.paths.model_copy(
        update={"data_dir": tmp_path, "work_dir": tmp_path / "work", "log_dir": tmp_path / "logs"}
    )
    return cfg.model_copy(update={"paths": paths})


def _adapter(tmp_path: Path, *, run_id: str | None = None) -> OriClaudeAdapter:
    return OriClaudeAdapter(
        _config(tmp_path),
        cwd=tmp_path,
        ori_binary=str(FAKE_ORI),
        claude_binary="true",
        run_id=run_id,
    )


# -- argv shape ---------------------------------------------------------------


def test_argv_is_ori_claude_model_dashdash_then_native_flags(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    argv = adapter._build_argv("hello", "anthropic/claude-sonnet-4.5")  # noqa: SLF001

    assert argv[0] == str(FAKE_ORI)
    assert argv[1] == "claude"
    assert argv[2] == "--model"
    assert argv[3] == "anthropic/claude-sonnet-4.5"
    assert argv[4] == "--"
    assert argv[5:7] == ["-p", "hello"]


def test_argv_has_exactly_one_model_flag_before_the_dashdash(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    argv = adapter._build_argv("hello", "openai/gpt-5")  # noqa: SLF001

    assert argv.count("--model") == 1
    assert argv.index("--model") < argv.index("--")


def test_argv_after_dashdash_carries_the_same_flags_as_the_native_route(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    argv = adapter._build_argv("hello", "openai/gpt-5")  # noqa: SLF001
    after = argv[argv.index("--") + 1 :]

    assert "--output-format" in after
    assert after[after.index("--output-format") + 1] == "stream-json"
    assert "--max-turns" in after
    assert after[after.index("--max-turns") + 1] == str(adapter.config.harness.max_turns)
    assert "--permission-mode" in after
    assert after[after.index("--permission-mode") + 1] == adapter.config.harness.permission_mode
    assert "--setting-sources" in after
    assert after[after.index("--setting-sources") + 1] == "project"
    assert "--allowedTools" in after
    idx = after.index("--allowedTools")
    assert after[idx + 1 : idx + 4] == ["Write", "Edit", "Bash"]
    # Notably absent after `--`: a second `--model` would conflict with
    # Ori's own (v12: Ori consumes --model and never forwards it).
    assert "--model" not in after


def test_argv_never_contains_dangerously_skip_permissions(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    argv = adapter._build_argv("hello", "openai/gpt-5")  # noqa: SLF001

    assert "--dangerously-skip-permissions" not in argv
    assert "bypassPermissions" not in argv


# -- env ------------------------------------------------------------------


def test_env_scrubs_anthropic_api_key_passes_openrouter_key_and_disables_ori_telemetry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-never-reach-the-child")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-real-credential")
    adapter = _adapter(tmp_path)

    env = adapter._build_env("task-1", "openai/gpt-5")  # noqa: SLF001

    assert "ANTHROPIC_API_KEY" not in env
    assert env["OPENROUTER_API_KEY"] == "or-real-credential"
    assert env["ORI_TELEMETRY"] == "0"


def test_env_carries_task_id_and_db_path_for_the_guardrail_hooks(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    env = adapter._build_env("task-42", "openai/gpt-5")  # noqa: SLF001

    assert env["COSMO_TASK_ID"] == "task-42"
    assert env["COSMO_DB_PATH"] == str(adapter.config.paths.db_path)


# -- preflight ----------------------------------------------------------------


def test_preflight_warns_when_openrouter_key_is_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(CREDENTIAL_ENV_VAR, raising=False)
    results = {r.name: r for r in _adapter(tmp_path).preflight()}

    assert results["openrouter credential"].status is CheckStatus.WARN


def test_preflight_ok_when_openrouter_key_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(CREDENTIAL_ENV_VAR, "or-real-credential")
    results = {r.name: r for r in _adapter(tmp_path).preflight()}

    assert results["openrouter credential"].status is CheckStatus.OK


def test_preflight_never_fails_on_a_set_anthropic_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The honest inverse of the native adapter's hard fail (spec 2.3): Ori
    supplies its own ANTHROPIC_API_KEY to the child regardless, so the
    operator's value being set is never a blocker here."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-operator-value")
    results = {r.name: r for r in _adapter(tmp_path).preflight()}

    assert results["subscription billing"].status is CheckStatus.OK


def test_preflight_warns_on_a_zeroed_cost_ceiling(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    assert cfg.cost.max_cost_per_run_usd == 0.0  # shipped default
    adapter = OriClaudeAdapter(cfg, cwd=tmp_path, ori_binary=str(FAKE_ORI), claude_binary="true")

    results = {r.name: r for r in adapter.preflight()}

    assert results["cost ceiling"].status is CheckStatus.WARN


def test_preflight_ok_with_a_real_cost_ceiling(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cost = cfg.cost.model_copy(update={"max_cost_per_run_usd": 5.0})
    cfg = cfg.model_copy(update={"cost": cost})
    adapter = OriClaudeAdapter(cfg, cwd=tmp_path, ori_binary=str(FAKE_ORI), claude_binary="true")

    results = {r.name: r for r in adapter.preflight()}

    assert results["cost ceiling"].status is CheckStatus.OK


def test_preflight_checks_both_ori_and_claude_are_on_path(tmp_path: Path) -> None:
    adapter = OriClaudeAdapter(
        _config(tmp_path), cwd=tmp_path, ori_binary="/no/such/ori", claude_binary="/no/such/claude"
    )

    results = {r.name: r for r in adapter.preflight()}

    assert results["ori cli"].status is CheckStatus.FAIL
    assert results["claude cli"].status is CheckStatus.FAIL


# -- cancel ---------------------------------------------------------------


def _read_grandchild_pid(log_path: Path, *, timeout: float = 2.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if log_path.exists():
            content = log_path.read_text().strip()
            if content:
                return int(content.splitlines()[0])
        time.sleep(0.02)
    raise AssertionError(f"grandchild pid never appeared in {log_path}")


def test_cancel_reaps_the_whole_process_group(tmp_path: Path) -> None:
    """Not inherited from the native adapter's cancel tests (those stub the
    process): drives a real subprocess tree through `OriClaudeAdapter.cancel`
    (inherited, unchanged, from `_ClaudeCodeInvoker`) and proves the whole
    group is gone -- the failure that costs a host if this ever regresses."""
    cfg = _config(tmp_path)
    timeouts = cfg.timeouts.model_copy(update={"kill_grace": 1})  # keep the test fast
    cfg = cfg.model_copy(update={"timeouts": timeouts})
    adapter = OriClaudeAdapter(
        cfg, cwd=tmp_path, ori_binary=str(FAKE_ORI), claude_binary="true", run_id="run-1"
    )
    log_path = tmp_path / "raw.log"
    process = ManagedProcess(
        ["sh", str(SPAWN_IGNORING_GRANDCHILD)], raw_log_path=log_path, cwd=tmp_path
    )
    adapter._running["t1"] = process  # noqa: SLF001
    grandchild_pid = _read_grandchild_pid(log_path)
    os.kill(grandchild_pid, 0)  # confirm alive before claiming it's reaped

    adapter.cancel("t1")

    with pytest.raises(ProcessLookupError):
        os.kill(grandchild_pid, 0)


# -- result mapping ---------------------------------------------------------


def test_result_mapping_uses_the_same_shared_stream_parser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "calls.log"))
    monkeypatch.setenv("FAKE_CLAUDE_STREAM_FILE", str(STREAM_FIXTURES / "normal_run.ndjson"))

    result = _adapter(tmp_path).probe("print hello")

    assert result.success is True
    assert result.session_id == "f4f79cd3-194e-4084-875e-ecf47b933e5f"
    assert result.total_cost_usd == 0.0733296
    assert result.output_summary == "success"


# -- model resolution -------------------------------------------------------


def test_probe_uses_an_explicit_model_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "calls.log"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    adapter = _adapter(tmp_path)

    adapter.probe("hello", model="openai/gpt-5")

    assert "--model openai/gpt-5" in log.read_text()


def test_propose_implement_review_use_per_harness_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "calls.log"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    cfg = _config(tmp_path)
    harness = cfg.harness.model_copy(
        update={
            "overrides": {
                "ori-claude": HarnessModelOverrides(
                    propose_model="openai/gpt-5",
                    implement_model="qwen/qwen3-coder",
                    review_model="google/gemini-2.5-pro",
                )
            }
        }
    )
    cfg = cfg.model_copy(update={"harness": harness})
    adapter = OriClaudeAdapter(cfg, cwd=tmp_path, ori_binary=str(FAKE_ORI), claude_binary="true")

    adapter.propose(Path("openspec/changes/add-foo"), {"task_id": "add-foo"})
    adapter.implement("t1", Path("openspec/changes/add-foo"))
    adapter.review("t1", Path("openspec/changes/add-foo"), "main")

    calls = log.read_text()
    assert "--model openai/gpt-5" in calls
    assert "--model qwen/qwen3-coder" in calls
    assert "--model google/gemini-2.5-pro" in calls


def test_native_adapter_in_the_same_config_is_unaffected_by_ori_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "calls.log"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    cfg = _config(tmp_path)
    harness = cfg.harness.model_copy(
        update={
            "overrides": {
                "ori-claude": HarnessModelOverrides(model="openai/gpt-5"),
            }
        }
    )
    cfg = cfg.model_copy(update={"harness": harness})
    native = ClaudeCodeAdapter(cfg, cwd=tmp_path, binary=str(FAKE_ORI))

    native.probe("hello")

    assert f"--model {cfg.harness.model}" in log.read_text()
    assert "openai/gpt-5" not in log.read_text()
