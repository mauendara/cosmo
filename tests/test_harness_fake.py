"""`FakeHarnessAdapter` (plan Phase 3): the scriptable double every later
phase's state-machine tests are meant to target instead of the real CLI."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from cosmo.config import load_config
from cosmo.harness.fake import FakeHarnessAdapter, FakeOutcome, ScriptedCall

NO_USER_CONFIG = Path("/nonexistent/config.toml")


def _adapter(**kwargs: object) -> FakeHarnessAdapter:
    cfg = load_config(config_path=NO_USER_CONFIG)
    return FakeHarnessAdapter(cfg, **kwargs)  # type: ignore[arg-type]


def test_default_script_succeeds() -> None:
    adapter = _adapter()

    result = adapter.implement("t1", Path("openspec/changes/x"))

    assert result.success is True
    assert result.exit_code == 0
    assert result.session_id == "fake-session"


def test_scripted_outcomes_are_consumed_in_order_and_the_last_repeats() -> None:
    adapter = _adapter(
        script=[
            ScriptedCall(outcome=FakeOutcome.SUCCESS),
            ScriptedCall(outcome=FakeOutcome.CODE_FAILURE),
        ]
    )

    first = adapter.implement("t1", Path("x"))
    second = adapter.implement("t1", Path("x"))
    third = adapter.implement("t1", Path("x"))

    assert first.success is True
    assert second.success is False
    assert third.success is False  # last scripted call repeats, doesn't raise


def test_propose_and_implement_and_probe_are_recorded_on_the_call_audit_trail() -> None:
    adapter = _adapter()

    adapter.propose(Path("openspec/changes/x"), {"task_id": "t1"})
    adapter.implement("t1", Path("x"), retry_context="prior failure detail")
    adapter.probe("smoke test")

    assert adapter.calls == [
        ("propose", "t1", None),
        ("implement", "t1", "prior failure detail"),
        ("probe", "probe", None),
    ]


def test_review_is_recorded_on_the_call_audit_trail_and_scriptable() -> None:
    """v4 workflow changes: `review()` reuses the same script/audit-trail
    mechanism as `propose`/`implement` -- the verdict itself is a file a
    test writes directly (`task.review.review_result_path`), not something
    `FakeOutcome` models (see `FakeHarnessAdapter.review`'s own comment)."""
    adapter = _adapter(script=[ScriptedCall(outcome=FakeOutcome.SUCCESS)])

    result = adapter.review("t1", Path("openspec/changes/x"), "develop")

    assert result.success is True
    assert adapter.calls == [("review", "t1", None)]


def test_get_progress_defaults_to_zero_and_is_settable() -> None:
    adapter = _adapter()
    assert adapter.get_progress("t1") == (0, 0)

    adapter.set_progress("t1", 3, 7)

    assert adapter.get_progress("t1") == (3, 7)


def test_hang_blocks_until_cancel_is_called() -> None:
    adapter = _adapter(script=ScriptedCall(outcome=FakeOutcome.HANG))
    results: list[bool] = []

    def _run() -> None:
        result = adapter.implement("t1", Path("x"))
        results.append(result.success)

    thread = threading.Thread(target=_run)
    thread.start()
    time.sleep(0.1)
    assert results == []  # still hung -- cancel() hasn't been called yet

    adapter.cancel("t1")
    thread.join(timeout=2.0)

    assert results == [False]


def test_rate_limit_and_cost_overrun_outcomes_report_their_own_fields() -> None:
    """Phase 8 is the first real caller of these two `FakeOutcome` values
    (Phase 3's own docstring on `ScriptedCall` flagged their nuance as
    deliberately unmodeled until whichever phase needed it). RATE_LIMIT is a
    genuinely failed call carrying a quota signal (`cosmo.run.quota` only
    treats a signal as actionable on a failed call, matching the real
    Claude adapter's `extract_quota_signal`). COST_OVERRUN is the opposite
    case spec 7.3 actually describes: an otherwise-successful call whose
    reported cost is what becomes the problem -- distinct from RATE_LIMIT
    so a test can exercise the cost-ceiling path without also tripping
    quota detection."""
    adapter = _adapter(
        script=[
            ScriptedCall(outcome=FakeOutcome.RATE_LIMIT, output_summary="rate limited"),
            ScriptedCall(outcome=FakeOutcome.COST_OVERRUN, total_cost_usd=999.0),
        ]
    )

    rate_limited = adapter.implement("t1", Path("x"))
    cost_overrun = adapter.implement("t1", Path("x"))

    assert rate_limited.success is False
    assert rate_limited.output_summary == "rate limited"
    assert rate_limited.quota_window == "five_hour"
    assert cost_overrun.success is True
    assert cost_overrun.total_cost_usd == 999.0
