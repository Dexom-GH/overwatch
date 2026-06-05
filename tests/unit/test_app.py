"""Host tests for the app entrypoint wiring (#38).

The live pipeline (real ZED/DeepStream/TensorRT stages) is target-only, but the
host-runnable glue is tested here: the ``CaptureStage`` adapter that makes the
capture spine a supervisable :class:`~overwatch.orchestrator.Stage`, and
``run_pipeline``'s start -> wait-for-shutdown -> clean-shutdown sequence.
"""

import threading
import time

import numpy as np
import pytest

from overwatch.app import (
    CaptureStage,
    DashboardStage,
    FusionStage,
    InferenceStage,
    OutputStage,
    RetentionStage,
    StoreStage,
    _build_source,
    _build_stages,
    run_pipeline,
)
from overwatch.bus import topics
from overwatch.bus.schemas import Alert, Event, Frame, HealthSignal, Track, ZoneCount
from overwatch.capture.base import CaptureSource
from overwatch.capture.rtsp_source import RtspSource
from overwatch.config.schema import AppConfig, RtspSourceConfig, Zone
from overwatch.output.retention import RetentionPolicy
from overwatch.output.sqlite_store import SqliteEventStore


def _frame(fid):
    return Frame(
        source_id="zed-0", frame_id=fid, timestamp=float(fid),
        image=np.zeros((2, 2, 3), dtype=np.uint8), width=2, height=2,
    )


class _ListSource(CaptureSource):
    def __init__(self, pairs):
        self._pairs = pairs
        self.closed = False

    def open(self):
        pass

    def frames(self):
        for pair in self._pairs:
            yield pair

    def close(self):
        self.closed = True


class _FakeBus:
    def __init__(self):
        self.published = []
        self.handlers = {}

    def publish(self, topic, message):
        self.published.append((topic, message))

    def subscribe(self, topic, handler):
        self.handlers.setdefault(topic, []).append(handler)

    def deliver(self, topic, message):  # test helper: simulate bus delivery
        for h in self.handlers.get(topic, []):
            h(message)


class _SyncBus:
    """A bus that delivers synchronously on publish — for deterministic wiring tests."""

    def __init__(self):
        self.handlers = {}

    def publish(self, topic, message):
        for h in self.handlers.get(topic, []):
            h(message)

    def subscribe(self, topic, handler):
        self.handlers.setdefault(topic, []).append(handler)

    def start(self):
        pass

    def close(self):
        pass


def _full_cfg(source_id="cam-1"):
    """A validated AppConfig with an RTSP source + one fence (host-constructible)."""
    return AppConfig.model_validate(
        {
            "bus": {"transport": "zeromq", "endpoint": "inproc://test"},
            "capture": {"sources": [
                {"type": "rtsp", "source_id": source_id, "url": "rtsp://h/s", "fps": 10}
            ]},
            "inference": {
                "detector_config": "nvinfer.txt", "tracker_config": "nvtracker.txt",
                "reid": {"engine": "e.engine", "refresh_seconds": 30, "min_crop_confidence": 0.5},
            },
            "fusion": {
                "zones": [],
                "fences": [{"name": "gate", "line": [[0, 10], [20, 10]], "space": "image"}],
                "health": {"immobility_seconds": 600, "lameness_score_threshold": 0.6},
                "events": {"fence_zones": []},
            },
            "output": {
                "slack": {"webhook_env": "SLACK_WEBHOOK", "min_severity": "warning"},
                "store": {"backend": "sqlite", "path": "data/x.db"},
            },
        }
    )


def _cfg_with_zone():
    """Like _full_cfg but with a counting zone covering (0,0)-(40,40) (#111 tests)."""
    cfg = _full_cfg()
    cfg.fusion.zones = [
        Zone(name="pen", polygon=[(0.0, 0.0), (40.0, 0.0), (40.0, 40.0), (0.0, 40.0)],
             space="image")
    ]
    return cfg


def _track(track_id, cx, cy, frame_id):
    return Track(track_id=track_id, frame_id=frame_id, bbox=(cx - 1, cy - 1, cx + 1, cy + 1),
                 class_id=0, class_name="sheep", confidence=0.9)


class TestCaptureStage:
    def test_name_is_capture(self):
        stage = CaptureStage(_ListSource([]), _FakeBus())
        assert stage.name == "capture"

    def test_run_publishes_frames_until_stop(self):
        src = _ListSource([(_frame(1), None), (_frame(2), None)])
        bus = _FakeBus()
        CaptureStage(src, bus).run(threading.Event())
        assert [t for t, _ in bus.published] == [topics.CAPTURE_FRAME, topics.CAPTURE_FRAME]
        assert src.closed

    def test_run_returns_immediately_when_already_stopped(self):
        src = _ListSource([(_frame(1), None)])
        bus = _FakeBus()
        stop = threading.Event()
        stop.set()
        CaptureStage(src, bus).run(stop)
        assert bus.published == []


