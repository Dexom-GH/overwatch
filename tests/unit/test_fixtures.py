"""Proves the shared host fixtures/factories work and compose with real code (#44)."""

from factories import make_alert, make_event, make_frame, make_track
from overwatch.config.schema import FenceLine, Zone, validate_config
from overwatch.output.slack import ThrottledAlertSink
from overwatch.output.throttle import AlertThrottle


# --- factories build valid schema objects ----------------------------------

def test_make_frame_and_depth_align_by_id():
    f = make_frame(7)
    assert f.frame_id == 7 and f.image.shape == (4, 6, 3)


def test_make_track_defaults_to_a_v1_class():
    t = make_track(3)
    assert t.track_id == 3 and t.class_name == "sheep"


def test_sample_zone_and_fence_validate(sample_zones, sample_fences):
    # The sample snippets must validate against the real schema models.
    Zone(**sample_zones[0])
    FenceLine(**sample_fences[0])


# --- fixtures work as fixtures ---------------------------------------------

def test_frame_factory_fixture(frame_factory):
    assert frame_factory(3).frame_id == 3


def test_mock_slack_webhook_records_payloads(mock_slack_webhook):
    status = mock_slack_webhook({"text": "hello"})
    assert status == 200
    assert mock_slack_webhook.payloads == [{"text": "hello"}]


# --- a sample slice test consuming the fixtures (proves they compose) -------

def test_recording_sink_composes_with_throttled_alert_sink(recording_sink):
    sink = ThrottledAlertSink(
        recording_sink, AlertThrottle(cooldown_seconds=60.0, clock=lambda: 0.0)
    )
    alert = make_alert(source_event=make_event("zone_count", zone_id="z1"))
    sink.send(alert)
    sink.send(alert)  # duplicate within cooldown -> suppressed
    assert len(recording_sink.sent) == 1


def test_factories_feed_config_validation():
    # Sample zone/fence snippets drop straight into a fusion config.
    from factories import sample_fence, sample_zone

    data = {
        "bus": {"transport": "zeromq", "endpoint": "ipc:///tmp/ow", "url_env": None},
        "capture": {"source": "zed", "source_id": "zed-0", "fps": 15},
        "inference": {
            "detector_config": "d.txt", "tracker_config": "t.txt",
            "reid": {"engine": "m.engine", "refresh_seconds": 30, "min_crop_confidence": 0.5},
        },
        "fusion": {
            "zones": [sample_zone()], "fences": [sample_fence()],
            "health": {"immobility_seconds": 600, "lameness_score_threshold": 0.6},
            "events": {"fence_zones": []},
        },
        "output": {
            "slack": {"webhook_env": "SLACK_WEBHOOK", "min_severity": "warning"},
            "store": {"backend": "sqlite", "path": "data/ow.db"},
        },
    }
    cfg = validate_config(data)
    assert cfg.fusion.zones[0].name == "pen-A"
    assert cfg.fusion.fences[0].name == "north"
