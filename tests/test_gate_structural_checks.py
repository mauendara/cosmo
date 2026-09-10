"""`gate.structural_checks` (G4, docs/v15-fixes-after-wa-chat-run.md): pure,
stdlib-only functions shared between `gate.diffgate` and every harness
template's `hooks/test_path_guard.py` -- see the module's own docstring for
why this file must stay copyable byte-for-byte into `templates/`.
"""

from __future__ import annotations

from pathlib import Path

from cosmo.gate.structural_checks import count_assertions, find_skip_annotation, is_test_path

HARNESS_TEMPLATES_ROOT = Path(__file__).resolve().parent.parent / "templates" / "harness"
SOURCE = Path(__file__).resolve().parent.parent / "src" / "cosmo" / "gate" / "structural_checks.py"
COPIES = [
    HARNESS_TEMPLATES_ROOT / harness / "hooks" / "_structural_checks.py"
    for harness in ("claude", "ori-claude", "claude-openrouter")
]


def test_count_assertions_counts_known_frameworks() -> None:
    lines = [
        "assertThat(1).isEqualTo(1);",
        "assertEquals(1, 1);",
        "expect(1).toBe(1);",
        "not an assertion at all",
    ]
    assert count_assertions(lines) == 3


def test_count_assertions_of_empty_is_zero() -> None:
    assert count_assertions([]) == 0


def test_is_test_path_matches_plain_glob() -> None:
    assert is_test_path("src/test/FooTest.java", ["src/test/**"]) == "src/test/**"


def test_is_test_path_matches_leading_doublestar_at_top_level() -> None:
    # fnmatch alone can't match "**/src/test/**" against a top-level path
    # with nothing before "src/" -- the whole reason this helper exists.
    assert is_test_path("src/test/Foo.java", ["**/src/test/**"]) == "**/src/test/**"


def test_is_test_path_returns_none_when_nothing_matches() -> None:
    assert is_test_path("src/main/Foo.java", ["src/test/**"]) is None


def test_find_skip_annotation_finds_a_hit() -> None:
    lines = ["  void a() {}", "  @Disabled", "  void b() {}"]
    assert find_skip_annotation(lines, ["@Disabled", "@Ignore"]) == "@Disabled"


def test_find_skip_annotation_returns_none_when_absent() -> None:
    lines = ["  void a() {}"]
    assert find_skip_annotation(lines, ["@Disabled", "@Ignore"]) is None


def test_every_harness_template_carries_a_byte_identical_copy() -> None:
    """The whole point of keeping this module stdlib-only (docstring): it
    can be copied wholesale into `hooks/`, same posture `_hooklib.py`
    already holds itself to. Drift here would silently reintroduce two
    independently-maintained assertion-counting implementations."""
    canonical = SOURCE.read_bytes()
    for copy in COPIES:
        assert copy.is_file(), f"missing {copy}"
        assert copy.read_bytes() == canonical, f"{copy} has drifted from {SOURCE}"
