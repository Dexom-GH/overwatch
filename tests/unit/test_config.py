"""Host tests for config schema + validation (issue #2).

Covers: valid load, fail-fast validation errors, env-var layering with the
``*_env`` secret indirection, the ADR-0001 conditional bus rule, and must-tune
flagging. All host-runnable (no device/gpu/zed markers).
"""

import logging
from pathlib import Path

import pytest

from overwatch.config import AppConfig, ConfigError, load_config
from overwatch.config.loader import validate_secrets
from overwatch.config.schema import validate_config


def _valid_data():
    """A minimal config dict that satisfies the schema."""
    return {
        "bus": {"transport": "zeromq", "endpoint": "ipc:///tmp/ow-bus", "url_env": None},
        "capture": {"source": "zed", "source_id": "zed-0", "fps": 15},
        "inference": {
            "detector_config": "detector.txt",
            "tracker_config": "tracker.txt",
            "reid": {
                "engine": "model.engine",
                "refresh_seconds": 30,
                "min_crop_confidence": 0.5,
            },
        },
        "fusion": {
            "zones": [],
            "health": {"immobility_seconds": 600, "lameness_score_threshold": 0.6},
            "events": {"fence_zones": []},
        },
        "output": {
            "slack": {"webhook_env": "SLACK_WEBHOOK", "min_severity": "warning"},
            "store": {"backend": "sqlite", "path": "data/ow.db"},
        },
    }


# --- schema validation -----------------------------------------------------

def test_validate_accepts_minimal_valid():
    cfg = validate_config(_valid_data())
    assert isinstance(cfg, AppConfig)
    assert cfg.bus.transport == "zeromq"
    assert cfg.capture.fps == 15
    assert cfg.output.store.backend == "sqlite"


def test_rejects_unknown_transport():
    data = _valid_data()
    data["bus"]["transport"] = "kafka"
    with pytest.raises(ConfigError) as exc:
        validate_config(data)
    assert "transport" in str(exc.value)


def test_rejects_fps_zero():
    data = _valid_data()
    data["capture"]["fps"] = 0
    with pytest.raises(ConfigError):
        validate_config(data)


def test_rejects_out_of_range_confidence():
    data = _valid_data()
    data["inference"]["reid"]["min_crop_confidence"] = 1.5
    with pytest.raises(ConfigError):
        validate_config(data)


def test_rejects_missing_required_section():
    data = _valid_data()
    del data["capture"]
    with pytest.raises(ConfigError):
        validate_config(data)


def test_rejects_unknown_key():
    data = _valid_data()
    data["capture"]["frame_rate"] = 30  # typo of fps; must fail fast
    with pytest.raises(ConfigError) as exc:
        validate_config(data)
    assert "frame_rate" in str(exc.value)


def test_error_message_aggregates_field_path():
    data = _valid_data()
    data["capture"]["fps"] = -1
    data["bus"]["transport"] = "kafka"
    with pytest.raises(ConfigError) as exc:
        validate_config(data)
    msg = str(exc.value)
    assert "capture" in msg and "fps" in msg
    assert "bus" in msg and "transport" in msg


# --- output alert throttle (#42) -------------------------------------------

def test_output_throttle_defaults_when_omitted():
    cfg = validate_config(_valid_data())
    assert cfg.output.throttle.cooldown_seconds == 60.0
    assert cfg.output.throttle.max_per_window is None
    assert cfg.output.throttle.rate_window_seconds == 60.0


def test_output_throttle_overrides_validate():
    data = _valid_data()
    data["output"]["throttle"] = {
        "cooldown_seconds": 30.0, "max_per_window": 5, "rate_window_seconds": 120.0
    }
    cfg = validate_config(data)
    assert cfg.output.throttle.cooldown_seconds == 30.0
    assert cfg.output.throttle.max_per_window == 5


def test_rejects_negative_cooldown():
    data = _valid_data()
    data["output"]["throttle"] = {"cooldown_seconds": -1.0}
    with pytest.raises(ConfigError):
        validate_config(data)


