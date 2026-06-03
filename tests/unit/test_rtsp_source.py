"""Host tests for the RTSP/IP capture source (#31).

``RtspSource`` is a depth-less ``CaptureSource``: it decodes an RTSP/file stream
and publishes ``(Frame, None)`` pairs (ADR-0006 capability split — mono feeds
carry no depth). The decode backend is OpenCV ``cv2.VideoCapture`` (GStreamer/
NVDEC on the Jetson, ffmpeg on the host), but the read/reconnect/EOF *logic* is
backend-agnostic and tested here with an injected fake capture — no network, no
real cv2 — so the whole source is host-runnable off-device.
"""

import numpy as np
import pytest

from overwatch.bus import topics
from overwatch.capture import rtsp_source as rtsp_mod
from overwatch.capture.base import CaptureSource
from overwatch.capture.rtsp_source import RtspSource, _inject_cred
from overwatch.capture.service import run_capture


class _FakeCap:
    """Mimics the slice of ``cv2.VideoCapture`` that ``RtspSource`` uses.

    ``results`` is a list of ``(ok, image)`` tuples returned by successive
    ``read()`` calls; once exhausted, ``read()`` returns ``(False, None)`` to
    model a stream that has gone away.
    """

    def __init__(self, results, opened=True):
        self._results = list(results)
        self._opened = opened
        self.released = 0

    def isOpened(self):  # noqa: N802 - matches cv2 API
        return self._opened

    def read(self):
        if self._results:
            return self._results.pop(0)
        return (False, None)

    def release(self):
        self.released += 1


def _img(h=4, w=6, c=3):
    return np.zeros((h, w, c), dtype=np.uint8)


def _factory(fake):
    """A capture factory that hands back the same stateful fake each call.

    Re-handing the same object models reconnect resuming the underlying stream;
    ``calls`` records how many times the source (re)opened a capture.
    """

    box = {"calls": 0}

    def make():
        box["calls"] += 1
        return fake

    return box, make


def test_is_a_capture_source():
    box, make = _factory(_FakeCap([]))
    src = RtspSource("cam-1", "rtsp://h/s", capture_factory=make)
    assert isinstance(src, CaptureSource)


def test_yields_frame_none_pairs_with_metadata():
    fake = _FakeCap([(True, _img()), (True, _img())])
    box, make = _factory(fake)
    clock = iter([10.0, 11.0])
    src = RtspSource(
        "cam-1", "rtsp://h/s", reconnect=False,
        capture_factory=make, clock=lambda: next(clock),
    )
    src.open()
    pairs = list(src.frames())

    assert [depth for _, depth in pairs] == [None, None]
    frames = [f for f, _ in pairs]
    assert [f.frame_id for f in frames] == [0, 1]          # monotonic from 0
    assert [f.source_id for f in frames] == ["cam-1", "cam-1"]
    assert [f.timestamp for f in frames] == [10.0, 11.0]   # from injected clock
    assert frames[0].width == 6 and frames[0].height == 4  # from image shape


def test_drops_alpha_to_bgr_hwc3():
    fake = _FakeCap([(True, _img(c=4))])  # BGRA in
    box, make = _factory(fake)
    src = RtspSource("cam-1", "rtsp://h/s", reconnect=False, capture_factory=make)
    src.open()
    frame, _ = next(iter(src.frames()))
    assert frame.image.shape == (4, 6, 3)  # alpha dropped to HxWx3


def test_clean_end_of_stream_returns_and_releases():
    fake = _FakeCap([(True, _img())])  # one frame, then read() -> (False, None)
    box, make = _factory(fake)
    src = RtspSource("cam-1", "rtsp://h/s", reconnect=False, capture_factory=make)
    src.open()
    frames = list(src.frames())  # returns cleanly at EOF, no hang
    assert len(frames) == 1
    src.close()
    assert fake.released >= 1


def test_run_capture_publishes_frame_only_no_depth():
    fake = _FakeCap([(True, _img()), (True, _img())])
    box, make = _factory(fake)
    src = RtspSource("cam-1", "rtsp://h/s", reconnect=False, capture_factory=make)

    class _FakeBus:
        def __init__(self):
            self.published = []

        def publish(self, topic, message):
            self.published.append((topic, message))

    bus = _FakeBus()
    n = run_capture(src, bus)
    assert n == 2
    assert [t for t, _ in bus.published] == [topics.CAPTURE_FRAME, topics.CAPTURE_FRAME]
    assert topics.CAPTURE_DEPTH not in [t for t, _ in bus.published]


def test_reconnects_on_transient_drop():
    # Good, drop, good — the source must recover across the gap and keep frame ids
    # monotonic, without sleeping for real (injected no-op sleep).
    fake = _FakeCap([(True, _img()), (False, None), (True, _img())])
    box, make = _factory(fake)
    sleeps = []
    src = RtspSource(
        "cam-1", "rtsp://h/s", reconnect=True, max_reconnects=2,
        capture_factory=make, sleep=sleeps.append,
    )
    src.open()
    frames = [f for f, _ in src.frames()]
    assert [f.frame_id for f in frames] == [0, 1]  # both good frames, across the drop
    assert box["calls"] > 1                         # at least one reopen happened
    assert sleeps                                   # backed off at least once


def test_gives_up_after_max_reconnects_no_infinite_loop():
    fake = _FakeCap([])  # read() always (False, None)
    box, make = _factory(fake)
    sleeps = []
    src = RtspSource(
        "cam-1", "rtsp://h/s", reconnect=True, max_reconnects=2,
        capture_factory=make, sleep=sleeps.append,
    )
    src.open()
    frames = list(src.frames())  # must terminate, not spin forever
    assert frames == []
    assert len(sleeps) == 2  # exactly max_reconnects backoff attempts, then give up


def test_open_raises_when_stream_not_opened():
    fake = _FakeCap([], opened=False)
    box, make = _factory(fake)
    src = RtspSource("cam-1", "rtsp://bad/s", capture_factory=make)
    with pytest.raises(RuntimeError):
        src.open()


def test_frames_requires_open_first():
    box, make = _factory(_FakeCap([]))
    src = RtspSource("cam-1", "rtsp://h/s", capture_factory=make)
    with pytest.raises(RuntimeError):
        next(iter(src.frames()))


def test_repr_redacts_credentials():
    src = RtspSource("cam-1", "rtsp://h:554/s", cred="user:s3cr3t")
    text = repr(src)
    assert "s3cr3t" not in text
    assert "cam-1" in text


def test_inject_cred_builds_userinfo_url():
    assert _inject_cred("rtsp://h:554/s", "user:pass") == "rtsp://user:pass@h:554/s"
    assert _inject_cred("rtsp://h:554/s", None) == "rtsp://h:554/s"  # no cred -> unchanged


def test_cv2_guard_raises_when_unavailable(monkeypatch):
    # With no injected factory and cv2 absent, opening must fail loudly (AC7):
    # importing the module stayed clean; only real use needs OpenCV.
    monkeypatch.setattr(rtsp_mod, "cv2", None)
    src = RtspSource("cam-1", "rtsp://h/s")  # no capture_factory -> default cv2 path
    with pytest.raises(RuntimeError, match="(?i)opencv"):
        src.open()
