"""`run.dag`: `depends_on` as a hard ordering constraint, `priority` as a
soft tie-breaker among tasks already eligible, cycle detection (plan Phase
8 exit criteria)."""

from __future__ import annotations

import pytest

from cosmo.run.dag import DagCycleError, find_cycle, resolve_execution_order
from cosmo.store.reader import TaskRow


def _task(
    task_id: str,
    *,
    depends_on: list[str] | None = None,
    priority: int = 0,
    status: str = "queued",
    created_at: str = "2026-01-01T00:00:00.000Z",
) -> TaskRow:
    return TaskRow(
        task_id=task_id,
        spec_path=f"openspec/changes/{task_id}",
        depends_on=depends_on or [],
        priority=priority,
        status=status,
        attempt_count=0,
        max_attempts=2,
        last_error=None,
        blocked_reason=None,
        allow_test_edits=False,
        worktree_path=None,
        session_id=None,
        created_at=created_at,
        updated_at=created_at,
    )


def test_depends_on_is_a_hard_ordering_constraint() -> None:
    tasks = [_task("b", depends_on=["a"]), _task("a")]

    order = resolve_execution_order(tasks)

    assert order == ["a", "b"]


def test_priority_only_breaks_ties_among_already_eligible_tasks() -> None:
    # "c" has the highest priority but depends on "b", which depends on
    # "a" -- it must not jump the queue ahead of "a"/"b" just because its
    # priority is higher; it only becomes *eligible* once both are done.
    tasks = [
        _task("a", priority=0),
        _task("b", depends_on=["a"], priority=0),
        _task("c", depends_on=["b"], priority=100),
        _task("z", priority=1),
    ]

    order = resolve_execution_order(tasks)

    # "a" and "z" are both eligible from the start; "z" (priority 1) goes
    # before "a" (priority 0). "b" only becomes eligible once "a" is
    # scheduled, and "c" only once "b" is -- their high/low priority never
    # gets a chance to matter against "a"/"z" at all.
    assert order == ["z", "a", "b", "c"]


def test_a_done_task_satisfies_a_dependency_without_appearing_in_the_order() -> None:
    tasks = [_task("a", status="done"), _task("b", depends_on=["a"])]

    order = resolve_execution_order(tasks)

    assert order == ["b"]


def test_a_dependency_on_a_blocked_task_never_becomes_eligible() -> None:
    tasks = [_task("a", status="blocked"), _task("b", depends_on=["a"])]

    order = resolve_execution_order(tasks)

    assert order == []


def test_a_dependency_on_a_nonexistent_task_never_becomes_eligible() -> None:
    tasks = [_task("b", depends_on=["ghost"])]

    order = resolve_execution_order(tasks)

    assert order == []


def test_only_queued_tasks_appear_in_the_order() -> None:
    tasks = [_task("a", status="implementing"), _task("b")]

    order = resolve_execution_order(tasks)

    assert order == ["b"]


def test_a_direct_cycle_is_detected() -> None:
    tasks = [_task("a", depends_on=["b"]), _task("b", depends_on=["a"])]

    with pytest.raises(DagCycleError):
        resolve_execution_order(tasks)


def test_a_longer_cycle_is_detected() -> None:
    tasks = [
        _task("a", depends_on=["b"]),
        _task("b", depends_on=["c"]),
        _task("c", depends_on=["a"]),
    ]

    with pytest.raises(DagCycleError):
        resolve_execution_order(tasks)


def test_a_done_task_cannot_be_part_of_a_live_cycle() -> None:
    # "a" depends on "b" which is already done; "b"'s own stale depends_on
    # (if it named "a") is irrelevant now that "b" already ran -- only
    # non-done tasks' edges are checked.
    tasks = [_task("a", depends_on=["b"]), _task("b", depends_on=["a"], status="done")]

    order = resolve_execution_order(tasks)

    assert order == ["a"]


def test_find_cycle_returns_the_cyclical_path() -> None:
    graph = {"a": ["b"], "b": ["c"], "c": ["a"]}

    cycle = find_cycle(graph)

    assert cycle is not None
    assert cycle[0] == cycle[-1]
    assert set(cycle) == {"a", "b", "c"}


def test_find_cycle_returns_none_for_an_acyclic_graph() -> None:
    graph = {"a": [], "b": ["a"], "c": ["b"]}

    assert find_cycle(graph) is None


def test_find_cycle_ignores_a_dependency_outside_the_given_graph() -> None:
    # Exactly `cli/main.py`'s `queue add` usage: a new task's depends_on
    # naming an unrelated, not-yet-`done` task is an unmet dependency, not
    # a cycle.
    graph = {"a": ["ghost"]}

    assert find_cycle(graph) is None
