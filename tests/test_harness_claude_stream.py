"""The stream-json reader and classifier (spec 4, plan Phase 3 exit criterion:
recorded NDJSON fixtures replay through the reader in unit tests).

Fixtures under `fixtures/stream_json/` are captured from -- or, for the
`api_retry`/`tool_call`/`malformed`/`truncated` cases, hand-derived from the
shape of -- a real `claude -p --output-format stream-json --verbose` run.
See the Phase 3 state doc for the raw capture.
"""

from __future__ import annotations

from pathlib import Path

from cosmo.harness.claude.stream import (
    ClassifiedKind,
    NdjsonLineBuffer,
    StreamReader,
    classify_line,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "stream_json"


def _replay(name: str) -> StreamReader:
    reader = StreamReader()
    raw = (FIXTURES / name).read_bytes()
    # Feed in small, misaligned chunks -- not one write per line -- to prove
    # the buffer tolerates a line arriving split across multiple `feed()`
    # calls, which is exactly how `ManagedProcess`'s stdout drain hands bytes
    # over (4096-byte `os.read` chunks, not line-buffered).
    chunk_size = 17
    for i in range(0, len(raw), chunk_size):
        reader.feed(raw[i : i + chunk_size])
    return reader


def test_normal_run_classifies_heartbeat_and_terminal_result() -> None:
    reader = _replay("normal_run.ndjson")

    kinds = [e.kind for e in reader.events]
    assert kinds == [ClassifiedKind.HEARTBEAT, ClassifiedKind.HEARTBEAT, ClassifiedKind.RESULT]
    assert reader.session_id == "f4f79cd3-194e-4084-875e-ecf47b933e5f"
    assert reader.terminal_result is not None
    assert reader.terminal_result.payload["total_cost_usd"] == 0.0733296
    assert reader.terminal_result.payload["num_turns"] == 1


def test_tool_call_content_blocks_are_classified_as_tool_call() -> None:
    reader = _replay("tool_call.ndjson")

    kinds = [e.kind for e in reader.events]
    assert kinds == [
        ClassifiedKind.HEARTBEAT,  # system/init
        ClassifiedKind.TOOL_CALL,  # assistant tool_use
        ClassifiedKind.TOOL_CALL,  # user tool_result
        ClassifiedKind.RESULT,
    ]


def test_api_retry_is_the_primary_quota_signal_in_both_observed_shapes() -> None:
    """Spec 7.2 names `system/api_retry`; the installed CLI actually emits a
    top-level `rate_limit_event` (see stream.py's docstring). Both must
    classify as RATE_LIMIT so whichever a given CLI version emits is caught."""
    reader = _replay("api_retry.ndjson")

    rate_limit_events = [e for e in reader.events if e.kind is ClassifiedKind.RATE_LIMIT]
    assert len(rate_limit_events) == 2
    assert rate_limit_events[0].payload["type"] == "rate_limit_event"
    assert rate_limit_events[1].payload == {
        "type": "system",
        "subtype": "api_retry",
        "retry_after_ms": 30000,
        "session_id": "retry-session",
    }
    assert reader.latest_rate_limit is rate_limit_events[1]
    assert reader.terminal_result is not None


def test_truncated_stream_yields_complete_lines_and_drops_the_partial_tail() -> None:
    reader = _replay("truncated.ndjson")

    # Two complete lines before the cut; the truncated third line never
    # arrives as a complete line, so it must not appear at all -- not as a
    # MALFORMED event, not as anything else. No terminal result either.
    assert len(reader.events) == 2
    assert reader.terminal_result is None
    assert reader.session_id == "truncated-session"


def test_malformed_line_is_isolated_and_does_not_stop_the_stream() -> None:
    reader = _replay("malformed.ndjson")

    kinds = [e.kind for e in reader.events]
    assert kinds == [
        ClassifiedKind.HEARTBEAT,
        ClassifiedKind.MALFORMED,
        ClassifiedKind.HEARTBEAT,
        ClassifiedKind.RESULT,
    ]
    assert reader.terminal_result is not None
    assert reader.terminal_result.payload["total_cost_usd"] == 0.03


def test_prose_content_is_never_inspected_for_signal() -> None:
    """The classifier must key off structured `type`/`subtype` fields, never
    a message's rendered text -- spec 4's "prose parsing is not used"."""
    line = (
        b'{"type":"assistant","message":{"role":"assistant","content":'
        b'[{"type":"text","text":"We are being rate limited, api_retry, please wait"}]},'
        b'"session_id":"s1"}'
    )

    event = classify_line(line)

    assert event.kind is ClassifiedKind.HEARTBEAT


def test_classify_line_never_raises_on_arbitrary_bytes() -> None:
    for garbage in (b"", b"   ", b"not json", b"{", b"[1, 2, 3]", b'"just a string"', b"null"):
        event = classify_line(garbage)
        assert event.kind is ClassifiedKind.MALFORMED


def test_ndjson_line_buffer_reassembles_a_line_split_across_feeds() -> None:
    buf = NdjsonLineBuffer()

    assert buf.feed(b'{"type":"r') == []
    assert buf.feed(b'esult"}\n{"ty') == [b'{"type":"result"}']
    assert buf.feed(b'pe":"x"}\n') == [b'{"type":"x"}']