def test_rejects_zero_rate_window():
    data = _valid_data()
    data["output"]["throttle"] = {"rate_window_seconds": 0.0}
    with pytest.raises(ConfigError):
        validate_config(data)


# --- ADR-0001 conditional bus / store rule ---------------------------------

def test_zeromq_requires_endpoint():
    data = _valid_data()
    data["bus"] = {"transport": "zeromq", "endpoint": None, "url_env": None}
    with pytest.raises(ConfigError) as exc:
        validate_config(data)
    assert "endpoint" in str(exc.value)


def test_redis_requires_url_env():
    data = _valid_data()
    data["bus"] = {"transport": "redis", "endpoint": None, "url_env": None}
    with pytest.raises(ConfigError) as exc:
        validate_config(data)
    assert "url_env" in str(exc.value)


def test_sqlite_store_requires_path():
    data = _valid_data()
    data["output"]["store"] = {"backend": "sqlite", "path": None}
    with pytest.raises(ConfigError) as exc:
        validate_config(data)
    assert "path" in str(exc.value)


# --- must-tune flagging ----------------------------------------------------

def test_must_tune_fields_lists_expected_paths():
    cfg = validate_config(_valid_data())
    paths = cfg.must_tune_fields()
    assert "capture.fps" in paths
    assert "fusion.health.immobility_seconds" in paths
    assert "inference.reid.min_crop_confidence" in paths


# --- loader: layering + env ------------------------------------------------

def test_load_default_config_returns_appconfig():
    cfg = load_config()
    assert isinstance(cfg, AppConfig)
    # default.yaml reflects ADR-0001 (hybrid: zeromq + sqlite)
    assert cfg.bus.transport == "zeromq"
    assert cfg.output.store.backend == "sqlite"


def test_override_file_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("OVERWATCH_CONFIG_DIR", str(tmp_path))
    (tmp_path / "default.yaml").write_text(_yaml(_valid_data()))
    override = tmp_path / "override.yaml"
    override.write_text("capture:\n  fps: 30\n")
    cfg = load_config(str(override))
    assert cfg.capture.fps == 30


def test_env_overrides_source_id(tmp_path, monkeypatch):
    monkeypatch.setenv("OVERWATCH_CONFIG_DIR", str(tmp_path))
    (tmp_path / "default.yaml").write_text(_yaml(_valid_data()))
    monkeypatch.setenv("ZED_SOURCE_ID", "zed-override")
    cfg = load_config()
    assert cfg.capture.source_id == "zed-override"


def test_slack_webhook_resolved_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OVERWATCH_CONFIG_DIR", str(tmp_path))
    (tmp_path / "default.yaml").write_text(_yaml(_valid_data()))
    monkeypatch.setenv("SLACK_WEBHOOK", "https://hooks.example/abc")
    cfg = load_config()
    assert cfg.output.slack.webhook == "https://hooks.example/abc"


def test_slack_webhook_none_when_env_unset(tmp_path, monkeypatch):
    monkeypatch.setenv("OVERWATCH_CONFIG_DIR", str(tmp_path))
    (tmp_path / "default.yaml").write_text(_yaml(_valid_data()))
    monkeypatch.delenv("SLACK_WEBHOOK", raising=False)
    # Point at an absent .env so a developer's real repo .env can't leak in.
    monkeypatch.setenv("OVERWATCH_ENV_FILE", str(tmp_path / ".env.absent"))
    cfg = load_config()
    assert cfg.output.slack.webhook is None


def test_secret_in_yaml_is_rejected(tmp_path, monkeypatch):
    # #41: secrets must NEVER live in YAML. A webhook literal in any YAML layer is
    # a hard error — it must come from the environment / .env instead.
    monkeypatch.setenv("OVERWATCH_CONFIG_DIR", str(tmp_path))
    (tmp_path / "default.yaml").write_text(_yaml(_valid_data()))
    override = tmp_path / "override.yaml"
    override.write_text("output:\n  slack:\n    webhook: https://hooks.example/in-yaml\n")
    monkeypatch.setenv("OVERWATCH_ENV_FILE", str(tmp_path / ".env.absent"))
    with pytest.raises(ConfigError) as exc:
        load_config(str(override))
    assert "webhook" in str(exc.value).lower()


