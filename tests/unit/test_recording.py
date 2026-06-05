"""Host tests for the record/replay harness (#11).

The harness persists ``Frame``/``DepthFrame`` to a framed log using the *same*
bus codec that crosses the wire, so a recording round-trips exactly and
``ReplaySource`` is a drop-in ``CaptureSource`` for offline iteration. Recording
from a *live* ZED is target-only and deferred to the device; here we record
synthetic frames, which exercises every code path except the camera grab.
"""

import numpy as np
import pytest

from overwatch.bus import topics
from overwatch.bus.schemas import DepthFrame, Frame
from overwatch.capture.base import CaptureSource
from overwatch.capture.recording import (
    FrameRecorder,
    RecordingError,
    ReplaySource,
    replay_to_bus,
)


def _frame(fid, val=1):
    img = np.full((4, 6, 3), val, dtype=np.uint8)
    return Frame(source_id="zed-0", frame_id=fid, timestamp=100.0 + fid,
                 image=img, width=6, height=4)


def _depth(fid, val=2.0):
    arr = np.full((4, 6), val, dtype=np.float32)
    return DepthFrame(source_id="zed-0", frame_id=fid, timestamp=100.0 + fid, depth=arr)


def _record(path, pairs):
    with FrameRecorder(path) as rec:
        for f, d in pairs:
            rec.record(f, d)


def _replay(path):
    src = ReplaySource(str(path))
    src.open()
    try:
        return list(src.frames())
    finally:
        src.close()


def test_round_trip_rgb_and_depth_preserves_everything(tmp_path):
    path = tmp_path / "clip.owrec"
    pairs = [(_frame(i, val=i), _depth(i, val=float(i))) for i in (1, 2, 3)]
    _record(path, pairs)

    out = _replay(path)
    assert [f.frame_id for f, _ in out] == [1, 2, 3]
    for (rf, rd), (of, od) in zip(out, pairs):
        assert rf.frame_id == of.frame_id
        assert rf.timestamp == of.timestamp
        assert rf.source_id == of.source_id
        np.testing.assert_array_equal(rf.image, of.image)
        assert rd is not None and od is not None
        assert rd.frame_id == od.frame_id
        np.testing.assert_array_equal(rd.depth, od.depth)


def test_replay_source_is_a_capture_source(tmp_path):
    path = tmp_path / "clip.owrec"
    _record(path, [(_frame(1), _depth(1))])
    src = ReplaySource(str(path))
    assert isinstance(src, CaptureSource)


def test_depth_optional_yields_none(tmp_path):
    path = tmp_path / "clip.owrec"
    _record(path, [(_frame(1), None), (_frame(2), _depth(2))])
    out = _replay(path)
    assert out[0][1] is None
    assert out[1][1] is not None and out[1][1].frame_id == 2


def test_empty_recording_yields_nothing(tmp_path):
    path = tmp_path / "clip.owrec"
    _record(path, [])
    assert _replay(path) == []


def test_order_is_preserved(tmp_path):
    path = tmp_path / "clip.owrec"
    _record(path, [(_frame(i), _depth(i)) for i in (5, 1, 9, 3)])
    assert [f.frame_id for f, _ in _replay(path)] == [5, 1, 9, 3]


def test_bad_magic_raises(tmp_path):
    path = tmp_path / "bad.owrec"
    path.write_bytes(b"NOPE\x00\x00garbage")
    src = ReplaySource(str(path))
    src.open()
    with pytest.raises(RecordingError):
        list(src.frames())


def test_truncated_record_raises(tmp_path):
    path = tmp_path / "clip.owrec"
    _record(path, [(_frame(1), _depth(1))])
    data = path.read_bytes()
    path.write_bytes(data[:-5])  # chop the tail of the last frame
    src = ReplaySource(str(path))
    src.open()
    with pytest.raises(RecordingError):
        list(src.frames())


def test_recorder_rejects_non_schema(tmp_path):
    path = tmp_path / "clip.owrec"
    with FrameRecorder(path) as rec:
        with pytest.raises(RecordingError):
            rec.record({"not": "a frame"})  # type: ignore[arg-type]


class _FakeBus:
    def __init__(self):
        self.published = []

    def publish(self, topic, message):
        self.published.append((topic, message))


def test_replay_to_bus_publishes_both_topics_in_order(tmp_path):
    path = tmp_path / "clip.owrec"
    _record(path, [(_frame(1), _depth(1)), (_frame(2), None)])
    bus = _FakeBus()
    n = replay_to_bus(str(path), bus)

    assert n == 3  # frame, depth, frame
    assert [t for t, _ in bus.published] == [
        topics.CAPTURE_FRAME,
        topics.CAPTURE_DEPTH,
        topics.CAPTURE_FRAME,
    ]
    assert bus.published[0][1].frame_id == 1
    assert bus.published[1][1].frame_id == 1
    assert bus.published[2][1].frame_id == 2


def test_replay_over_real_zeromq_bus_delivers_synced_frames(tmp_path):
    # End-to-end over the real transport (not a fake bus): a replayed clip flows
    # through a live in-proc ZeroMqBus to a downstream subscriber, host-side, with
    # frame ids / timestamps / depth preserved. This is the offline-iteration path
    # a host consumer (e.g. a fusion harness) would attach to.
    import threading

    from overwatch.bus.zeromq_bus import ZeroMqBus

    path = tmp_path / "clip.owrec"
    _record(path, [(_frame(1), _depth(1)), (_frame(2), None)])

    received = []
    done = threading.Event()
    lock = threading.Lock()

    def _collect(topic):
        def _handler(msg):
            with lock:
                received.append((topic, msg))
                if len(received) == 3:  # frame, depth, frame
                    done.set()
        return _handler

    bus = ZeroMqBus(endpoint="inproc://test-replay")
    bus.subscribe(topics.CAPTURE_FRAME, _collect(topics.CAPTURE_FRAME))
    bus.subscribe(topics.CAPTURE_DEPTH, _collect(topics.CAPTURE_DEPTH))
    bus.start()
    try:
        n = replay_to_bus(str(path), bus)
        assert n == 3
        assert done.wait(timeout=5.0), "replayed frames were not all delivered"
    finally:
        bus.close()

    with lock:
        topics_seen = [t for t, _ in received]
        by_topic = {}
        for t, m in received:
            by_topic.setdefault(t, []).append(m)
    assert topics_seen.count(topics.CAPTURE_FRAME) == 2
    assert topics_seen.count(topics.CAPTURE_DEPTH) == 1
    frames = sorted(by_topic[topics.CAPTURE_FRAME], key=lambda f: f.frame_id)
    assert [f.frame_id for f in frames] == [1, 2]
    assert frames[0].timestamp == 101.0  # _frame(1): 100.0 + 1, ids/timestamps synced
    depth = by_topic[topics.CAPTURE_DEPTH][0]
    assert depth.frame_id == 1
    np.testing.assert_array_equal(depth.depth, _depth(1).depth)
