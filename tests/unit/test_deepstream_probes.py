"""Host tests for the DeepStream tracker-probe mapping (#15).

The pyds pipeline itself is target-only, but the contract-critical bit — turning a
DeepStream object's metadata into a ``schemas.Track`` on the bus — is a pure
function, factored out so it is unit-tested on the host. Notably the bbox frame
conversion: DeepStream's ``rect_params`` is ``(left, top, width, height)``; the
bus ``Track.bbox`` contract is ``(x1, y1, x2, y2)`` pixels.
"""

from overwatch.bus.schemas import Track
from overwatch.inference.deepstream.probes import track_from_object


def test_track_from_object_maps_fields_and_converts_bbox():
    t = track_from_object(
        track_id=3, left=10, top=20, width=30, height=40,
        class_id=2, class_name="car", confidence=0.8, frame_id=5,
    )
    assert isinstance(t, Track)
    assert t.track_id == 3
    assert t.frame_id == 5
    assert t.class_id == 2
    assert t.class_name == "car"
    assert abs(t.confidence - 0.8) < 1e-6
    # (left, top, width, height) -> (x1, y1, x2, y2)
    assert t.bbox == (10.0, 20.0, 40.0, 60.0)
    assert t.identity is None  # ReID attaches later, on-demand (ADR-0003)


def test_track_from_object_coerces_types():
    # pyds hands back numpy/float-ish values; the Track must carry clean python types.
    t = track_from_object(
        track_id=7.0, left=1.5, top=2.5, width=4, height=6,
        class_id=0.0, class_name="person", confidence=1, frame_id=9.0,
    )
    assert isinstance(t.track_id, int) and t.track_id == 7
    assert isinstance(t.frame_id, int) and t.frame_id == 9
    assert isinstance(t.class_id, int) and t.class_id == 0
    assert t.bbox == (1.5, 2.5, 5.5, 8.5)
    assert isinstance(t.confidence, float)
