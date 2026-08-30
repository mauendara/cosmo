"""`compute_template_version` (spec 9.2's `template_version`, plan Phase 4)."""

from __future__ import annotations

from pathlib import Path

from cosmo.bootstrap.hashing import compute_template_version


def _write(tree: Path, rel: str, content: str) -> None:
    path = tree / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_identical_content_hashes_the_same_regardless_of_write_order(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    _write(a, "one.txt", "hello")
    _write(a, "sub/two.txt", "world")
    _write(b, "sub/two.txt", "world")
    _write(b, "one.txt", "hello")

    assert compute_template_version(a) == compute_template_version(b)


def test_changing_file_content_changes_the_version(tmp_path: Path) -> None:
    tree = tmp_path / "t"
    _write(tree, "one.txt", "hello")
    before = compute_template_version(tree)

    _write(tree, "one.txt", "hello!")
    after = compute_template_version(tree)

    assert before != after


def test_adding_a_file_changes_the_version(tmp_path: Path) -> None:
    tree = tmp_path / "t"
    _write(tree, "one.txt", "hello")
    before = compute_template_version(tree)

    _write(tree, "two.txt", "another")
    after = compute_template_version(tree)

    assert before != after


def test_pycache_artifacts_are_excluded_from_the_hash(tmp_path: Path) -> None:
    """A hook script that imports a sibling module leaves a __pycache__/*.pyc
    next to it the moment it's run -- including by this project's own test
    suite exercising the shipped hooks. That bytecode must not perturb
    template_version."""
    tree = tmp_path / "t"
    _write(tree, "hooks/guard.py", "import _hooklib\n")
    before = compute_template_version(tree)

    _write(tree, "hooks/__pycache__/guard.cpython-314.pyc", "not real bytecode")
    after = compute_template_version(tree)

    assert before == after


def test_renaming_a_file_changes_the_version_even_with_identical_content(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    _write(a, "one.txt", "hello")
    _write(b, "two.txt", "hello")

    assert compute_template_version(a) != compute_template_version(b)
