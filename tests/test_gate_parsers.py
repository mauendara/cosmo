"""Parsers for each tool's own output (spec 9.3) -- Maven's Surefire text
reports are exercised against real `mvn` output captured by hand from the
Phase 6 fixture repo (`tests/fixtures/gate_repo/backend`); Vitest/Playwright
are exercised against their documented JSON reporter shapes.
"""

from __future__ import annotations

import json
from pathlib import Path

from cosmo.gate.parsers import (
    parse_maven_surefire_reports,
    parse_playwright_json,
    parse_vitest_json,
)

_PASSING_SUREFIRE = """\
-------------------------------------------------------------------------------
Test set: com.cosmo.fixture.HelloControllerTest
-------------------------------------------------------------------------------
Tests run: 2, Failures: 0, Errors: 0, Skipped: 0, Time elapsed: 0.093 s \
-- in com.cosmo.fixture.HelloControllerTest
"""

_FAILING_SUREFIRE = """\
-------------------------------------------------------------------------------
Test set: com.cosmo.fixture.HelloControllerTest
-------------------------------------------------------------------------------
Tests run: 2, Failures: 1, Errors: 0, Skipped: 0, Time elapsed: 0.097 s \
<<< FAILURE! -- in com.cosmo.fixture.HelloControllerTest
com.cosmo.fixture.HelloControllerTest.greetReturnsExpectedMessage \
-- Time elapsed: 0.067 s <<< FAILURE!
java.lang.AssertionError:

Expecting actual:
  "hello from cosmo gate fixture"
to contain:
  "WRONG TEXT"
\tat com.cosmo.fixture.HelloControllerTest.greetReturnsExpectedMessage(HelloControllerTest.java:23)
\tat java.base/java.lang.reflect.Method.invoke(Method.java:580)
"""


def test_parse_maven_surefire_reports_all_passing(tmp_path: Path) -> None:
    reports = tmp_path / "surefire-reports"
    reports.mkdir()
    (reports / "com.cosmo.fixture.HelloControllerTest.txt").write_text(_PASSING_SUREFIRE)

    counts, failing = parse_maven_surefire_reports(reports)
    assert counts.passed == 2
    assert counts.failed == 0
    assert failing == []


def test_parse_maven_surefire_reports_one_failing(tmp_path: Path) -> None:
    reports = tmp_path / "surefire-reports"
    reports.mkdir()
    (reports / "com.cosmo.fixture.HelloControllerTest.txt").write_text(_FAILING_SUREFIRE)

    counts, failing = parse_maven_surefire_reports(reports)
    assert counts.passed == 1
    assert counts.failed == 1
    assert len(failing) == 1
    assert failing[0].test_id == "com.cosmo.fixture.HelloControllerTest.greetReturnsExpectedMessage"
    assert failing[0].assertion is not None
    assert "AssertionError" in failing[0].assertion
    assert failing[0].stack_excerpt is not None
    assert "HelloControllerTest.java:23" in failing[0].stack_excerpt


def test_parse_maven_surefire_reports_aggregates_multiple_files(tmp_path: Path) -> None:
    reports = tmp_path / "surefire-reports"
    reports.mkdir()
    (reports / "A.txt").write_text(_PASSING_SUREFIRE)
    (reports / "B.txt").write_text(_FAILING_SUREFIRE)

    counts, failing = parse_maven_surefire_reports(reports)
    assert counts.passed == 3
    assert counts.failed == 1
    assert len(failing) == 1


def test_parse_vitest_json_passing() -> None:
    raw = json.dumps(
        {
            "numPassedTests": 2,
            "numFailedTests": 0,
            "numPendingTests": 0,
            "testResults": [{"assertionResults": [{"status": "passed"}, {"status": "passed"}]}],
        }
    )
    counts, failing = parse_vitest_json(raw)
    assert counts.passed == 2
    assert counts.failed == 0
    assert failing == []


def test_parse_vitest_json_failing() -> None:
    raw = json.dumps(
        {
            "numPassedTests": 1,
            "numFailedTests": 1,
            "numPendingTests": 0,
            "testResults": [
                {
                    "assertionResults": [
                        {"status": "passed", "fullName": "a passes"},
                        {
                            "status": "failed",
                            "fullName": "backendUrl > defaults to localhost:8080",
                            "failureMessages": ["AssertionError: expected 'x' to be 'y'"],
                        },
                    ]
                }
            ],
        }
    )
    counts, failing = parse_vitest_json(raw)
    assert counts.passed == 1
    assert counts.failed == 1
    assert len(failing) == 1
    assert failing[0].test_id == "backendUrl > defaults to localhost:8080"
    assert failing[0].assertion == "AssertionError: expected 'x' to be 'y'"


def test_parse_playwright_json_passing() -> None:
    raw = json.dumps(
        {
            "suites": [
                {
                    "specs": [
                        {
                            "title": "home page shows the backend greeting",
                            "tests": [{"status": "expected", "results": [{"status": "passed"}]}],
                        }
                    ],
                    "suites": [],
                }
            ]
        }
    )
    counts, failing, artifacts = parse_playwright_json(raw)
    assert counts.passed == 1
    assert counts.failed == 0
    assert failing == []
    assert artifacts == []


def test_parse_playwright_json_failing_with_nested_suites_and_attachments() -> None:
    raw = json.dumps(
        {
            "suites": [
                {
                    "specs": [],
                    "suites": [
                        {
                            "specs": [
                                {
                                    "title": "home page shows the backend greeting",
                                    "tests": [
                                        {
                                            "status": "unexpected",
                                            "results": [
                                                {
                                                    "status": "failed",
                                                    "error": {
                                                        "message": "Timed out waiting for text",
                                                        "stack": "Error: Timed out\\n at foo.ts:1",
                                                    },
                                                    "attachments": [
                                                        {
                                                            "name": "trace",
                                                            "path": "/work/trace.zip",
                                                        }
                                                    ],
                                                }
                                            ],
                                        }
                                    ],
                                }
                            ],
                            "suites": [],
                        }
                    ],
                }
            ]
        }
    )
    counts, failing, artifacts = parse_playwright_json(raw)
    assert counts.failed == 1
    assert counts.passed == 0
    assert len(failing) == 1
    assert failing[0].test_id == "home page shows the backend greeting"
    assert failing[0].assertion == "Timed out waiting for text"
    assert artifacts == [Path("/work/trace.zip")]