class TestBuildStages:
    """The source factory dispatches typed config to concrete CaptureSources (#31)."""

    def _rtsp(self, source_id, url="rtsp://cam/stream", fps=10):
        return RtspSourceConfig(type="rtsp", source_id=source_id, url=url, fps=fps)

    def test_build_source_rtsp(self):
        src = _build_source(self._rtsp("cam-1"))
        assert isinstance(src, RtspSource)

    def test_build_source_unknown_type_raises(self):
        from types import SimpleNamespace

        with pytest.raises(ValueError, match="unknown capture source type"):
            _build_source(SimpleNamespace(type="bogus"))

    def test_build_stages_wires_full_pipeline_in_order(self):
        stages = _build_stages(_full_cfg(), _FakeBus())
        # capture -> inference -> fusion -> output (#38) + store sink (#108)
        # + retention sweeper (#106) + dashboard (#110)
        assert [s.name for s in stages] == [
            "capture:cam-1", "inference", "fusion", "output", "store", "retention",
            "dashboard",
        ]

    def test_build_stages_appends_durable_tier_stages_for_sqlite(self):
        # Store/retention/dashboard are wired from output.store; constructing them
        # must NOT open the DB or bind a port (lazy in run()) — no side effect.
        stages = _build_stages(_full_cfg(), _FakeBus())
        assert isinstance(stages[-3], StoreStage)
        assert isinstance(stages[-2], RetentionStage)
        assert isinstance(stages[-1], DashboardStage)

    def test_build_stages_omits_dashboard_when_disabled(self):
        cfg = _full_cfg()
        cfg.output.dashboard.enabled = False
        names = [s.name for s in _build_stages(cfg, _FakeBus())]
        assert "dashboard" not in names

    def test_build_stages_feeds_rtsp_url_to_inference(self):
        # #84: the live RTSP URL must reach the DeepStream InferenceStage as its
        # source so nvurisrcbin can ingest it (not the bare source_id).
        stages = _build_stages(_full_cfg(), _FakeBus())
        inference = stages[1]
        assert inference._source == "rtsp://h/s"

    def test_build_stages_passes_detector_labels_to_inference(self, tmp_path):
        # #91: class labels resolved from the detector config reach the
        # InferenceStage so Track.class_name is a name, not a numeric id.
        (tmp_path / "labels.txt").write_text("person\ncar\n", encoding="utf-8")
        pgie = tmp_path / "pgie.txt"
        pgie.write_text("[property]\nlabelfile-path=labels.txt\n", encoding="utf-8")
        cfg = _full_cfg()
        cfg.inference.detector_config = str(pgie)
        stages = _build_stages(cfg, _FakeBus())
        assert stages[1]._labels == ["person", "car"]

    def test_build_stages_tolerates_missing_labelfile(self):
        # A detector config without a readable labelfile -> labels None, no crash.
        stages = _build_stages(_full_cfg(), _FakeBus())  # detector_config "nvinfer.txt"
        assert stages[1]._labels is None


class TestFusionStage:
    def test_subscribes_to_infer_track_and_publishes_alerts(self):
        bus = _FakeBus()
        stage = FusionStage(bus, _full_cfg())
        assert topics.INFER_TRACK in bus.handlers  # subscribed in __init__
        # a track crossing the configured fence (below -> above) -> an alert
        bus.deliver(topics.INFER_TRACK, _track(1, 10, 5, frame_id=0))
        bus.deliver(topics.INFER_TRACK, _track(1, 10, 15, frame_id=1))
        stop = threading.Event()
        stop.set()
        stage.run(stop)  # flushes the trailing frame
        alert_topics = [t for t, _ in bus.published]
        assert topics.OUTPUT_ALERT in alert_topics

    def test_publishes_raw_records_on_their_fusion_topics(self):
        # #111: the raw monitoring records reach the durable topics, not just alerts.
        bus = _FakeBus()
        stage = FusionStage(bus, _full_cfg())
        stage._publish_record(ZoneCount(zone_id="pen", timestamp=1.0, count=3))
        stage._publish_record(HealthSignal(track_id=1, timestamp=2.0, kind="immobility", score=1.0))
        stage._publish_record(Event(timestamp=3.0, kind="fence_crossing"))
        by_topic = {t for t, _ in bus.published}
        assert {topics.FUSION_COUNT, topics.FUSION_HEALTH, topics.FUSION_EVENT} <= by_topic

    def test_publishes_fusion_event_on_crossing(self):
        bus = _FakeBus()
        stage = FusionStage(bus, _full_cfg())
        bus.deliver(topics.INFER_TRACK, _track(1, 10, 5, frame_id=0))
        bus.deliver(topics.INFER_TRACK, _track(1, 10, 15, frame_id=1))
        stop = threading.Event()
        stop.set()
        stage.run(stop)  # flush -> crossing event published
        assert topics.FUSION_EVENT in [t for t, _ in bus.published]

    def test_end_to_end_fusion_to_store_to_dashboard(self):
        # #111 payoff: tracks -> fusion publishes raw records -> StoreStage persists
        # -> the dashboard's zone-count + event panels populate. Synchronous bus,
        # so this is deterministic (no threads/timing).
        from overwatch.output.dashboard.view import latest_zone_counts

        bus = _SyncBus()
        store = SqliteEventStore(":memory:")
        FusionStage(bus, _cfg_with_zone())   # publishes fusion.count/event + output.alert
        StoreStage(bus, store=store)         # persists them
        # track in the pen zone, crossing the fence (below -> above)
        bus.publish(topics.INFER_TRACK, _track(1, 10, 5, frame_id=0))
        bus.publish(topics.INFER_TRACK, _track(1, 10, 15, frame_id=1))
        bus.publish(topics.INFER_TRACK, _track(1, 10, 15, frame_id=2))  # flush frame 1
        counts = latest_zone_counts(store, end=1e9)
        assert counts and counts[0].zone_id == "pen" and counts[0].count >= 1
        events = list(store.query("event", 0.0, 1e9))
        assert any(e.kind == "fence_crossing" for e in events)


