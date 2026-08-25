"""`templates/harness/claude/hooks/annotation_guard.py` (spec 2.5, 6.1)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

HOOK = (
    Path(__file__).resolve().parents[1]
    / "templates"
    / "harness"
    / "claude"
    / "hooks"
    / "annotation_guard.py"
)


def _run(payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
        timeout=5,
        check=False,
    )


def _deny_reason(result: subprocess.CompletedProcess[str]) -> str | None:
    if not result.stdout.strip():
        return None
    decision = json.loads(result.stdout)
    return str(decision["hookSpecificOutput"]["permissionDecisionReason"])


def test_edit_introducing_disabled_annotation_is_denied() -> None:
    result = _run(
        {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "/repo/src/main/AppTest.java",
                "old_string": "@Test\npublic void foo() {}",
                "new_string": "@Disabled\n@Test\npublic void foo() {}",
            },
        }
    )
    reason = _deny_reason(result)
    assert reason is not None
    assert "@Disabled" in reason


def test_edit_that_already_had_the_annotation_and_leaves_count_unchanged_is_allowed() -> None:
    result = _run(
        {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "/repo/src/main/AppTest.java",
                "old_string": "@Disabled\npublic void foo() {}",
                "new_string": "@Disabled\npublic void bar() {}",
            },
        }
    )
    assert result.stdout.strip() == ""


def test_edit_unrelated_to_annotations_is_allowed() -> None:
    result = _run(
        {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "/repo/src/main/App.java",
                "old_string": "int x = 1;",
                "new_string": "int x = 2;",
            },
        }
    )
    assert result.stdout.strip() == ""


def test_write_of_a_new_file_containing_it_skip_is_denied() -> None:
    result = _run(
        {
            "tool_name": "Write",
            "tool_input": {
                "file_path": "/nonexistent/brand-new-file.spec.ts",
                "content": "it.skip('does a thing', () => {})",
            },
        }
    )
    reason = _deny_reason(result)
    assert reason is not None
    assert "it.skip" in reason


def test_xit_call_is_denied_but_the_substring_exit_is_not() -> None:
    denied = _run(
        {
            "tool_name": "Write",
            "tool_input": {"file_path": "/nonexistent/f.spec.ts", "content": "xit('x', () => {})"},
        }
    )
    assert _deny_reason(denied) is not None

    allowed = _run(
        {
            "tool_name": "Write",
            "tool_input": {"file_path": "/nonexistent/f.ts", "content": "process.exit(0)"},
        }
    )
    assert allowed.stdout.strip() == ""


def test_write_overwriting_an_existing_file_that_already_had_the_annotation_is_allowed(
    tmp_path: Path,
) -> None:
    existing = tmp_path / "AppTest.java"
    existing.write_text("@Disabled\npublic class AppTest {}")

    result = _run(
        {
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(existing),
                "content": "@Disabled\npublic class AppTest { /* reformatted */ }",
            },
        }
    )
    assert result.stdout.strip() == ""


def test_unrelated_tool_is_ignored() -> None:
    result = _run({"tool_name": "Read", "tool_input": {"file_path": "x"}})
    assert result.stdout.strip() == ""
