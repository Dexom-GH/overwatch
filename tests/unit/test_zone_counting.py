"""Host tests for mono 2D zone counting -> alert (#33).

``ZoneCounter.count_2d`` counts tracks whose bbox centroid falls inside each
configured zone — **2D, no depth de-dup** (the mono path per ADR-0006; the
depth-deduped ZED variant is #16). ``to_alert`` escalates a zone whose count
crosses a threshold to an ``Alert`` tagged with the *zone's* ``source_id`` (a
``Track`` has none). De-dup of sustained crossings reuses the shared throttle
(#42) keyed per-zone. All host-runnable.
"""

import json

from overwatch.bus.schemas import Track
from overwatch.config.schema import Zone
from overwatch.fusion.zone_counting import ZoneCounter
from overwatch.output.slack import SlackAlertSink, ThrottledAlertSink
from overwatch.output.throttle import AlertThrottle

# A 10x10 zone at the origin, tied to camera cam-1.
_SQUARE = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]


def _zone(name="pen-A", source_id="cam-1", polygon=None):
    return Zone(name=name, polygon=polygon or _SQUARE, space="image", source_id=source_id)


def _track(track_id, cx, cy):
    return Track(
        track_id=track_id, frame_id=0, bbox=(cx - 0.5, cy - 0.5, cx + 0.5, cy + 0.5),
        class_id=0, class_name="sheep", confidence=0.9,
    )


def test_counts_only_tracks_inside_the_zone():
    counter = ZoneCounter([_zone()])
    tracks = [_track(1, 5, 5), _track(2, 2, 2), _track(3, 50, 50)]  # 2 in, 1 out
    counts = counter.count_2d(1.0, tracks)
    assert len(counts) == 1
    assert counts[0].zone_id == "pen-A"
    assert counts[0].count == 2
    assert counts[0].timestamp == 1.0


def test_per_zone_counts_are_independent():
    far = [(100.0, 100.0), (110.0, 100.0), (110.0, 110.0), (100.0, 110.0)]
    counter = ZoneCounter([_zone("pen-A"), _zone("pen-B", source_id="cam-2", polygon=far)])
    counts = {c.zone_id: c.count for c in counter.count_2d(1.0, [_track(1, 5, 5), _track(2, 105, 105)])}
    assert counts == {"pen-A": 1, "pen-B": 1}


def test_to_alert_fires_at_or_above_threshold():
    counter = ZoneCounter([_zone()], default_threshold=2)
    assert counter.to_alert(counter.count_2d(1.0, [_track(1, 5, 5)])[0]) is None  # count 1 < 2
    alert = counter.to_alert(counter.count_2d(2.0, [_track(1, 5, 5), _track(2, 6, 6)])[0])
    assert alert is not None
    assert alert.severity == "warning"


def test_alert_is_tagged_with_zone_source_id():
    counter = ZoneCounter([_zone(source_id="cam-7")], default_threshold=1)
    alert = counter.to_alert(counter.count_2d(1.0, [_track(1, 5, 5)])[0])
    assert alert is not None
    assert alert.detail.get("source_id") == "cam-7"
    assert "cam-7" in alert.message


def test_no_alert_when_no_threshold_configured():
    counter = ZoneCounter([_zone()])  # no default_threshold, no per-zone threshold
    assert counter.to_alert(counter.count_2d(1.0, [_track(1, 5, 5)])[0]) is None


def test_per_zone_threshold_overrides_default():
    counter = ZoneCounter(
        [_zone("pen-A"), _zone("pen-B", polygon=_SQUARE)],
        thresholds={"pen-B": 5}, default_threshold=1,
    )
    counts = {c.zone_id: c for c in counter.count_2d(1.0, [_track(1, 5, 5)])}
    assert counter.to_alert(counts["pen-A"]) is not None   # default threshold 1
    assert counter.to_alert(counts["pen-B"]) is None        # overridden to 5


def test_threshold_crossing_dedups_per_zone_via_throttle():
    # The demoable host vertical (#33): a zone over threshold -> one Slack post,
    # tagged with source_id; a sustained crossing within cooldown is de-duped.
    counter = ZoneCounter([_zone(source_id="cam-1")], default_threshold=1)
    posts = []
    clock = [0.0]
    sink = ThrottledAlertSink(
        SlackAlertSink("u", poster=lambda url, payload: posts.append(json.loads(payload))),
        AlertThrottle(cooldown_seconds=60.0, clock=lambda: clock[0]),
    )

    def tick(ts):
        clock[0] = ts
        for zc in counter.count_2d(ts, [_track(1, 5, 5)]):
            alert = counter.to_alert(zc)
            if alert is not None:
                sink.send(alert)

    tick(0.0)   # over threshold -> one post
    tick(5.0)   # still over, within cooldown -> de-duped
    assert len(posts) == 1
    assert "cam-1" in json.dumps(posts[0])
