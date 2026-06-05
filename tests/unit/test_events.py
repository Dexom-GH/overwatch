"""Host tests for fence-crossing event detection (#20).

``EventDetector`` turns the per-frame track stream into fence-crossing ``Event``s
and maps them to ``Alert``s. It is 2D image-plane geometry (ADR-0006: mono-capable,
no depth), so the whole thing is host-runnable. Geometry primitives live in
``fusion/zones.py``; here we test the stateful per-track crossing detection, the
direction filter, and the alert mapping.
"""

from overwatch.bus.schemas import Event, Track
from overwatch.config.schema import FenceLine
from overwatch.fusion.events import EventDetector
from overwatch.fusion.zones import fence_crossing

# A horizontal fence from left to right; 'out' is its left side per zones.fence_crossing.
_LINE = [(0.0, 10.0), (20.0, 10.0)]


def _fence(crossing="any", name="north-gate"):
    return FenceLine(name=name, line=_LINE, space="image", crossing=crossing)


def _track(track_id, cx, cy, frame_id=0):
    # 2x2 bbox centred on (cx, cy) so bbox_centroid == (cx, cy).
    return Track(
        track_id=track_id,
        frame_id=frame_id,
        bbox=(cx - 1, cy - 1, cx + 1, cy + 1),
        class_id=0,
        class_name="sheep",
        confidence=0.9,
    )


def _expected_direction(cx, y_from, y_to):
    return fence_crossing((cx, y_from), (cx, y_to), _LINE)


def test_first_observation_records_but_emits_nothing():
    det = EventDetector([_fence()])
    assert det.detect_fence_crossing(1.0, [_track(1, 10, 5)]) == []


def test_crossing_emits_event():
    det = EventDetector([_fence()])
    det.detect_fence_crossing(1.0, [_track(1, 10, 5)])          # below the line
    events = det.detect_fence_crossing(2.0, [_track(1, 10, 15)])  # now above -> crossed
    assert len(events) == 1
    ev = events[0]
    assert ev.kind == "fence_crossing"
    assert ev.track_id == 1
    assert ev.zone_id == "north-gate"
    assert ev.timestamp == 2.0
    assert ev.detail["direction"] == _expected_direction(10, 5, 15)


def test_no_event_when_motion_does_not_cross():
    det = EventDetector([_fence()])
    det.detect_fence_crossing(1.0, [_track(1, 10, 5)])
    # moves but stays on the same side of the line
    assert det.detect_fence_crossing(2.0, [_track(1, 12, 7)]) == []


def test_direction_filter_only_fires_matching_direction():
    direction = _expected_direction(10, 5, 15)
    opposite = "out_to_in" if direction == "in_to_out" else "in_to_out"

    match = EventDetector([_fence(crossing=direction)])
    match.detect_fence_crossing(1.0, [_track(1, 10, 5)])
    assert len(match.detect_fence_crossing(2.0, [_track(1, 10, 15)])) == 1

    blocked = EventDetector([_fence(crossing=opposite)])
    blocked.detect_fence_crossing(1.0, [_track(1, 10, 5)])
    assert blocked.detect_fence_crossing(2.0, [_track(1, 10, 15)]) == []


def test_per_track_state_is_independent():
    det = EventDetector([_fence()])
    det.detect_fence_crossing(1.0, [_track(1, 10, 5), _track(2, 10, 15)])
    # track 1 crosses up; track 2 stays above -> only one event
    events = det.detect_fence_crossing(2.0, [_track(1, 10, 15), _track(2, 10, 16)])
    assert [e.track_id for e in events] == [1]


def test_to_alert_maps_fence_crossing():
    det = EventDetector([_fence()])
    event = Event(
        timestamp=3.0, kind="fence_crossing", track_id=7, zone_id="north-gate",
        detail={"direction": "in_to_out", "class_name": "sheep"},
    )
    alert = det.to_alert(event)
    assert alert is not None
    assert alert.severity == "warning"
    assert alert.source_event is event
    # operator-friendly: animal name + fence name + plain-language direction,
    # no raw direction codes.
    assert "Sheep" in alert.message
    assert "north-gate" in alert.message
    assert "leaving" in alert.message.lower()
    assert "in_to_out" not in alert.message


def test_to_alert_falls_back_to_animal_when_class_unknown():
    det = EventDetector([_fence()])
    event = Event(
        timestamp=3.0, kind="fence_crossing", track_id=7, zone_id="gate",
        detail={"direction": "out_to_in"},
    )
    alert = det.to_alert(event)
    assert alert is not None
    assert "Animal" in alert.message          # no class_name -> generic noun
    assert "entering" in alert.message.lower()  # out_to_in -> entering


def test_to_alert_ignores_non_fence_events():
    det = EventDetector([_fence()])
    assert det.to_alert(Event(timestamp=1.0, kind="something_else")) is None
