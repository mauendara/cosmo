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
    ClassifiedEvent,
    ClassifiedKind,
    NdjsonLineBuffer,
    StreamReader,
    classify_line,
    describe_tool_call,
    extract_quota_signal,
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


def test_a_lone_allowed_status_rate_limit_event_is_not_a_quota_signal() -> None:
    """Real bug, found by hand against a genuine overnight run: a
    `rate_limit_event` fires once per session purely as informational
    telemetry -- `status: "allowed"` on every ordinary call, carrying a real
    `resetsAt` the window reaches regardless of whether anything was ever
    actually rate-limited. A call that fails for an unrelated reason
    (`error_max_turns` here, exactly as captured) must not be reported as a
    *confirmed* quota exhaustion just because that routine event happened to
    stream by. See docs/v3-implementation-state.md for the full capture."""
    reader = _replay("rate_limit_allowed_only.ndjson")

    assert reader.latest_rate_limit is not None
    assert extract_quota_signal(reader) == (None, None)


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


def _tool_use_payload(name: str, tool_input: dict[str, object]) -> dict[str, object]:
    block = {"type": "tool_use", "id": "toolu_1", "name": name, "input": tool_input}
    return {"type": "assistant", "message": {"content": [block]}}


def test_describe_tool_call_summarizes_a_bash_command() -> None:
    reader = _replay("tool_call.ndjson")
    bash_event = next(e for e in reader.events if e.kind is ClassifiedKind.TOOL_CALL)

    assert describe_tool_call(bash_event.payload) == "Bash: echo hi"


def test_describe_tool_call_summarizes_an_edit_by_file_path() -> None:
    payload = _tool_use_payload("Edit", {"file_path": "src/App.tsx", "old_string": "a"})

    assert describe_tool_call(payload) == "Edit: src/App.tsx"


def test_describe_tool_call_falls_back_to_the_tool_name_for_an_unrecognized_tool() -> None:
    payload = _tool_use_payload("SomeFutureTool", {"whatever": "shape"})

    assert describe_tool_call(payload) == "SomeFutureTool"


def test_describe_tool_call_truncates_a_long_detail() -> None:
    long_path = "src/" + ("x" * 200) + ".tsx"
    payload = _tool_use_payload("Write", {"file_path": long_path})

    line = describe_tool_call(payload)

    assert line is not None
    assert len(line) == 100
    assert line.endswith("…")


def test_describe_tool_call_collapses_the_worktree_prefix_before_truncating() -> None:
    """Found by hand watching a real `cosmo run`: the worktree's absolute
    path alone (`/home/.../work/<run_id>/<task_id>/frontend/...`) ate the
    entire 100-char cap, so every activity line truncated before the actual
    filename ever appeared. The collapse must happen before truncation, not
    after -- this pins that ordering, not just that collapsing happens."""
    worktree = Path("/home/dev/.local/share/cosmo/work/deadbeef/scaffold-app")
    long_path = f"{worktree}/frontend/package-lock.json"
    payload = _tool_use_payload("Edit", {"file_path": long_path})

    line = describe_tool_call(payload, cwd=worktree)

    assert line == "Edit: ./frontend/package-lock.json"


def test_describe_tool_call_without_cwd_leaves_the_path_untouched() -> None:
    payload = _tool_use_payload("Edit", {"file_path": "/some/absolute/path.py"})

    assert describe_tool_call(payload) == "Edit: /some/absolute/path.py"


def test_describe_tool_call_returns_none_for_a_tool_result_only_event() -> None:
    block = {"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok"}
    payload = {"type": "user", "message": {"content": [block]}}

    assert describe_tool_call(payload) is None


def test_stream_reader_on_event_fires_for_every_classified_line_including_heartbeats() -> None:
    seen: list[ClassifiedEvent] = []
    reader = StreamReader(on_event=seen.append)
    raw = (FIXTURES / "tool_call.ndjson").read_bytes()

    reader.feed(raw)

    assert [e.kind for e in seen] == [e.kind for e in reader.events]
    assert len(seen) == 4  # system/init heartbeat, 2 tool-call turns, result
