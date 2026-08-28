"""`notify.watch` (v5 improvements plan part 3): forwards notification-worthy
`events` rows to a `Sink`, and raises its own staleness alert -- built and
tested against a fake in-memory `Sink`, never a real Telegram call."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from cosmo.config.model import NotifyConfig
from cosmo.events.emitter import EventEmitter
from cosmo.events.envelope import Event, EventType
from cosmo.notify.watch import WatchState, watch_once
from cosmo.store.enums import Severity
from cosmo.store.reader import latest_event_rowid
from cosmo.store.writer import StoreWriter


@dataclass
class _FakeSink:
    sent: list[Event] = field(default_factory=list)

    def send(self, event: Event) -> None:
        self.sent.append(event)


def _config(**overrides: object) -> NotifyConfig:
    base: dict[str, object] = {"enabled": True, "min_severity": Severity.WARNING}
    base.update(overrides)
    return NotifyConfig(**base)  # type: ignore[arg-type]


def test_a_warning_event_is_forwarded(tmp_path: Path) -> None:
    db_path = tmp_path / "cosmo.db"
    writer = StoreWriter(db_path)
    EventEmitter(writer).emit(
        event_type=EventType.TASK_BLOCKED, severity=Severity.WARNING, task_id="a"
    )
    sink = _FakeSink()
    state = WatchState(since_rowid=0, last_activity_monotonic=0.0)

    watch_once(db_path=db_path, sink=sink, config=_config(), state=state, now_monotonic=0.0)

    assert len(sink.sent) == 1
    assert sink.sent[0].event_type == EventType.TASK_BLOCKED.value
    writer.close()


def test_an_info_event_below_min_severity_is_not_forwarded(tmp_path: Path) -> None:
    db_path = tmp_path / "cosmo.db"
    writer = StoreWriter(db_path)
    EventEmitter(writer).emit(
        event_type=EventType.TASK_HEARTBEAT, severity=Severity.INFO, task_id="a"
    )
    sink = _FakeSink()
    state = WatchState(since_rowid=0, last_activity_monotonic=0.0)

    watch_once(db_path=db_path, sink=sink, config=_config(), state=state, now_monotonic=0.0)

    assert sink.sent == []
    writer.close()


def test_run_summary_is_always_forwarded_despite_info_severity(tmp_path: Path) -> None:
    db_path = tmp_path / "cosmo.db"
    writer = StoreWriter(db_path)
    EventEmitter(writer).emit(event_type=EventType.RUN_SUMMARY, severity=Severity.INFO, run_id="r1")
    sink = _FakeSink()
    state = WatchState(since_rowid=0, last_activity_monotonic=0.0)

    watch_once(db_path=db_path, sink=sink, config=_config(), state=state, now_monotonic=0.0)

    assert len(sink.sent) == 1
    assert sink.sent[0].event_type == EventType.RUN_SUMMARY.value
    writer.close()


def test_task_completed_is_always_forwarded_despite_info_severity(tmp_path: Path) -> None:
    db_path = tmp_path / "cosmo.db"
    writer = StoreWriter(db_path)
    EventEmitter(writer).emit(
        event_type=EventType.TASK_COMPLETED, severity=Severity.INFO, task_id="a"
    )
    sink = _FakeSink()
    state = WatchState(since_rowid=0, last_activity_monotonic=0.0)

    watch_once(db_path=db_path, sink=sink, config=_config(), state=state, now_monotonic=0.0)

    assert len(sink.sent) == 1
    assert sink.sent[0].event_type == EventType.TASK_COMPLETED.value
    writer.close()


def test_state_advances_past_already_seen_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "cosmo.db"
    writer = StoreWriter(db_path)
    emitter = EventEmitter(writer)
    emitter.emit(event_type=EventType.TASK_BLOCKED, severity=Severity.WARNING, task_id="a")
    since = latest_event_rowid(db_path)
    sink = _FakeSink()
    state = WatchState(since_rowid=since, last_activity_monotonic=0.0)

    watch_once(db_path=db_path, sink=sink, config=_config(), state=state, now_monotonic=0.0)

    assert sink.sent == []  # already-seen row must not be re-sent
    writer.close()


def test_new_activity_resets_the_staleness_clock(tmp_path: Path) -> None:
    db_path = tmp_path / "cosmo.db"
    writer = StoreWriter(db_path)
    emitter = EventEmitter(writer)
    emitter.emit(event_type=EventType.TASK_BLOCKED, severity=Severity.WARNING, task_id="a")
    sink = _FakeSink()
    state = WatchState(since_rowid=0, last_activity_monotonic=0.0, alerted_stale=True)

    watch_once(
        db_path=db_path,
        sink=sink,
        config=_config(stale_after_seconds=100),
        state=state,
        now_monotonic=50.0,
    )

    assert state.last_activity_monotonic == 50.0
    assert state.alerted_stale is False


def test_silence_past_the_threshold_raises_exactly_one_stale_alert(tmp_path: Path) -> None:
    db_path = tmp_path / "cosmo.db"
    StoreWriter(db_path).close()  # just needs the schema to exist
    sink = _FakeSink()
    state = WatchState(since_rowid=0, last_activity_monotonic=0.0)
    config = _config(stale_after_seconds=100)

    watch_once(db_path=db_path, sink=sink, config=config, state=state, now_monotonic=150.0)
    assert len(sink.sent) == 1
    assert sink.sent[0].event_type == "watch.stale"
    assert state.alerted_stale is True

    # A second poll, still silent, must not re-alert.
    watch_once(db_path=db_path, sink=sink, config=config, state=state, now_monotonic=200.0)
    assert len(sink.sent) == 1