# --- .env / secrets resolution (#41) ---------------------------------------

def test_secret_resolves_from_dotenv_file(tmp_path, monkeypatch):
    monkeypatch.setenv("OVERWATCH_CONFIG_DIR", str(tmp_path))
    (tmp_path / "default.yaml").write_text(_yaml(_valid_data()))
    env_file = tmp_path / ".env"
    env_file.write_text("SLACK_WEBHOOK=https://hooks.example/from-dotenv\n")
    monkeypatch.setenv("OVERWATCH_ENV_FILE", str(env_file))
    monkeypatch.delenv("SLACK_WEBHOOK", raising=False)
    cfg = load_config()
    assert cfg.output.slack.webhook == "https://hooks.example/from-dotenv"


def test_real_env_var_wins_over_dotenv(tmp_path, monkeypatch):
    # A value already in the process environment must win over the .env fallback.
    monkeypatch.setenv("OVERWATCH_CONFIG_DIR", str(tmp_path))
    (tmp_path / "default.yaml").write_text(_yaml(_valid_data()))
    env_file = tmp_path / ".env"
    env_file.write_text("SLACK_WEBHOOK=https://hooks.example/from-dotenv\n")
    monkeypatch.setenv("OVERWATCH_ENV_FILE", str(env_file))
    monkeypatch.setenv("SLACK_WEBHOOK", "https://hooks.example/from-realenv")
    cfg = load_config()
    assert cfg.output.slack.webhook == "https://hooks.example/from-realenv"


def test_validate_secrets_raises_when_required_secret_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("OVERWATCH_CONFIG_DIR", str(tmp_path))
    (tmp_path / "default.yaml").write_text(_yaml(_valid_data()))
    monkeypatch.delenv("SLACK_WEBHOOK", raising=False)
    monkeypatch.setenv("OVERWATCH_ENV_FILE", str(tmp_path / ".env.absent"))
    cfg = load_config()  # lenient load: webhook is None, no raise here
    with pytest.raises(ConfigError) as exc:
        validate_secrets(cfg)
    assert "SLACK_WEBHOOK" in str(exc.value)


def test_validate_secrets_passes_when_secret_present(tmp_path, monkeypatch):
    monkeypatch.setenv("OVERWATCH_CONFIG_DIR", str(tmp_path))
    (tmp_path / "default.yaml").write_text(_yaml(_valid_data()))
    monkeypatch.setenv("SLACK_WEBHOOK", "https://hooks.example/ok")
    cfg = load_config()
    validate_secrets(cfg)  # must not raise


def test_nonstring_webhook_env_raises_configerror(tmp_path, monkeypatch):
    # A YAML typo (webhook_env as a non-string) must surface as a clean ConfigError,
    # not a raw TypeError from os.environ.get during the env overlay.
    monkeypatch.setenv("OVERWATCH_CONFIG_DIR", str(tmp_path))
    data = _valid_data()
    data["output"]["slack"]["webhook_env"] = 123
    (tmp_path / "default.yaml").write_text(_yaml(data))
    with pytest.raises(ConfigError):
        load_config()


def test_load_emits_must_tune_warning(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("OVERWATCH_CONFIG_DIR", str(tmp_path))
    (tmp_path / "default.yaml").write_text(_yaml(_valid_data()))
    with caplog.at_level(logging.WARNING):
        load_config()
    assert any("must-tune" in r.getMessage().lower() for r in caplog.records)


def test_env_example_documents_secrets(tmp_path, monkeypatch):
    # .env.example must document every secret the loader/validate_secrets expect.
    root = Path(__file__).resolve().parents[2]
    text = (root / ".env.example").read_text(encoding="utf-8")
    assert "SLACK_WEBHOOK" in text          # required secret
    assert "RTSP_CRED" in text              # per-camera creds pattern (#30)


def _yaml(data):
    import yaml

    return yaml.safe_dump(data)