class TestOutputStage:
    def test_subscribes_to_output_alert_and_delivers_via_sink(self):
        bus = _FakeBus()
        delivered = []

        class _Sink:
            def send(self, alert):
                delivered.append(alert)

        OutputStage(bus, _full_cfg(), sink=_Sink())
        assert topics.OUTPUT_ALERT in bus.handlers
        alert = Alert(timestamp=1.0, severity="warning", title="t", message="m")
        bus.deliver(topics.OUTPUT_ALERT, alert)
        assert delivered == [alert]

    def test_run_returns_on_stop(self):
        bus = _FakeBus()
        stage = OutputStage(bus, _full_cfg(), sink=type("S", (), {"send": lambda self, a: None})())
        stop = threading.Event()
        stop.set()
        stage.run(stop)  # returns immediately when already stopped


class TestInferenceStage:
    def test_name_and_host_construction(self):
        # __init__ must not touch gi/pyds (deferred to run) so it builds on host.
        stage = InferenceStage(_FakeBus(), pgie_config="p.txt", source="rtsp://h/s")
        assert stage.name == "inference"


class TestStoreStage:
    def test_name_is_store(self):
        assert StoreStage(_FakeBus(), store=SqliteEventStore(":memory:")).name == "store"

    def test_persists_alert_and_its_source_event(self):
        bus = _FakeBus()
        store = SqliteEventStore(":memory:")
        StoreStage(bus, store=store)
        assert topics.OUTPUT_ALERT in bus.handlers  # subscribed in __init__
        event = Event(timestamp=5.0, kind="fence_crossing", track_id=7, zone_id="gate")
        bus.deliver(topics.OUTPUT_ALERT, Alert(
            timestamp=5.0, severity="warning", title="Fence crossing", message="m",
            source_event=event,
        ))
        assert len(list(store.query("alert", 0.0, 100.0))) == 1
        events = list(store.query("event", 0.0, 100.0))
        assert len(events) == 1 and events[0].kind == "fence_crossing"

    def test_alert_without_source_event_records_only_the_alert(self):
        bus = _FakeBus()
        store = SqliteEventStore(":memory:")
        StoreStage(bus, store=store)
        bus.deliver(topics.OUTPUT_ALERT, Alert(
            timestamp=1.0, severity="info", title="t", message="m",
        ))
        assert len(list(store.query("alert", 0.0, 100.0))) == 1
        assert list(store.query("event", 0.0, 100.0)) == []

    def test_persists_raw_fusion_records_when_published(self):
        # Forward-compatible: the sink already subscribes the fusion record topics,
        # so once fusion publishes them (separate issue) they are persisted with no
        # further sink change.
        bus = _FakeBus()
        store = SqliteEventStore(":memory:")
        StoreStage(bus, store=store)
        bus.deliver(topics.FUSION_COUNT, ZoneCount(zone_id="pen-A", timestamp=2.0, count=4))
        bus.deliver(topics.FUSION_HEALTH, HealthSignal(track_id=1, timestamp=3.0, kind="immobility", score=1.0))
        bus.deliver(topics.FUSION_EVENT, Event(timestamp=4.0, kind="fence_crossing"))
        assert len(list(store.query("zone_count", 0.0, 100.0))) == 1
        assert len(list(store.query("health_signal", 0.0, 100.0))) == 1
        assert len(list(store.query("event", 0.0, 100.0))) == 1


