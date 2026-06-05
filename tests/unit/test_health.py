"""Host tests for immobility health detection (#19).

``HealthMonitor.update_immobility`` flags a track that stays put longer than
``immobility_seconds``. It is 2D centroid dwell logic (ADR-0006: mono-capable, no
depth) — pure host logic, so the dwell timer, the movement reset (AC2), and the
once-per-episode emission are all unit-tested here. The alert maps through the
shared ``SlackAlertSink`` (landed in #20/#73).
"""

import json

from overwatch.bus.schemas import HealthSignal, Track
from overwatch.fusion.health import HealthMonitor
from overwatch.output.slack import SlackAlertSink, ThrottledAlertSink
from overwatch.output.throttle import AlertThrottle


def _track(track_id, cx, cy, frame_id=0):
    return Track(
        track_id=track_id,
        frame_id=frame_id,
        bbox=(cx - 1, cy - 1, cx + 1, cy + 1),
        class_id=0,
        class_name="sheep",
        confidence=0.9,
    )


def test_no_signal_before_threshold():
    mon = HealthMonitor(immobility_seconds=10.0, move_threshold_px=5.0)
    assert mon.update_immobility(0.0, _track(1, 10, 10)) is None   # seed
    assert mon.update_immobility(5.0, _track(1, 10, 10)) is None   # 5s < 10s


def test_signal_when_stationary_past_threshold():
    mon = HealthMonitor(immobility_seconds=10.0, move_threshold_px=5.0)
    mon.update_immobility(0.0, _track(1, 10, 10))
    sig = mon.update_immobility(10.0, _track(1, 10, 10))
    assert isinstance(sig, HealthSignal)
    assert sig.kind == "immobility"
    assert sig.track_id == 1
    assert sig.timestamp == 10.0


def test_movement_resets_the_timer():
    mon = HealthMonitor(immobility_seconds=10.0, move_threshold_px=5.0)
    mon.update_immobility(0.0, _track(1, 10, 10))            # seed at A
    assert mon.update_immobility(10.0, _track(1, 100, 100)) is None  # moved -> reset
    assert mon.update_immobility(15.0, _track(1, 100, 100)) is None  # only 5s at B
    sig = mon.update_immobility(21.0, _track(1, 100, 100))   # 11s at B -> fires
    assert isinstance(sig, HealthSignal)


def test_no_duplicate_signal_within_one_immobile_episode():
    mon = HealthMonitor(immobility_seconds=10.0, move_threshold_px=5.0)
    mon.update_immobility(0.0, _track(1, 10, 10))
    assert mon.update_immobility(10.0, _track(1, 10, 10)) is not None  # first crossing
    assert mon.update_immobility(11.0, _track(1, 10, 10)) is None      # still immobile
    assert mon.update_immobility(20.0, _track(1, 10, 10)) is None


def test_new_episode_after_movement_fires_again():
    mon = HealthMonitor(immobility_seconds=10.0, move_threshold_px=5.0)
    mon.update_immobility(0.0, _track(1, 10, 10))
    assert mon.update_immobility(10.0, _track(1, 10, 10)) is not None
    mon.update_immobility(11.0, _track(1, 200, 200))        # moves away -> new anchor
    assert mon.update_immobility(22.0, _track(1, 200, 200)) is not None  # immobile again


def test_per_track_state_is_independent():
    mon = HealthMonitor(immobility_seconds=10.0, move_threshold_px=5.0)
    mon.update_immobility(0.0, _track(1, 10, 10))
    mon.update_immobility(0.0, _track(2, 50, 50))
    # track 1 sits still; track 2 keeps moving
    sig1 = mon.update_immobility(10.0, _track(1, 10, 10))
    sig2 = mon.update_immobility(10.0, _track(2, 90, 90))
    assert sig1 is not None and sig2 is None


def test_to_alert_maps_immobility_signal():
    mon = HealthMonitor(immobility_seconds=10.0)
    sig = HealthSignal(track_id=7, timestamp=12.0, kind="immobility", score=1.0,
                       detail={"stationary_seconds": 725.0, "class_name": "goat"})
    alert = mon.to_alert(sig)
    assert alert is not None
    assert alert.severity == "warning"
    # operator-friendly: animal name + human-readable duration, not raw "725s".
    assert "Goat" in alert.message
    assert "stationary" in alert.message.lower()
    assert "12 min" in alert.message
    assert "725" not in alert.message


def test_immobility_end_to_end_host_chain():
    # Demoable host vertical (#19): a stationary track -> exactly one Slack post.
    mon = HealthMonitor(immobility_seconds=10.0, move_threshold_px=5.0)
    posts = []
    sink = SlackAlertSink("u", poster=lambda url, payload: posts.append(json.loads(payload)))

    def feed(ts, cx, cy):
        sig = mon.update_immobility(ts, _track(1, cx, cy))
        if sig is not None:
            sink.send(mon.to_alert(sig))

    feed(0.0, 10, 10)    # seed
    feed(5.0, 10, 10)    # still < threshold
    feed(10.0, 10, 10)   # crosses threshold -> one alert
    feed(11.0, 10, 10)   # still immobile -> no new alert
    assert len(posts) == 1
    assert "immobil" in json.dumps(posts[0]).lower()


def test_two_tracks_dedup_per_track_through_throttle():
    # Regression (#19/#42): two distinct tracks going immobile inside one cooldown
    # window must each get their own Slack post — the throttle keys per track, so
    # they do NOT collapse to a single alert (which they would if source_event were
    # None and both keyed on the shared ("Immobility", None)).
    mon = HealthMonitor(immobility_seconds=10.0, move_threshold_px=5.0)
    posts = []
    slack = SlackAlertSink("u", poster=lambda url, payload: posts.append(json.loads(payload)))
    # One clock value -> both alerts land inside the cooldown window.
    throttled = ThrottledAlertSink(slack, AlertThrottle(cooldown_seconds=60.0, clock=lambda: 0.0))

    for tid, cx, cy in ((1, 10, 10), (2, 80, 80)):
        mon.update_immobility(0.0, _track(tid, cx, cy))            # seed each track
        sig = mon.update_immobility(10.0, _track(tid, cx, cy))     # crosses threshold
        assert sig is not None
        throttled.send(mon.to_alert(sig))

    assert len(posts) == 2  # one per track, not de-duped together
