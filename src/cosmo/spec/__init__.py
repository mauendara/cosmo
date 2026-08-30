"""The raw-spec workflow (v4 workflow changes, see
`docs/v4-changes-to-workflow-plan.md`): `docs/specs/<name>-spec.md` ->
enrichment/decomposition -> `docs/specs/<name>-spec/tasks/<task>-task.md` ->
`cosmo spec queue`. `cli.main`'s `spec_app` commands are a thin layer over
this package, the same split `cosmo.run`/`cosmo.task` already have with
`cli.main`'s `queue_app`/`run` commands.
"""

from __future__ import annotations

from cosmo.spec.taskfile import SpecTaskFile, TaskFileError, list_task_files, parse_task_file

__all__ = ["SpecTaskFile", "TaskFileError", "list_task_files", "parse_task_file"]
