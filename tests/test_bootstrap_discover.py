"""Locating and listing Cosmo's own templates/ tree (plan Phase 4)."""

from __future__ import annotations

import pytest

from cosmo.bootstrap.discover import (
    TemplatesRootNotFoundError,
    harness_template_dir,
    list_templates,
    project_template_dir,
    templates_root,
)


def test_templates_root_resolves_to_a_real_directory_in_this_checkout() -> None:
    root = templates_root()
    assert root.is_dir()
    assert (root / "harness").is_dir()
    assert (root / "projects").is_dir()


def test_list_templates_finds_the_real_shipped_templates() -> None:
    listing = list_templates()
    assert "claude" in listing.harnesses
    assert "_blank" in listing.project_templates
    assert "java-spring-react" in listing.project_templates


def test_harness_template_dir_of_a_real_harness_exists() -> None:
    d = harness_template_dir("claude")
    assert (d / "settings.json").is_file()
    assert (d / "CLAUDE.md").is_file()


def test_harness_template_dir_of_an_unknown_harness_raises() -> None:
    with pytest.raises(TemplatesRootNotFoundError):
        harness_template_dir("no-such-harness")


def test_project_template_dir_of_an_unknown_template_raises() -> None:
    with pytest.raises(TemplatesRootNotFoundError):
        project_template_dir("no-such-template")
