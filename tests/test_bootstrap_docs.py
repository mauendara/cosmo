"""`copy_project_docs` never-overwrite semantics (spec 10.4 step 3)."""

from __future__ import annotations

from pathlib import Path

from cosmo.bootstrap.docs import copy_project_docs


def _fixture_templates_root(tmp_path: Path) -> Path:
    root = tmp_path / "templates"
    docs = root / "projects" / "widget-stack" / "docs"
    (docs / "backend").mkdir(parents=True)
    (docs / "backend" / "architecture.md").write_text("template content\n")
    (docs / "base-standards.md").write_text("standards\n")
    return root


def test_first_run_creates_every_file_preserving_subdirectories(tmp_path: Path) -> None:
    templates_root = _fixture_templates_root(tmp_path)
    target = tmp_path / "target"
    target.mkdir()

    result = copy_project_docs("widget-stack", target, templates_root=templates_root)

    assert (target / "docs" / "backend" / "architecture.md").read_text() == "template content\n"
    assert (target / "docs" / "base-standards.md").read_text() == "standards\n"
    assert {p.as_posix() for p in result.created} == {
        "backend/architecture.md",
        "base-standards.md",
    }
    assert result.skipped == []


def test_docs_specs_directory_is_created_even_though_no_template_ships_it(
    tmp_path: Path,
) -> None:
    templates_root = _fixture_templates_root(tmp_path)
    target = tmp_path / "target"
    target.mkdir()

    copy_project_docs("widget-stack", target, templates_root=templates_root)

    assert (target / "docs" / "specs").is_dir()


def test_rerun_never_overwrites_an_existing_file_by_default(tmp_path: Path) -> None:
    templates_root = _fixture_templates_root(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    copy_project_docs("widget-stack", target, templates_root=templates_root)
    (target / "docs" / "base-standards.md").write_text("the developer's own edits\n")

    result = copy_project_docs("widget-stack", target, templates_root=templates_root)

    assert (target / "docs" / "base-standards.md").read_text() == "the developer's own edits\n"
    assert result.created == []
    assert {p.as_posix() for p in result.skipped} == {
        "backend/architecture.md",
        "base-standards.md",
    }


def test_force_overwrites_existing_files(tmp_path: Path) -> None:
    templates_root = _fixture_templates_root(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    copy_project_docs("widget-stack", target, templates_root=templates_root)
    (target / "docs" / "base-standards.md").write_text("the developer's own edits\n")

    result = copy_project_docs("widget-stack", target, force=True, templates_root=templates_root)

    assert (target / "docs" / "base-standards.md").read_text() == "standards\n"
    assert {p.as_posix() for p in result.created} == {
        "backend/architecture.md",
        "base-standards.md",
    }
    assert result.skipped == []
