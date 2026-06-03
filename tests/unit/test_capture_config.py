"""Host tests for the multi-source capture config (#30).

`CaptureConfig` evolves from a single hard-locked ZED to a typed LIST of sources
(`zed | rtsp`). Decision (in-issue, #30): **keep backward-compat** — the legacy
scalar `{source, source_id, fps}` form normalizes to a one-element `sources` list,
so the shipped single-ZED `default.yaml` and existing consumers keep working.
RTSP credentials resolve from env (never YAML) — that loader behavior is tested
in test_config.py. Schema-level validation is tested here.
"""

import pytest

from overwatch.config.schema import CaptureConfig, ConfigError, validate_config


def _base():
    return {
        "bus": {"transport": "zeromq", "endpoint": "ipc:///tmp/ow", "url_env": None},
        "capture": {"source": "zed", "source_id": "zed-0", "fps": 15},
        "inference": {
            "detector_config": "d.txt", "tracker_config": "t.txt",
            "reid": {"engine": "m.engine", "refresh_seconds": 30, "min_crop_confidence": 0.5},
        },
        "fusion": {
            "zones": [], "health": {"immobility_seconds": 600, "lameness_score_threshold": 0.6},
            "events": {"fence_zones": []},
        },
        "output": {
            "slack": {"webhook_env": "SLACK_WEBHOOK", "min_severity": "warning"},
            "store": {"backend": "sqlite", "path": "data/ow.db"},
        },
    }


class TestLegacyNormalization:
    def test_scalar_form_becomes_one_zed_source(self):
        cfg = validate_config(_base())
        assert len(cfg.capture.sources) == 1
        assert cfg.capture.sources[0].type == "zed"
        assert cfg.capture.sources[0].source_id == "zed-0"

    def test_compat_properties_read_primary(self):
        cfg = validate_config(_base())
        # Existing consumers (app.py, service.py) read these unchanged.
        assert cfg.capture.source_id == "zed-0"
        assert cfg.capture.fps == 15
        assert cfg.capture.source == "zed"


class TestTypedSourceList:
    def test_explicit_zed_list(self):
        data = _base()
        data["capture"] = {"sources": [{"type": "zed", "source_id": "zed-0", "fps": 15}]}
        cfg = validate_config(data)
        assert cfg.capture.sources[0].source_id == "zed-0"

    def test_rtsp_source_with_cred_env(self):
        data = _base()
        data["capture"] = {
            "sources": [
                {"type": "zed", "source_id": "zed-0", "fps": 15},
                {
                    "type": "rtsp", "source_id": "cam-n", "fps": 10,
                    "url": "rtsp://host:554/stream", "cred_env": "RTSP_CRED_CAM_N",
                },
            ]
        }
        cfg = validate_config(data)
        assert cfg.capture.sources[1].type == "rtsp"
        assert cfg.capture.sources[1].url == "rtsp://host:554/stream"
        assert cfg.capture.sources[1].cred_env == "RTSP_CRED_CAM_N"
        assert cfg.capture.sources[1].cred is None  # resolved by the loader, not YAML

    def test_rejects_duplicate_source_ids(self):
        data = _base()
        data["capture"] = {
            "sources": [
                {"type": "zed", "source_id": "dup", "fps": 15},
                {"type": "rtsp", "source_id": "dup", "fps": 10, "url": "rtsp://h/s"},
            ]
        }
        with pytest.raises(ConfigError):
            validate_config(data)

    def test_rejects_unknown_source_type(self):
        data = _base()
        data["capture"] = {"sources": [{"type": "usb", "source_id": "c", "fps": 15}]}
        with pytest.raises(ConfigError):
            validate_config(data)

    def test_rejects_empty_source_list(self):
        data = _base()
        data["capture"] = {"sources": []}
        with pytest.raises(ConfigError):
            validate_config(data)

    def test_rtsp_requires_url(self):
        data = _base()
        data["capture"] = {"sources": [{"type": "rtsp", "source_id": "c", "fps": 10}]}
        with pytest.raises(ConfigError):
            validate_config(data)


def test_capture_config_constructs_directly():
    # Direct construction (legacy form) for non-loader callers.
    cc = CaptureConfig(source="zed", source_id="zed-0", fps=15)
    assert cc.source_id == "zed-0"
