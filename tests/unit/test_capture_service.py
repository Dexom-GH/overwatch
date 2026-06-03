"""Host tests for the capture-stage service (#14).

The capture spine drives any ``CaptureSource`` and republishes its
``(Frame, Optional[DepthFrame])`` stream onto the bus as ``capture.frame`` /
``capture.depth``. The driver and the FPS/summary helpers are host-runnable and
tested here with a fake source + fake bus; only the live ``ZedSource`` (pyzed)
and the ``__main__`` demo that constructs it are target-only.
"""

import numpy as np
import pytest

from overwatch.bus import topics
from overwatch.bus.schemas import DepthFrame, Frame
from overwatch.capture.base import CaptureSource
from overwatch.capture.service import FpsMeter, FrameLogger, run_capture


def _frame(fid):
    img = np.zeros((4, 6, 3), dtype=np.uint8)
    return Frame(
        source_id="zed-0", frame_id=fid, timestamp=float(fid),
        image=img, width=6, height=4,
    )


def _depth(fid):
    return DepthFrame(
        source_id="zed-0", frame_id=fid, timestamp=float(fid),
        depth=np.zeros((4, 6), dtype=np.float32),
    )


class _ListSource(CaptureSource):
    """A CaptureSource that replays a fixed list of pairs; records open/close."""

    def __init__(self, pairs):
        self._pairs = pairs
        self.opened = False
        self.closed = False

    def open(self):
        self.opened = True

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


def test_run_capture_publishes_frame_then_depth_in_order():
    src = _ListSource([(_frame(1), _depth(1)), (_frame(2), _depth(2))])
    bus = _FakeBus()
    n = run_capture(src, bus)
    assert n == 2
    assert [t for t, _ in bus.published] == [
        topics.CAPTURE_FRAME, topics.CAPTURE_DEPTH,
        topics.CAPTURE_FRAME, topics.CAPTURE_DEPTH,
    ]
    assert src.opened and src.closed


def test_run_capture_omits_depth_when_none():
    src = _ListSource([(_frame(1), None)])
    bus = _FakeBus()
    n = run_capture(src, bus)
    assert n == 1
    assert [t for t, _ in bus.published] == [topics.CAPTURE_FRAME]


def test_run_capture_respects_max_frames():
    src = _ListSource([(_frame(i), _depth(i)) for i in range(10)])
    bus = _FakeBus()
    n = run_capture(src, bus, max_frames=3)
    assert n == 3
    assert len(bus.published) == 6  # 3 frames x (frame + depth)


def test_run_capture_closes_source_on_error():
    class _BoomSource(_ListSource):
        def frames(self):
            yield (_frame(1), None)
            raise RuntimeError("boom")

    src = _BoomSource([])
    bus = _FakeBus()
    with pytest.raises(RuntimeError):
        run_capture(src, bus)
    assert src.closed


def test_run_capture_preserves_grab_alignment():
    # RGB + depth from one ZED grab() must keep a shared frame_id/timestamp (AC2).
    src = _ListSource([(_frame(7), _depth(7))])
    bus = _FakeBus()
    run_capture(src, bus)
    (_, frame), (_, depth) = bus.published
    assert frame.frame_id == depth.frame_id == 7
    assert frame.timestamp == depth.timestamp


def test_fps_meter_estimates_rate():
    m = FpsMeter(window=10)
    for t in range(5):  # ticks 1s apart -> 1 fps
        m.tick(float(t))
    assert abs(m.fps() - 1.0) < 1e-6


def test_fps_meter_zero_until_two_samples():
    m = FpsMeter()
    assert m.fps() == 0.0
    m.tick(0.0)
    assert m.fps() == 0.0


def test_frame_logger_emits_id_shape_and_fps():
    out = []
    clock = iter([0.0, 1.0])
    logger = FrameLogger(clock=lambda: next(clock), sink=out.append)
    logger(_frame(1))
    logger(_frame(2))
    assert "frame 1" in out[0] and "6x4" in out[0]
    assert "fps=" in out[1]


def test_zed_source_is_target_only_on_host():
    # The import-guard convention: instantiating ZedSource on the host (no pyzed)
    # must raise loudly, while importing the module stays clean.
    from overwatch.capture.zed_source import ZedSource

    with pytest.raises(RuntimeError):
        ZedSource()
