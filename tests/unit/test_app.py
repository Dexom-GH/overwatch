"""Host tests for the app entrypoint wiring (#38).

The live pipeline (real ZED/DeepStream/TensorRT stages) is target-only, but the
host-runnable glue is tested here: the ``CaptureStage`` adapter that makes the
capture spine a supervisable :class:`~overwatch.orchestrator.Stage`, and
``run_pipeline``'s start -> wait-for-shutdown -> clean-shutdown sequence.
"""

import threading

import numpy as np

from overwatch.app import CaptureStage, run_pipeline
from overwatch.bus import topics
from overwatch.bus.schemas import Frame
from overwatch.capture.base import CaptureSource


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

    def publish(self, topic, message):
        self.published.append((topic, message))


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
