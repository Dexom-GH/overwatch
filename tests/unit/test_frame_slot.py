"""Host tests for the live-feed FrameSlot (#120, ADR-0008).

The slot is the in-process hand-off between the DeepStream pipeline and the
dashboard MJPEG stream (frames stay off the bus). Pure threading — host-runnable.
"""

import threading
import time

from overwatch.output.dashboard.frame_slot import FrameSlot


def test_latest_is_empty_before_any_frame():
    assert FrameSlot().latest() == (None, 0)


def test_put_then_latest_returns_frame_and_bumps_seq():
    slot = FrameSlot()
    slot.put(b"f1")
    assert slot.latest() == (b"f1", 1)
    slot.put(b"f2")
    assert slot.latest() == (b"f2", 2)  # overwrites — latest-frame, no backlog


def test_wait_for_returns_current_immediately_with_negative_last_seq():
    slot = FrameSlot()
    slot.put(b"f1")
    frame, seq = slot.wait_for(-1, timeout=1.0)
    assert frame == b"f1" and seq == 1


def test_wait_for_times_out_when_no_fresher_frame():
    slot = FrameSlot()
    slot.put(b"f1")
    start = time.monotonic()
    frame, seq = slot.wait_for(1, timeout=0.1)  # already seen seq 1
    assert (frame, seq) == (b"f1", 1)  # same seq => caller should treat as "no new frame"
    # it blocked (didn't return instantly); allow generous OS timer slop, no upper bound
    assert time.monotonic() - start >= 0.03


def test_wait_for_wakes_on_new_frame_from_another_thread():
    slot = FrameSlot()

    def producer():
        time.sleep(0.05)
        slot.put(b"fresh")

    threading.Thread(target=producer, daemon=True).start()
    frame, seq = slot.wait_for(0, timeout=2.0)  # blocks until producer puts
    assert frame == b"fresh" and seq == 1
