"""Semantic tests for the Codex-specific apply_patch and Bash guardrails."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

HOOKS = Path(__file__).resolve().parents[1] / "templates" / "harness" / "codex" / "hooks"


def _run_hook(
    name: str,
    tmp_path: Path,
    *,
    tool_name: str,
    tool_input: dict[str, Any],
    role: str = "implement",
) -> dict[str, Any] | None:
    env = dict(os.environ)
    env["COSMO_HARNESS_ROLE"] = role
    result = subprocess.run(
        ["python3", str(HOOKS / name)],
        input=json.dumps({"cwd": str(tmp_path), "tool_name": tool_name, "tool_input": tool_input}),
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )
    return json.loads(result.stdout) if result.stdout else None


def _denied(result: dict[str, Any] | None) -> bool:
    assert result is not None
    output = result["hookSpecificOutput"]
    return bool(output["permissionDecision"] == "deny")


@pytest.mark.parametrize(
    "path",
    ["src/test/java/AppTest.java", "e2e/login.ts", "web/src/App.test.tsx"],
)
def test_test_guard_denies_every_protected_patch_path(tmp_path: Path, path: str) -> None:
    patch = f"*** Begin Patch\n*** Update File: {path}\n@@\n-old\n+new\n*** End Patch"
    assert _denied(
        _run_hook(
            "test_path_guard.py",
            tmp_path,
            tool_name="apply_patch",
            tool_input={"command": patch},
        )
    )


def test_test_guard_denies_shell_redirect_to_protected_path(tmp_path: Path) -> None:
    assert _denied(
        _run_hook(
            "test_path_guard.py",
            tmp_path,
            tool_name="Bash",
            tool_input={"command": "printf rewritten > web/src/App.test.tsx"},
        )
    )


def test_test_guard_allows_source_patch(tmp_path: Path) -> None:
    patch = "*** Begin Patch\n*** Update File: src/app.py\n@@\n-old\n+new\n*** End Patch"
    assert (
        _run_hook(
            "test_path_guard.py",
            tmp_path,
            tool_name="apply_patch",
            tool_input={"command": patch},
        )
        is None
    )


@pytest.mark.parametrize("annotation", ["@Disabled", "test.skip", "describe.skip", "xit("])
def test_annotation_guard_checks_only_added_patch_lines(tmp_path: Path, annotation: str) -> None:
    patch = (
        "*** Begin Patch\n*** Update File: src/app.ts\n@@\n"
        f" context mentioning {annotation}\n+{annotation}\n*** End Patch"
    )
    assert _denied(
        _run_hook(
            "annotation_guard.py",
            tmp_path,
            tool_name="apply_patch",
            tool_input={"command": patch},
        )
    )


@pytest.mark.parametrize(
    "command",
    [
        "git push origin HEAD",
        "git reset --hard HEAD~1",
        "git clean -fdx",
        "git commit --no-verify -m unsafe",
    ],
)
def test_commit_integrity_guard_denies_destructive_git_commands(
    tmp_path: Path, command: str
) -> None:
    assert _denied(
        _run_hook(
            "commit_integrity_guard.py",
            tmp_path,
            tool_name="Bash",
            tool_input={"command": command},
        )
    )


@pytest.mark.parametrize(
    ("tool_name", "tool_input"),
    [
        ("Bash", {"command": "cp -a /repo/.git .git-local-copy"}),
        ("Bash", {"command": "printf broken > /repo/.git/config"}),
        (
            "apply_patch",
            {
                "command": "*** Begin Patch\n*** Update File: .git/config\n"
                "@@\n-old\n+new\n*** End Patch"
            },
        ),
    ],
)
def test_commit_integrity_guard_denies_direct_git_metadata_mutation(
    tmp_path: Path, tool_name: str, tool_input: dict[str, Any]
) -> None:
    assert _denied(
        _run_hook(
            "commit_integrity_guard.py",
            tmp_path,
            tool_name=tool_name,
            tool_input=tool_input,
        )
    )


def test_commit_integrity_guard_allows_normal_verified_commit(tmp_path: Path) -> None:
    assert (
        _run_hook(
            "commit_integrity_guard.py",
            tmp_path,
            tool_name="Bash",
            tool_input={"command": "git add HELLO.md && git commit -m 'add hello'"},
        )
        is None
    )


@pytest.mark.parametrize("command", ["build &", "nohup build", "disown %1"])
def test_background_guard_denies_detached_work(tmp_path: Path, command: str) -> None:
    assert _denied(
        _run_hook(
            "background_task_guard.py",
            tmp_path,
            tool_name="Bash",
            tool_input={"command": command},
        )
    )


def test_background_guard_allows_quoted_ampersand_and_and_operator(tmp_path: Path) -> None:
    assert (
        _run_hook(
            "background_task_guard.py",
            tmp_path,
            tool_name="Bash",
            tool_input={"command": "printf '&' && build"},
        )
        is None
    )


def test_review_guard_allows_only_verdict_patch(tmp_path: Path) -> None:
    verdict = (
        "*** Begin Patch\n*** Add File: .cosmo/review-result.json\n"
        '+{"verdict":"approved"}\n*** End Patch'
    )
    source = "*** Begin Patch\n*** Update File: src/app.py\n@@\n-old\n+new\n*** End Patch"

    assert (
        _run_hook(
            "review_write_guard.py",
            tmp_path,
            tool_name="apply_patch",
            tool_input={"command": verdict},
            role="review",
        )
        is None
    )
    assert _denied(
        _run_hook(
            "review_write_guard.py",
            tmp_path,
            tool_name="apply_patch",
            tool_input={"command": source},
            role="review",
        )
    )


def test_review_guard_allows_readonly_shell_and_denies_source_redirect(tmp_path: Path) -> None:
    assert (
        _run_hook(
            "review_write_guard.py",
            tmp_path,
            tool_name="Bash",
            tool_input={"command": "git diff develop...HEAD"},
            role="review",
        )
        is None
    )
    assert _denied(
        _run_hook(
            "review_write_guard.py",
            tmp_path,
            tool_name="Bash",
            tool_input={"command": "printf bad > src/app.py"},
            role="review",
        )
    )


@pytest.mark.parametrize(
    "command",
    [
        "sed -n '1,240p' .agent/codex/skills/openspec-workflow/SKILL.md",
        "sed -n '1,20p' README.md && sed -n '1,20p' docs/skills.md",
        "if [ -f AGENTS.md ]; then sed -n '1,260p' AGENTS.md; fi\n"
        "openspec status --change review-check\n"
        "git status --short\n"
        "sed -n '1,260p' openspec/changes/review-check/tasks.md\n"
        "find openspec/changes/review-check -maxdepth 3 -type f -print | sort",
    ],
)
def test_review_guard_allows_readonly_sed(tmp_path: Path, command: str) -> None:
    assert (
        _run_hook(
            "review_write_guard.py",
            tmp_path,
            tool_name="Bash",
            tool_input={"command": command},
            role="review",
        )
        is None
    )


@pytest.mark.parametrize(
    "command",
    ["sed -i 's/old/new/' src/app.py", "sed -ni '1p' src/app.py", "perl -ne 'print 1' x"],
)
def test_review_guard_denies_mutating_sed_and_arbitrary_perl(tmp_path: Path, command: str) -> None:
    assert _denied(
        _run_hook(
            "review_write_guard.py",
            tmp_path,
            tool_name="Bash",
            tool_input={"command": command},
            role="review",
        )
    )


@pytest.mark.parametrize(
    ("tool_name", "tool_input"),
    [("Read", {"file_path": ".env.local"}), ("Bash", {"command": "cat secrets/token"})],
)
def test_secret_guard_denies_read_paths(
    tmp_path: Path, tool_name: str, tool_input: dict[str, Any]
) -> None:
    assert _denied(
        _run_hook(
            "secret_read_guard.py",
            tmp_path,
            tool_name=tool_name,
            tool_input=tool_input,
        )
    )


def test_documented_hook_references_resolve_inside_template() -> None:
    config_path = HOOKS.parent / "hooks.json"
    config = json.loads(config_path.read_text())
    for entry in config["hooks"]["PreToolUse"]:
        for hook in entry["hooks"]:
            command_path = hook["command"].removeprefix("python3 ")
            relative = Path(command_path).relative_to(".agent/codex")
            assert (HOOKS.parent / relative).is_file()
