"""`ClaudeOpenRouterAdapter` -- direct OpenRouter routing, no `ori` involved.

The real `claude` binary is never invoked here -- `fixtures/fake_claude.sh`
stands in for it (it just logs argv/env), the same "fake the external
process, test the mechanics" stance the other two claude-binary adapters'
tests take.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cosmo.checks import CheckStatus
from cosmo.config import CosmoConfig, load_config
from cosmo.harness.claude_openrouter.adapter import (
    ANTHROPIC_BASE_URL_VALUE,
    CREDENTIAL_ENV_VAR,
    ClaudeOpenRouterAdapter,
)

FAKE_CLAUDE = Path(__file__).resolve().parent / "fixtures" / "fake_claude.sh"
NO_USER_CONFIG = Path("/nonexistent/config.toml")


def _config(tmp_path: Path) -> CosmoConfig:
    cfg = load_config(config_path=NO_USER_CONFIG)
    paths = cfg.paths.model_copy(
        update={"data_dir": tmp_path, "work_dir": tmp_path / "work", "log_dir": tmp_path / "logs"}
    )
    return cfg.model_copy(update={"paths": paths})


def _adapter(tmp_path: Path, *, run_id: str | None = None) -> ClaudeOpenRouterAdapter:
    return ClaudeOpenRouterAdapter(
        _config(tmp_path), cwd=tmp_path, binary=str(FAKE_CLAUDE), run_id=run_id
    )


# -- argv shape ---------------------------------------------------------------


def test_argv_is_claude_p_prompt_native_flags_then_settings(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    argv = adapter._build_argv("hello", "z-ai/glm-4.6")  # noqa: SLF001

    assert argv[0] == str(FAKE_CLAUDE)
    assert argv[1] == "-p"
    assert argv[2] == "hello"
    # No wrapper wraps this route -- unlike ori-claude, there is no `--`
    # boundary and no separate `--model` flag: model selection happens in
    # the environment (ANTHROPIC_MODEL), asserted in the env tests below.
    assert "--" not in argv
    assert "--model" not in argv


def test_argv_keeps_setting_sources_project(tmp_path: Path) -> None:
    """Same as ori-claude and the native route: guardrail hooks stay wired
    through --setting-sources project. This adapter's real advantage over
    ori-claude is elsewhere -- it doesn't trigger ori's thinking.display
    bug, which affects every non-Anthropic model regardless of this flag
    (see harness/ori/adapter.py's module docstring)."""
    adapter = _adapter(tmp_path)
    argv = adapter._build_argv("hello", "z-ai/glm-4.6")  # noqa: SLF001

    assert "--setting-sources" in argv
    assert argv[argv.index("--setting-sources") + 1] == "project"


def test_argv_carries_native_flags(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    argv = adapter._build_argv("hello", "z-ai/glm-4.6")  # noqa: SLF001

    assert "--output-format" in argv
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--max-turns" in argv
    assert argv[argv.index("--max-turns") + 1] == str(adapter.config.harness.max_turns)
    assert "--permission-mode" in argv
    assert argv[argv.index("--permission-mode") + 1] == adapter.config.harness.permission_mode
    assert "--allowedTools" in argv
    idx = argv.index("--allowedTools")
    assert argv[idx + 1 : idx + 4] == ["Write", "Edit", "Bash"]


def test_argv_carries_the_apikey_helper_settings_json(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    argv = adapter._build_argv("hello", "z-ai/glm-4.6")  # noqa: SLF001

    assert "--settings" in argv
    settings_json = argv[argv.index("--settings") + 1]
    assert "apiKeyHelper" in settings_json
    assert "OPENROUTER_API_KEY" in settings_json


def test_argv_never_contains_dangerously_skip_permissions(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    argv = adapter._build_argv("hello", "z-ai/glm-4.6")  # noqa: SLF001

    assert "--dangerously-skip-permissions" not in argv
    assert "bypassPermissions" not in argv


# -- env ------------------------------------------------------------------


def test_env_sets_base_url_and_model_scrubs_anthropic_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-never-reach-the-child")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-real-credential")
    adapter = _adapter(tmp_path)

    env = adapter._build_env("task-1", "z-ai/glm-4.6")  # noqa: SLF001

    assert "ANTHROPIC_API_KEY" not in env
    assert env["ANTHROPIC_BASE_URL"] == ANTHROPIC_BASE_URL_VALUE
    assert env["ANTHROPIC_MODEL"] == "z-ai/glm-4.6"
    # Not resolved by this adapter itself -- the apiKeyHelper script reads it
    # from the child's own environment at call time; passing os.environ
    # through unmodified is what makes that work.
    assert env["OPENROUTER_API_KEY"] == "or-real-credential"


def test_env_carries_task_id_and_db_path_for_the_guardrail_hooks(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    env = adapter._build_env("task-42", "z-ai/glm-4.6")  # noqa: SLF001

    assert env["COSMO_TASK_ID"] == "task-42"
    assert env["COSMO_DB_PATH"] == str(adapter.config.paths.db_path)


# -- preflight ----------------------------------------------------------------


def test_preflight_fails_when_openrouter_key_is_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unlike ori-claude's warn: this route has no fallback credential
    store, so a missing key is a hard failure, not a maybe."""
    monkeypatch.delenv(CREDENTIAL_ENV_VAR, raising=False)
    results = {r.name: r for r in _adapter(tmp_path).preflight()}

    assert results["openrouter credential"].status is CheckStatus.FAIL


def test_preflight_ok_when_openrouter_key_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(CREDENTIAL_ENV_VAR, "or-real-credential")
    results = {r.name: r for r in _adapter(tmp_path).preflight()}

    assert results["openrouter credential"].status is CheckStatus.OK


def test_preflight_warns_on_a_zeroed_cost_ceiling(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    assert cfg.cost.max_cost_per_run_usd == 0.0  # shipped default
    adapter = ClaudeOpenRouterAdapter(cfg, cwd=tmp_path, binary=str(FAKE_CLAUDE))

    results = {r.name: r for r in adapter.preflight()}

    assert results["cost ceiling"].status is CheckStatus.WARN


def test_preflight_ok_with_a_real_cost_ceiling(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cost = cfg.cost.model_copy(update={"max_cost_per_run_usd": 5.0})
    cfg = cfg.model_copy(update={"cost": cost})
    adapter = ClaudeOpenRouterAdapter(cfg, cwd=tmp_path, binary=str(FAKE_CLAUDE))

    results = {r.name: r for r in adapter.preflight()}

    assert results["cost ceiling"].status is CheckStatus.OK


def test_preflight_checks_claude_is_on_path(tmp_path: Path) -> None:
    adapter = ClaudeOpenRouterAdapter(_config(tmp_path), cwd=tmp_path, binary="/no/such/claude")

    results = {r.name: r for r in adapter.preflight()}

    assert results["claude cli"].status is CheckStatus.FAIL


# -- registry / capabilities ---------------------------------------------------


def test_name_is_claude_openrouter(tmp_path: Path) -> None:
    assert ClaudeOpenRouterAdapter.name == "claude-openrouter"


def test_supports_gating_is_true() -> None:
    assert ClaudeOpenRouterAdapter.capabilities.supports_gating is True
