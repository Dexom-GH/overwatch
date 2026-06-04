"""Host tests for the mono end-to-end fusion glue (#79).

``FrameAssembler`` reassembles per-frame ``Track`` lists from the per-object
``infer.track`` stream (#15 publishes one ``Track`` per object). ``MonoAlertFanout``
feeds each completed frame to the merged fusion consumers (fence #20, immobility
#19, count #33) and emits the resulting ``Alert``s to an injected sink. Both are
pure host logic — the live pyds pipeline is target-only, but the
stream->frames->rules->alert glue is unit-tested here.
"""

from overwatch.bus.schemas import Alert, Track
from overwatch.config.schema import FenceLine, Zone
from overwatch.fusion.mono_alerts import FrameAssembler, MonoAlertFanout


def _track(track_id, cx, cy, frame_id):
    return Track(
        track_id=track_id, frame_id=frame_id, bbox=(cx - 1, cy - 1, cx + 1, cy + 1),
        class_id=0, class_name="sheep", confidence=0.9,
    )


# -- FrameAssembler ---------------------------------------------------------

def test_assembler_flushes_a_frame_when_the_next_frame_starts():
    frames = []
    fa = FrameAssembler(lambda ts, tracks: frames.append((ts, tracks)), clock=lambda: 1.0)
    fa.add(_track(1, 5, 5, frame_id=0))
    fa.add(_track(2, 6, 6, frame_id=0))
    assert frames == []  # frame 0 not flushed until a later frame arrives
    fa.add(_track(1, 5, 6, frame_id=1))
    assert len(frames) == 1
    ts, tracks = frames[0]
    assert ts == 1.0
    assert [t.track_id for t in tracks] == [1, 2]  # the two frame-0 tracks


def test_assembler_final_flush_emits_trailing_frame():
    frames = []
    fa = FrameAssembler(lambda ts, tracks: frames.append((ts, tracks)), clock=lambda: 2.0)
    fa.add(_track(1, 5, 5, frame_id=7))
    fa.flush()
    assert len(frames) == 1 and frames[0][0] == 2.0
    assert [t.frame_id for t in frames[0][1]] == [7]
    fa.flush()  # idempotent: nothing buffered
    assert len(frames) == 1


def test_assembler_handles_multiple_frames_in_order():
    seen = []
    fa = FrameAssembler(lambda ts, tracks: seen.append(len(tracks)), clock=lambda: 0.0)
    for fid in range(3):
        fa.add(_track(1, 5, 5, fid))
        fa.add(_track(2, 6, 6, fid))
    fa.flush()
    assert seen == [2, 2, 2]  # three frames, two tracks each


# -- MonoAlertFanout --------------------------------------------------------

def _fanout(sink, **kw):
    fence = FenceLine(name="gate", line=[(0.0, 10.0), (20.0, 10.0)], space="image")
    zone = Zone(name="pen", polygon=[(0.0, 0.0), (40.0, 0.0), (40.0, 40.0), (0.0, 40.0)],
                space="image", source_id="cam-1")
    return MonoAlertFanout(
        sink, fences=[fence], zones=[zone],
        zone_thresholds={"pen": 2}, immobility_seconds=kw.get("imm", 5.0),
        move_threshold_px=2.0, clock=kw.get("clock", lambda: 0.0),
    )


def test_fanout_emits_fence_crossing_alert():
    alerts = []
    fan = _fanout(alerts.append)
    fan.on_track(_track(1, 10, 5, frame_id=0))   # below the fence
    fan.on_track(_track(1, 10, 15, frame_id=1))  # above -> crossing on flush of frame 1
    fan.flush()
    kinds = [a.source_event.kind for a in alerts if a.source_event]
    assert "fence_crossing" in kinds


def test_fanout_emits_zone_count_alert_over_threshold():
    alerts = []
    fan = _fanout(alerts.append)
    # 3 tracks inside the pen zone, threshold is 2 -> count alert
    for tid, (cx, cy) in enumerate([(5, 5), (6, 6), (7, 7)], start=1):
        fan.on_track(_track(tid, cx, cy, frame_id=0))
    fan.flush()
    titles = [a.title for a in alerts]
    assert "Zone count" in titles


def test_fanout_emits_immobility_alert_after_dwell():
    alerts = []
    clock = [0.0]
    fan = _fanout(alerts.append, imm=10.0, clock=lambda: clock[0])
    # same track, same spot, across frames; advance the clock past immobility_seconds
    for fid in range(4):
        clock[0] = fid * 5.0  # 0, 5, 10, 15s
        fan.on_track(_track(1, 5, 5, frame_id=fid))
    fan.flush()
    assert any(a.title == "Immobility" for a in alerts)


def test_fanout_sink_receives_alert_objects():
    alerts = []
    fan = _fanout(alerts.append)
    for tid, (cx, cy) in enumerate([(5, 5), (6, 6), (7, 7)], start=1):
        fan.on_track(_track(tid, cx, cy, frame_id=0))
    fan.flush()
    assert alerts and all(isinstance(a, Alert) for a in alerts)
