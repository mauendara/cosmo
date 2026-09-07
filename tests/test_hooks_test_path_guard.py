"""`templates/harness/claude/hooks/test_path_guard.py` (spec 2.5, 6.1 layer 1).

Runs the shipped hook script itself as a subprocess -- these scripts travel
to an arbitrary target repo and are invoked directly by `python3`, with no
`cosmo` package importable, so testing anything less than the real file
would not actually prove the guard works.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from cosmo.config import load_config
from cosmo.store import StoreWriter

HOOK = (
    Path(__file__).resolve().parents[1]
    / "templates"
    / "harness"
    / "claude"
    / "hooks"
    / "test_path_guard.py"
)
NO_USER_CONFIG = Path("/nonexistent/config.toml")


def _run(
    payload: dict[str, object], env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    full_env = {"PATH": "/usr/bin:/bin"}
    if env:
        full_env.update(env)
    return subprocess.run(
        ["python3", str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=full_env,
        timeout=5,
        check=False,
    )


def _db_with_task(tmp_path: Path, task_id: str, *, allow_test_edits: bool) -> Path:
    cfg = load_config(config_path=NO_USER_CONFIG)
    db_path = tmp_path / "cosmo.db"
    paths = cfg.paths.model_copy(update={"data_dir": tmp_path})
    cfg = cfg.model_copy(update={"paths": paths})
    writer = StoreWriter(db_path)
    writer.queue_add(
        task_id=task_id,
        spec_path="openspec/changes/x",
        max_attempts=2,
        allow_test_edits=allow_test_edits,
    )
    writer.close()
    return db_path


def test_edit_outside_any_protected_path_is_allowed() -> None:
    result = _run(
        {
            "tool_name": "Edit",
            "cwd": "/repo",
            "tool_input": {"file_path": "/repo/src/main/App.java"},
        }
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_edit_under_src_test_is_denied_by_default() -> None:
    result = _run(
        {
            "tool_name": "Edit",
            "cwd": "/repo",
            "tool_input": {"file_path": "/repo/src/test/java/AppTest.java"},
        }
    )
    assert result.returncode == 0
    decision = json.loads(result.stdout)
    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_write_matching_spec_ts_anywhere_is_denied() -> None:
    # G4: a bare `content: "x"` Write to a path that doesn't exist on disk
    # now structurally qualifies as "a brand-new test file with nothing
    # suspicious in it" (see test_write_of_a_brand_new_test_file_below) --
    # this test needs a genuinely denyable reason (a skip annotation) to
    # keep testing "protected path, denied by default" meaningfully.
    result = _run(
        {
            "tool_name": "Write",
            "cwd": "/repo",
            "tool_input": {
                "file_path": "/repo/frontend/widgets/widget.spec.ts",
                "content": "it.skip('x', () => {});",
            },
        }
    )
    decision = json.loads(result.stdout)
    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_write_matching_test_tsx_anywhere_is_denied() -> None:
    result = _run(
        {
            "tool_name": "Write",
            "cwd": "/repo",
            "tool_input": {
                "file_path": "/repo/src/components/Widget.test.tsx",
                "content": "it.skip('x', () => {});",
            },
        }
    )
    decision = json.loads(result.stdout)
    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_e2e_path_is_denied() -> None:
    result = _run(
        {
            "tool_name": "Write",
            "cwd": "/repo",
            "tool_input": {"file_path": "/repo/e2e/login.ts", "content": "it.skip('x', () => {});"},
        }
    )
    decision = json.loads(result.stdout)
    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_allow_test_edits_true_on_the_task_row_bypasses_the_guard(tmp_path: Path) -> None:
    db_path = _db_with_task(tmp_path, "t1", allow_test_edits=True)
    result = _run(
        {
            "tool_name": "Edit",
            "cwd": "/repo",
            "tool_input": {"file_path": "/repo/src/test/java/AppTest.java"},
        },
        env={"COSMO_DB_PATH": str(db_path), "COSMO_TASK_ID": "t1"},
    )
    assert result.stdout.strip() == ""


def test_allow_test_edits_false_on_the_task_row_still_denies(tmp_path: Path) -> None:
    db_path = _db_with_task(tmp_path, "t1", allow_test_edits=False)
    result = _run(
        {
            "tool_name": "Edit",
            "cwd": "/repo",
            "tool_input": {"file_path": "/repo/src/test/java/AppTest.java"},
        },
        env={"COSMO_DB_PATH": str(db_path), "COSMO_TASK_ID": "t1"},
    )
    decision = json.loads(result.stdout)
    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_missing_db_env_vars_fail_closed_and_still_deny() -> None:
    """No COSMO_DB_PATH/COSMO_TASK_ID at all (e.g. a manual claude -p run
    outside Cosmo's own loop) must fail closed, not silently allow."""
    result = _run(
        {
            "tool_name": "Edit",
            "cwd": "/repo",
            "tool_input": {"file_path": "/repo/src/test/java/AppTest.java"},
        }
    )
    decision = json.loads(result.stdout)
    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_unrelated_tool_is_ignored() -> None:
    result = _run({"tool_name": "Read", "cwd": "/repo", "tool_input": {"file_path": "x"}})
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_write_of_a_brand_new_test_file_with_no_skip_annotation_is_allowed(
    tmp_path: Path,
) -> None:
    """G4 (docs/v15-fixes-after-wa-chat-run.md): a Write to a path that
    doesn't exist yet is a brand-new test file -- exactly what a
    well-behaved agent is expected to produce for new work (mirrors
    `gate.diffgate`'s own "doesn't flag a newly added test file" carve-out)
    -- structurally allowed even without `allow_test_edits`."""
    target = tmp_path / "e2e" / "login.ts"
    result = _run(
        {
            "tool_name": "Write",
            "cwd": str(tmp_path),
            "tool_input": {"file_path": str(target), "content": "test('logs in', () => {});"},
        }
    )
    assert result.stdout.strip() == ""


def test_write_of_a_new_test_file_with_a_skip_annotation_is_still_denied(
    tmp_path: Path,
) -> None:
    """G4: a brand-new test file is only allowed if it isn't born already
    disabled."""
    target = tmp_path / "e2e" / "login.ts"
    result = _run(
        {
            "tool_name": "Write",
            "cwd": str(tmp_path),
            "tool_input": {"file_path": str(target), "content": "test.skip('logs in', () => {});"},
        }
    )
    decision = json.loads(result.stdout)
    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_write_that_drops_most_of_an_existing_test_file_is_denied(tmp_path: Path) -> None:
    """G4: a `Write` overwriting an *existing* protected file with
    drastically less content -- the LOC-drop check, same idea as
    `gate.diffgate`'s `diff_gate_loc_drop_threshold`."""
    target = tmp_path / "e2e" / "login.ts"
    target.parent.mkdir(parents=True)
    target.write_text("\n".join(f"test('case {i}', () => {{}});" for i in range(30)) + "\n")

    result = _run(
        {
            "tool_name": "Write",
            "cwd": str(tmp_path),
            "tool_input": {"file_path": str(target), "content": "test('case 0', () => {});\n"},
        }
    )
    decision = json.loads(result.stdout)
    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_edit_that_does_not_weaken_a_test_is_allowed() -> None:
    """G4: an `Edit` whose replaced region doesn't reduce the assertion
    count and doesn't introduce a skip annotation -- e.g. a genuine
    prop-signature fix -- is structurally allowed without needing
    `allow_test_edits` set in advance. Real shape: `wa-chat-chat-shell`'s
    blocked session, which needed exactly this kind of edit and had no way
    to get it approved mid-task."""
    result = _run(
        {
            "tool_name": "Edit",
            "cwd": "/repo",
            "tool_input": {
                "file_path": "/repo/src/test/java/AppTest.java",
                "old_string": "render(<Chat />);",
                "new_string": "render(<Chat contact={fakeContact} />);",
            },
        }
    )
    assert result.stdout.strip() == ""


def test_edit_that_decreases_assertions_is_denied() -> None:
    result = _run(
        {
            "tool_name": "Edit",
            "cwd": "/repo",
            "tool_input": {
                "file_path": "/repo/src/test/java/AppTest.java",
                "old_string": "expect(a).toBe(1);\nexpect(b).toBe(2);",
                "new_string": "expect(a).toBe(1);",
            },
        }
    )
    decision = json.loads(result.stdout)
    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_edit_that_introduces_a_skip_annotation_is_denied() -> None:
    result = _run(
        {
            "tool_name": "Edit",
            "cwd": "/repo",
            "tool_input": {
                "file_path": "/repo/src/test/java/AppTest.java",
                "old_string": "it('works', () => { expect(a).toBe(1); });",
                "new_string": "it.skip('works', () => { expect(a).toBe(1); });",
            },
        }
    )
    decision = json.loads(result.stdout)
    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_notebook_edit_has_no_structural_allow_list(tmp_path: Path) -> None:
    """G4: `NotebookEdit` is excluded from the structural allow-list (a
    notebook's JSON structure makes line-level assertion-counting
    unreliable) -- `allow_test_edits` set in advance remains the only way
    to edit a protected notebook."""
    result = _run(
        {
            "tool_name": "NotebookEdit",
            "cwd": "/repo",
            "tool_input": {
                "notebook_path": "/repo/e2e/notebook.test.ipynb",
                "new_source": "assert 1 == 1",
            },
        }
    )
    decision = json.loads(result.stdout)
    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_completes_well_under_the_2s_budget(tmp_path: Path) -> None:
    db_path = _db_with_task(tmp_path, "t1", allow_test_edits=False)
    started = time.monotonic()
    _run(
        {
            "tool_name": "Edit",
            "cwd": "/repo",
            "tool_input": {"file_path": "/repo/src/test/java/AppTest.java"},
        },
        env={"COSMO_DB_PATH": str(db_path), "COSMO_TASK_ID": "t1"},
    )
    assert time.monotonic() - started < 2.0