class _FakeServer:
    """Stands in for an http.server.HTTPServer — serve_forever blocks until shutdown."""

    def __init__(self):
        self._stopped = threading.Event()
        self.serve_called = False
        self.shutdown_called = False
        self.closed = False

    def serve_forever(self):
        self.serve_called = True
        self._stopped.wait()

    def shutdown(self):
        self.shutdown_called = True
        self._stopped.set()

    def server_close(self):
        self.closed = True


class TestDashboardStage:
    def test_name(self):
        assert DashboardStage(_full_cfg(), server=_FakeServer()).name == "dashboard"

    def test_serves_then_stops_clean(self):
        srv = _FakeServer()
        stage = DashboardStage(_full_cfg(), server=srv)
        stop = threading.Event()
        t = threading.Thread(target=stage.run, args=(stop,), daemon=True)
        t.start()
        try:
            assert _wait_until(lambda: srv.serve_called, 5.0)
        finally:
            stop.set()
            t.join(timeout=5.0)
        assert not t.is_alive()  # clean shutdown
        assert srv.shutdown_called and srv.closed

    def test_real_server_serves_dashboard_then_stops(self):
        import http.client

        from overwatch.output.dashboard.server import make_server

        store = SqliteEventStore(":memory:")
        store.record(Alert(timestamp=1.0, severity="warning", title="Penned", message="m"))
        server = make_server(store, host="127.0.0.1", port=0, now=lambda: 100.0)
        host, port = server.server_address
        stage = DashboardStage(_full_cfg(), server=server)
        stop = threading.Event()
        t = threading.Thread(target=stage.run, args=(stop,), daemon=True)
        t.start()

        def _get_ok():
            try:
                conn = http.client.HTTPConnection(host, port, timeout=2)
                conn.request("GET", "/")
                body = conn.getresponse().read().decode("utf-8")
                conn.close()
                return "Penned" in body
            except OSError:
                return False

        try:
            assert _wait_until(_get_ok, 5.0), "dashboard did not serve the seeded store"
        finally:
            stop.set()
            t.join(timeout=5.0)
        assert not t.is_alive()


def _wait_until(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class TestRetentionStage:
    def test_enforces_policy_periodically_then_stops_clean(self):
        store = SqliteEventStore(":memory:")
        for ts in (10.0, 20.0, 30.0):
            store.record(Alert(timestamp=ts, severity="info", title="t", message="m"))
        # row cap = 1 -> a sweep prunes down to the newest record.
        stage = RetentionStage(
            RetentionPolicy(max_count=1), interval_seconds=0.01, store=store,
            now=lambda: 100.0,
        )
        assert stage.name == "retention"
        stop = threading.Event()
        t = threading.Thread(target=stage.run, args=(stop,), daemon=True)
        t.start()
        try:
            pruned = _wait_until(
                lambda: len(list(store.query("alert", 0.0, 200.0))) == 1, timeout=5.0
            )
            assert pruned, "retention stage did not enforce the policy"
        finally:
            stop.set()
            t.join(timeout=5.0)
        assert not t.is_alive()  # clean shutdown, no leaked thread

    def test_returns_immediately_when_already_stopped(self):
        store = SqliteEventStore(":memory:")
        for ts in (10.0, 20.0):
            store.record(Alert(timestamp=ts, severity="info", title="t", message="m"))
        stage = RetentionStage(
            RetentionPolicy(max_count=1), interval_seconds=1000.0, store=store,
            now=lambda: 100.0,
        )
        stop = threading.Event()
        stop.set()
        stage.run(stop)  # wait() returns at once -> no sweep
        assert len(list(store.query("alert", 0.0, 200.0))) == 2  # untouched


class _RecordingSupervisor:
    """Stands in for a Supervisor; records lifecycle calls in order."""

    def __init__(self):
        self.calls = []

    def start(self):
        self.calls.append("start")

    def shutdown(self, timeout=5.0):
        self.calls.append("shutdown")


class TestRunPipeline:
    def test_starts_then_shuts_down_when_shutdown_requested(self):
        sup = _RecordingSupervisor()
        shutdown = threading.Event()
        shutdown.set()  # shutdown already requested -> wait() returns at once
        run_pipeline(sup, install_signals=False, shutdown_event=shutdown)
        assert sup.calls == ["start", "shutdown"]

    def test_shuts_down_even_if_wait_is_interrupted(self):
        # A supervisor whose start() raises must not leave without a shutdown
        # attempt is out of scope; here we assert shutdown runs after a normal wake.
        sup = _RecordingSupervisor()
        shutdown = threading.Event()

        def wake():
            shutdown.set()

        timer = threading.Timer(0.05, wake)
        timer.start()
        run_pipeline(sup, install_signals=False, shutdown_event=shutdown)
        timer.cancel()
        assert sup.calls == ["start", "shutdown"]
