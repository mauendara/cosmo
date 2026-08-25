"""Persistent state: SQLite schema, single-writer discipline (spec 8).

The raw write-connection factory is deliberately not re-exported here --
`StoreWriter` is the only supported way to get a writable connection outside
the migration runner.
"""

from __future__ import annotations

from cosmo.store.reader import (
    EventRow,
    ProgressRow,
    ProjectRow,
    TaskRow,
    find_project_by_path,
    get_progress,
    get_task,
    list_events,
    list_projects,
    list_tasks,
)
from cosmo.store.writer import StoreWriter, TaskNotFoundError

__all__ = [
    "StoreWriter",
    "TaskNotFoundError",
    "TaskRow",
    "ProgressRow",
    "EventRow",
    "ProjectRow",
    "list_tasks",
    "get_task",
    "get_progress",
    "list_events",
    "list_projects",
    "find_project_by_path",
]
