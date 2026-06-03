"""Host tests for the Slack alert sink (#16) and the fence-crossing chain (#20).

``SlackAlertSink`` formats an ``Alert`` and POSTs it to a webhook. The HTTP POST
is injected (a fake ``poster``) so formatting + delivery are unit-tested off the
network. We also exercise the full host-side fence-crossing vertical:
EventDetector -> to_alert -> ThrottledAlertSink(SlackAlertSink) — proving spurious
re-crossings de-dup via the shared throttle (#42), which is AC2 of #20.
"""

import json

from overwatch.bus.schemas import Alert, Event, Track
from overwatch.config.schema import FenceLine
from overwatch.fusion.events import EventDetector
from overwatch.output.slack import SlackAlertSink, ThrottledAlertSink
from overwatch.output.throttle import AlertThrottle


class _Poster:
    """Captures (url, payload-dict) instead of making a real HTTP POST."""

    def __init__(self):
        self.posts = []

    def __call__(self, url, payload):
        self.posts.append((url, json.loads(payload.decode("utf-8"))))


def _alert(severity="warning", title="Fence crossing", message="Track 1 crossed fence 'gate'"):
    event = Event(timestamp=1.0, kind="fence_crossing", track_id=1, zone_id="gate")
    return Alert(
        timestamp=1.0, severity=severity, title=title, message=message, source_event=event
    )


def test_send_posts_formatted_payload_to_webhook():
    poster = _Poster()
    sink = SlackAlertSink("https://hooks.slack.test/abc", poster=poster)
    sink.send(_alert())
    assert len(poster.posts) == 1
    url, body = poster.posts[0]
    assert url == "https://hooks.slack.test/abc"
    # the alert title + message reach the Slack payload
    blob = json.dumps(body)
    assert "Fence crossing" in blob
    assert "Track 1 crossed fence 'gate'" in blob


def test_send_colors_by_severity():
    def color_for(sev):
        poster = _Poster()
        SlackAlertSink("u", poster=poster).send(_alert(severity=sev))
        return poster.posts[0][1]["attachments"][0]["color"]

    # distinct colors per severity (warning != critical != info)
    colors = {color_for(s) for s in ("info", "warning", "critical")}
    assert len(colors) == 3


def test_unknown_severity_still_sends_with_fallback_color():
    poster = _Poster()
    SlackAlertSink("u", poster=poster).send(_alert(severity="weird"))
    assert len(poster.posts) == 1  # never raises on an unexpected severity


def test_throttled_sink_dedups_within_cooldown():
    poster = _Poster()
    clock = [0.0]
    throttle = AlertThrottle(cooldown_seconds=60.0, clock=lambda: clock[0])
    sink = ThrottledAlertSink(SlackAlertSink("u", poster=poster), throttle)

    sink.send(_alert())
    clock[0] = 5.0       # 5s later, identical alert (same fence) -> suppressed
    sink.send(_alert())
    assert len(poster.posts) == 1

    clock[0] = 70.0      # past the cooldown -> emitted again
    sink.send(_alert())
    assert len(poster.posts) == 2


def test_fence_crossing_end_to_end_host_chain():
    # The demoable host vertical (#20): a track crosses a configured fence ->
    # exactly one Slack post; a spurious re-cross within cooldown is de-duped.
    fence = FenceLine(name="gate", line=[(0.0, 10.0), (20.0, 10.0)], space="image")
    detector = EventDetector([fence])
    poster = _Poster()
    clock = [0.0]
    sink = ThrottledAlertSink(
        SlackAlertSink("u", poster=poster),
        AlertThrottle(cooldown_seconds=60.0, clock=lambda: clock[0]),
    )

    def _track(cy, frame_id):
        return Track(track_id=1, frame_id=frame_id, bbox=(9, cy - 1, 11, cy + 1),
                     class_id=0, class_name="sheep", confidence=0.9)

    def feed(ts, cy, frame_id):
        for event in detector.detect_fence_crossing(ts, [_track(cy, frame_id)]):
            alert = detector.to_alert(event)
            if alert is not None:
                sink.send(alert)

    feed(1.0, 5, 0)    # below the fence (seed)
    feed(2.0, 15, 1)   # crosses up -> alert
    clock[0] = 3.0
    feed(3.0, 5, 2)    # crosses back within cooldown -> de-duped
    assert len(poster.posts) == 1
    assert "gate" in json.dumps(poster.posts[0][1])
