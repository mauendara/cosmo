"""Parsers turning each tool's own output into `TestCounts`/`FailingTest`
(spec 9.3). Regex/JSON-shape heuristics, not the tools' own libraries --
matching the diff gate's stance (module docstring, `diffgate.py`): good
enough to drive `error_detail` and pass/fail counts, not a claim of being a
general-purpose Surefire/Vitest/Playwright report parser.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from cosmo.gate.types import FailingTest, TestCounts

_MAVEN_TEST_SET = re.compile(r"^Test set:\s*(?P<class>[\w.$]+)\s*$", re.MULTILINE)
_MAVEN_SUMMARY = re.compile(r"Tests run: (\d+), Failures: (\d+), Errors: (\d+), Skipped: (\d+)")
_MAVEN_FAILING_METHOD = re.compile(
    r"^(?P<method>[\w.$]+)\s+--\s+Time elapsed:.*<<<\s*(FAILURE|ERROR)!\s*$", re.MULTILINE
)


def parse_maven_surefire_reports(reports_dir: Path) -> tuple[TestCounts, list[FailingTest]]:
    """Reads Surefire's own per-class `.txt` reports (`target/surefire-
    reports/*.txt`) rather than console output -- the console's "Failures:"
    recap section omits the assertion message entirely (confirmed by hand
    against a real failing run), while each report file carries the full
    exception message and stack for exactly the methods that failed."""
    passed = failed = skipped = 0
    failing: list[FailingTest] = []

    for report in sorted(reports_dir.glob("*.txt")):
        text = report.read_text(errors="replace")
        class_match = _MAVEN_TEST_SET.search(text)
        class_name = class_match.group("class") if class_match else report.stem

        summary = _MAVEN_SUMMARY.search(text)
        if summary:
            run, fail, err, skip = (int(g) for g in summary.groups())
            passed += run - fail - err - skip
            failed += fail + err
            skipped += skip

        for match in _MAVEN_FAILING_METHOD.finditer(text):
            method = match.group("method").rsplit(".", 1)[-1]
            after = text[match.end() :]
            # Up to the first stack frame is the exception message; the
            # first few frames after it are the trimmed stack (spec 9.3).
            frame_start = after.find("\n\tat ")
            message_block = after[: frame_start if frame_start != -1 else len(after)].strip()
            stack_lines = (
                [line.strip() for line in after[frame_start:].splitlines() if line.strip()][:5]
                if frame_start != -1
                else []
            )
            message_lines = [line for line in message_block.splitlines() if line.strip()][:4]
            failing.append(
                FailingTest(
                    test_id=f"{class_name}.{method}",
                    assertion=" | ".join(message_lines) or None,
                    stack_excerpt="\n".join(stack_lines) or None,
                )
            )

    return TestCounts(passed=max(passed, 0), failed=failed, skipped=skipped), failing


def parse_vitest_json(raw: str) -> tuple[TestCounts, list[FailingTest]]:
    """Shape of `vitest run --reporter=json` (Jest-compatible)."""
    data = json.loads(raw)
    passed = int(data.get("numPassedTests", 0))
    failed = int(data.get("numFailedTests", 0))
    skipped = int(data.get("numPendingTests", 0)) + int(data.get("numTodoTests", 0))

    failing: list[FailingTest] = []
    for file_result in data.get("testResults", []):
        for assertion in file_result.get("assertionResults", []):
            if assertion.get("status") != "failed":
                continue
            messages = assertion.get("failureMessages") or []
            stack = "\n".join(messages)[:1000] if messages else None
            failing.append(
                FailingTest(
                    test_id=assertion.get("fullName") or assertion.get("title", "unknown"),
                    assertion=messages[0].splitlines()[0] if messages else None,
                    stack_excerpt=stack,
                )
            )
    return TestCounts(passed=passed, failed=failed, skipped=skipped), failing


def parse_playwright_json(raw: str) -> tuple[TestCounts, list[FailingTest], list[Path]]:
    """Shape of `@playwright/test`'s built-in `json` reporter."""
    data = json.loads(raw)
    passed = failed = skipped = 0
    failing: list[FailingTest] = []
    artifacts: list[Path] = []

    def walk(suite: dict[str, Any]) -> None:
        nonlocal passed, failed, skipped
        for spec in suite.get("specs", []):
            for test in spec.get("tests", []):
                results = test.get("results", [])
                status = test.get("status") or (results[-1].get("status") if results else None)
                if status == "expected":
                    passed += 1
                elif status == "skipped":
                    skipped += 1
                else:
                    failed += 1
                    last = results[-1] if results else {}
                    error = last.get("error") or {}
                    for attachment in last.get("attachments", []) or []:
                        path = attachment.get("path")
                        if path:
                            artifacts.append(Path(path))
                    message = error.get("message") or ""
                    failing.append(
                        FailingTest(
                            test_id=f"{spec.get('title', 'unknown')}",
                            assertion=message.splitlines()[0] if message else None,
                            stack_excerpt=(error.get("stack") or "")[:1000] or None,
                        )
                    )
        for child in suite.get("suites", []) or []:
            walk(child)

    for suite in data.get("suites", []):
        walk(suite)

    return TestCounts(passed=passed, failed=failed, skipped=skipped), failing, artifacts
