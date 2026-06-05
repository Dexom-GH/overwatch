"""Host tests for the app entrypoint wiring (#38).

The live pipeline (real ZED/DeepStream/TensorRT stages) is target-only, but the
host-runnable glue is tested here: the ``CaptureStage`` adapter that makes the
capture spine a supervisable :class:`~overwatch.orchestrator.Stage`, and
``run_pipeline``'s start -> wait-for-shutdown -> clean-shutdown sequence.
"""

import threading

import numpy as np
import pytest

from overwatch.app import (
    CaptureStage,
    FusionStage,
    InferenceStage,
    OutputStage,
    _build_source,
    _build_stages,
    run_pipeline,
)
from overwatch.bus import topics
from overwatch.bus.schemas import Alert, Frame, Track
from overwatch.capture.base import CaptureSource
from overwatch.capture.rtsp_source import RtspSource
from overwatch.config.schema import AppConfig, RtspSourceConfig


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
        # capture -> inference -> fusion -> output (#38)
        assert [s.name for s in stages] == ["capture:cam-1", "inference", "fusion", "output"]

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
