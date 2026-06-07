"""Host tests for the dashboard feed producers (#132).

The cv2/network specifics are injected (``capture_factory`` / ``encode`` /
``render``), so the threaded read/reconnect/stop logic is tested without OpenCV
or a camera.
"""

import time

from overwatch.output.dashboard.feeds import MockFeeder, RtspFeeder
from overwatch.output.dashboard.frame_slot import FrameSlot


class _FakeCap:
    def __init__(self, frames, opened=True):
        self._frames = list(frames)
        self._i = 0
        self._opened = opened
        self.released = False

    def isOpened(self):
        return self._opened

    def read(self):
        if self._i < len(self._frames):
            f = self._frames[self._i]
            self._i += 1
            return True, f
        return False, None

    def release(self):
        self.released = True
        self._opened = False


def _wait_until(predicate, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline and not predicate():
        time.sleep(0.005)
    return predicate()


def test_rtsp_feeder_fills_slot_from_frames_then_ends_without_reconnect():
    slot = FrameSlot()
    cap = _FakeCap(["A", "B", "C"])
    feeder = RtspFeeder(
        slot, "rtsp://cam/s",
        reconnect=False,
        capture_factory=lambda: cap,
        encode=lambda img: ("J:" + img).encode(),
        sleep=lambda _s: None,
    )
    feeder.start()
    assert _wait_until(lambda: slot.latest()[1] >= 3)
    feeder.stop()
    assert slot.latest()[0] == b"J:C"
    assert cap.released


def test_rtsp_feeder_reconnects_after_a_failed_open():
    slot = FrameSlot()
    caps = [_FakeCap([], opened=False), _FakeCap(["X"])]

    def factory():
        return caps.pop(0) if caps else _FakeCap([], opened=False)

    feeder = RtspFeeder(
        slot, "rtsp://cam",
        capture_factory=factory,
        encode=lambda img: ("J:" + img).encode(),
        sleep=lambda _s: None,
    )
    feeder.start()
    assert _wait_until(lambda: slot.latest()[0] == b"J:X")
    feeder.stop()


def test_rtsp_feeder_splices_credentials_into_url():
    # No factory => the real _open() path; we only check the URL it would dial,
    # via inject_cred (no cv2 call here since we stop before opening).
    from overwatch.capture.rtsp_source import inject_cred

    assert inject_cred("rtsp://cam:554/s", "u:p") == "rtsp://u:p@cam:554/s"


def test_rtsp_feeder_stop_is_idempotent():
    feeder = RtspFeeder(
        FrameSlot(), "rtsp://cam",
        capture_factory=lambda: _FakeCap(["A"] * 10_000),
        encode=lambda _img: b"J",
        sleep=lambda _s: None,
    )
    feeder.start()
    feeder.stop()
    feeder.stop()  # no error, no hang


def test_mock_feeder_fills_slot_with_rendered_frames():
    slot = FrameSlot()
    feeder = MockFeeder(slot, fps=1000, render=lambda n: ("m%d" % n).encode(), sleep=lambda _s: None)
    feeder.start()
    assert _wait_until(lambda: slot.latest()[1] >= 3)
    feeder.stop()
    assert slot.latest()[0].startswith(b"m")
