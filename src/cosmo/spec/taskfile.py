"""The `docs/specs/<name>-spec/tasks/<task>-task.md` file convention (v4
workflow changes, see `docs/v4-changes-to-workflow-plan.md`): YAML
frontmatter (`task_id`, `depends_on`, `priority`, `title`, `allow_test_edits`)
plus a markdown body -- the same frontmatter-plus-body shape every
skill/agent file under `templates/harness/claude/` already uses (spec 10.3's
own convention), just a new location/purpose for it, not a new file format.
`cosmo spec add`'s own harness call (`skills/spec-enrichment/SKILL.md`) is
instructed to emit exactly this shape; this module is what reads it back for
`cosmo spec add`'s own preview and `cosmo spec queue`'s real insert.

`allow_test_edits` (default `False`, spec 2.5) is optional frontmatter, not a
required key: found live -- a spec-batch task whose entire deliverable lives
under a guardrailed test path (e.g. `e2e/**`) always queued with the flag
unset, since `cosmo spec queue` had no way to convey a per-task value at all;
the harness then correctly refused to write anything under the guarded path
and submitted an empty implementation, rejected by review three times running
before a human traced it back to the missing flag. `spec_add`'s own prompt
now tells the enrichment harness to set this frontmatter key when it
decomposes a task shaped that way, closing the gap at the source rather than
requiring a human to notice and re-queue by hand.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

_FRONTMATTER_DELIM = "---"


class TaskFileError(ValueError):
    """A `*-task.md` file's frontmatter is missing, malformed, or missing a
    required key -- always includes the offending path, since `cosmo spec
    queue` may be reading many of these in one call."""


@dataclass(frozen=True, slots=True)
class SpecTaskFile:
    path: Path
    task_id: str
    depends_on: list[str]
    priority: int
    title: str
    allow_test_edits: bool
    body: str


def parse_task_file(path: Path) -> SpecTaskFile:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FRONTMATTER_DELIM:
        raise TaskFileError(f"{path}: missing YAML frontmatter (expected a leading '---' line)")
    try:
        closing_offset = lines[1:].index(_FRONTMATTER_DELIM)
    except ValueError:
        raise TaskFileError(f"{path}: frontmatter has no closing '---' line") from None
    end = closing_offset + 1
    frontmatter_text = "\n".join(lines[1:end])
    body = "\n".join(lines[end + 1 :]).lstrip("\n")

    try:
        data = yaml.safe_load(frontmatter_text) or {}
    except yaml.YAMLError as exc:
        raise TaskFileError(f"{path}: invalid YAML frontmatter: {exc}") from exc
    if not isinstance(data, dict):
        raise TaskFileError(f"{path}: frontmatter must be a YAML mapping")

    task_id = data.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise TaskFileError(f"{path}: frontmatter must set a non-empty string 'task_id'")

    title_raw = data.get("title", task_id)
    title = title_raw if isinstance(title_raw, str) else task_id

    priority = data.get("priority", 0)
    if not isinstance(priority, int) or isinstance(priority, bool):
        raise TaskFileError(f"{path}: 'priority' must be an integer")

    depends_on = data.get("depends_on", [])
    if not isinstance(depends_on, list) or not all(isinstance(d, str) for d in depends_on):
        raise TaskFileError(f"{path}: 'depends_on' must be a list of task_id strings")

    allow_test_edits = data.get("allow_test_edits", False)
    if not isinstance(allow_test_edits, bool):
        raise TaskFileError(f"{path}: 'allow_test_edits' must be a boolean")

    return SpecTaskFile(
        path=path,
        task_id=task_id,
        depends_on=list(depends_on),
        priority=priority,
        title=title,
        allow_test_edits=allow_test_edits,
        body=body,
    )


def list_task_files(tasks_dir: Path) -> list[SpecTaskFile]:
    """Sorted by filename for deterministic preview/insert order -- real
    ordering among eligible tasks is `depends_on`/`priority`'s job
    (`run.dag.resolve_execution_order`), not this listing's."""
    if not tasks_dir.is_dir():
        return []
    return [parse_task_file(p) for p in sorted(tasks_dir.glob("*-task.md"))]
