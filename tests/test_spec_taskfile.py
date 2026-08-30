"""`cosmo.spec.taskfile` (v4 workflow changes): parsing the
`docs/specs/<name>-spec/tasks/<task>-task.md` frontmatter contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cosmo.spec.taskfile import TaskFileError, list_task_files, parse_task_file


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_parses_full_frontmatter_and_body(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "backend-task.md",
        "---\n"
        "task_id: demo-backend\n"
        "depends_on: [a, b]\n"
        "priority: 3\n"
        "title: Add health check\n"
        "---\n"
        "\n"
        "Implement GET /health.\n",
    )

    tf = parse_task_file(path)

    assert tf.task_id == "demo-backend"
    assert tf.depends_on == ["a", "b"]
    assert tf.priority == 3
    assert tf.title == "Add health check"
    assert tf.body == "Implement GET /health."


def test_defaults_depends_on_priority_and_title(tmp_path: Path) -> None:
    path = _write(tmp_path / "t.md", "---\ntask_id: only-id\n---\n\nbody\n")

    tf = parse_task_file(path)

    assert tf.depends_on == []
    assert tf.priority == 0
    assert tf.title == "only-id"  # falls back to task_id
    assert tf.allow_test_edits is False


def test_allow_test_edits_true_parses(tmp_path: Path) -> None:
    """Found live: a spec-batch task whose whole deliverable was Playwright
    specs under a guardrailed `e2e/` path had no way to request the flag at
    all -- `cosmo spec queue` always inserted `allow_test_edits=False`,
    IMPLEMENTING correctly refused every write under the guard, and review
    rejected the resulting empty submission three times running before a
    human traced it back to the missing flag."""
    path = _write(
        tmp_path / "t.md",
        "---\ntask_id: e2e-suite\nallow_test_edits: true\n---\n\nbody\n",
    )

    tf = parse_task_file(path)

    assert tf.allow_test_edits is True


def test_non_bool_allow_test_edits_raises(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "t.md",
        "---\ntask_id: x\nallow_test_edits: yes-please\n---\n\nbody\n",
    )
    with pytest.raises(TaskFileError, match="allow_test_edits"):
        parse_task_file(path)


def test_missing_frontmatter_raises(tmp_path: Path) -> None:
    path = _write(tmp_path / "t.md", "just a markdown file\n")
    with pytest.raises(TaskFileError, match="missing YAML frontmatter"):
        parse_task_file(path)


def test_unclosed_frontmatter_raises(tmp_path: Path) -> None:
    path = _write(tmp_path / "t.md", "---\ntask_id: x\n\nno closing delimiter\n")
    with pytest.raises(TaskFileError, match="no closing"):
        parse_task_file(path)


def test_missing_task_id_raises(tmp_path: Path) -> None:
    path = _write(tmp_path / "t.md", "---\ntitle: no id here\n---\n\nbody\n")
    with pytest.raises(TaskFileError, match="task_id"):
        parse_task_file(path)


def test_non_list_depends_on_raises(tmp_path: Path) -> None:
    path = _write(tmp_path / "t.md", "---\ntask_id: x\ndepends_on: not-a-list\n---\n\nbody\n")
    with pytest.raises(TaskFileError, match="depends_on"):
        parse_task_file(path)


def test_non_integer_priority_raises(tmp_path: Path) -> None:
    path = _write(tmp_path / "t.md", "---\ntask_id: x\npriority: soon\n---\n\nbody\n")
    with pytest.raises(TaskFileError, match="priority"):
        parse_task_file(path)


def test_list_task_files_is_sorted_and_empty_for_a_missing_dir(tmp_path: Path) -> None:
    assert list_task_files(tmp_path / "nonexistent") == []

    tasks_dir = tmp_path / "tasks"
    _write(tasks_dir / "z-task.md", "---\ntask_id: z\n---\n\nbody\n")
    _write(tasks_dir / "a-task.md", "---\ntask_id: a\n---\n\nbody\n")
    _write(tasks_dir / "not-a-task.txt", "ignored\n")

    files = list_task_files(tasks_dir)

    assert [f.task_id for f in files] == ["a", "z"]
