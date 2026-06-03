"""Host tests for the shared alert de-dup / rate-limit utility (#42).

One reusable utility collapses repeat/rapid alerts so "one crossing != an alert
storm", implemented once for the counting / health / fence slices (#16/#19/#20/#33).
Pure logic + mocked sink — no target-only deps.
"""

from overwatch.bus.schemas import Alert, Event
from overwatch.output.slack import AlertSink, ThrottledAlertSink
from overwatch.output.throttle import AlertThrottle, default_alert_key


class _Clock:
    """Controllable monotonic clock for deterministic window tests."""

    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def _alert(kind=None, zone_id=None, track_id=None, title="count", severity="warning"):
    event = None
    if kind is not None:
        event = Event(timestamp=0.0, kind=kind, zone_id=zone_id, track_id=track_id)
    return Alert(
        timestamp=0.0, severity=severity, title=title, message="m", source_event=event
    )


class TestDefaultAlertKey:
    def test_zone_event_keys_on_zone_id(self):
        a = _alert(kind="zone_count", zone_id="pen-A", track_id=7)
        # zone events identify by zone_id (zone_id wins over track_id)
        assert default_alert_key(a) == ("zone_count", "pen-A")

    def test_per_animal_event_keys_on_track_id(self):
        a = _alert(kind="immobility", track_id=42)
        assert default_alert_key(a) == ("immobility", "42")

    def test_alert_without_source_event_keys_on_title(self):
        a = _alert(title="system warning")
        assert default_alert_key(a) == ("system warning", None)


class TestDedup:
    def test_first_alert_is_allowed(self):
        th = AlertThrottle(cooldown_seconds=60.0, clock=_Clock())
        assert th.allow(_alert(kind="zone_count", zone_id="z1")) is True

    def test_identical_key_within_window_is_suppressed(self):
        clock = _Clock()
        th = AlertThrottle(cooldown_seconds=60.0, clock=clock)
        a = _alert(kind="zone_count", zone_id="z1")
        assert th.allow(a) is True
        clock.advance(30.0)
        assert th.allow(a) is False  # still within the 60s cooldown

    def test_same_key_allowed_again_after_window(self):
        clock = _Clock()
        th = AlertThrottle(cooldown_seconds=60.0, clock=clock)
        a = _alert(kind="zone_count", zone_id="z1")
        assert th.allow(a) is True
        clock.advance(61.0)
        assert th.allow(a) is True  # cooldown elapsed

    def test_different_keys_do_not_suppress_each_other(self):
        th = AlertThrottle(cooldown_seconds=60.0, clock=_Clock())
        assert th.allow(_alert(kind="zone_count", zone_id="z1")) is True
        assert th.allow(_alert(kind="zone_count", zone_id="z2")) is True
        assert th.allow(_alert(kind="immobility", track_id=1)) is True


class TestRateLimit:
    def test_burst_capped_within_rate_window(self):
        th = AlertThrottle(
            cooldown_seconds=0.0, max_per_window=2, rate_window_seconds=60.0, clock=_Clock()
        )
        # Distinct keys so de-dup never fires; only the rate cap should bite.
        assert th.allow(_alert(kind="e", zone_id="z1")) is True
        assert th.allow(_alert(kind="e", zone_id="z2")) is True
        assert th.allow(_alert(kind="e", zone_id="z3")) is False  # cap reached

    def test_rate_budget_recovers_after_window(self):
        clock = _Clock()
        th = AlertThrottle(
            cooldown_seconds=0.0, max_per_window=2, rate_window_seconds=60.0, clock=clock
        )
        assert th.allow(_alert(kind="e", zone_id="z1")) is True
        assert th.allow(_alert(kind="e", zone_id="z2")) is True
        assert th.allow(_alert(kind="e", zone_id="z3")) is False
        clock.advance(61.0)
        assert th.allow(_alert(kind="e", zone_id="z4")) is True

    def test_deduped_alerts_do_not_consume_rate_budget(self):
        clock = _Clock()
        th = AlertThrottle(
            cooldown_seconds=60.0, max_per_window=2, rate_window_seconds=60.0, clock=clock
        )
        dup = _alert(kind="e", zone_id="z1")
        assert th.allow(dup) is True       # emitted (1 of 2)
        assert th.allow(dup) is False      # suppressed by de-dup, must NOT spend budget
        assert th.allow(_alert(kind="e", zone_id="z2")) is True   # 2 of 2
        assert th.allow(_alert(kind="e", zone_id="z3")) is False  # now cap reached


class _RecordingSink(AlertSink):
    def __init__(self):
        self.sent = []

    def send(self, alert):
        self.sent.append(alert)


class TestThrottledAlertSink:
    def test_forwards_allowed_alert_to_delegate(self):
        rec = _RecordingSink()
        sink = ThrottledAlertSink(rec, AlertThrottle(cooldown_seconds=60.0, clock=_Clock()))
        sink.send(_alert(kind="zone_count", zone_id="z1"))
        assert len(rec.sent) == 1

    def test_drops_suppressed_alert(self):
        rec = _RecordingSink()
        sink = ThrottledAlertSink(rec, AlertThrottle(cooldown_seconds=60.0, clock=_Clock()))
        a = _alert(kind="zone_count", zone_id="z1")
        sink.send(a)
        sink.send(a)  # duplicate within cooldown -> not forwarded
        assert len(rec.sent) == 1


class _ThrottleCfg:
    """Duck-typed stand-in for config.schema.ThrottleConfig."""

    cooldown_seconds = 30.0
    max_per_window = 1
    rate_window_seconds = 10.0


class TestFromConfig:
    def test_from_config_applies_configured_window_and_rate(self):
        clock = _Clock()
        th = AlertThrottle.from_config(_ThrottleCfg(), clock=clock)
        assert th.allow(_alert(kind="e", zone_id="z1")) is True
        # max_per_window=1 -> a second distinct-key alert is rate-capped
        assert th.allow(_alert(kind="e", zone_id="z2")) is False
        # configured cooldown is 30s, not the 60s default
        clock.advance(31.0)
        assert th.allow(_alert(kind="e", zone_id="z3")) is True
