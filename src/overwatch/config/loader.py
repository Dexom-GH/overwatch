"""Layered config loader.

Resolution order (later overrides earlier): packaged ``configs/default.yaml`` ->
an optional override YAML (``config_path``) -> environment variables. Env layering
is deliberately minimal: it resolves the ``*_env`` secret indirections (e.g.
``SLACK_WEBHOOK``, named by ``output.slack.webhook_env``) and a small documented
set of direct overrides (``ZED_SOURCE_ID`` -> ``capture.source_id``). Returns a
validated, typed ``AppConfig``; misconfiguration raises ``ConfigError``.

The base config directory is ``$OVERWATCH_CONFIG_DIR`` if set, else the repo's
``configs/`` next to the package. Target code — kept Python 3.8-compatible.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from overwatch.config.schema import AppConfig, ConfigError, validate_config

log = logging.getLogger("overwatch.config")


def load_config(config_path: Optional[str] = None) -> AppConfig:
    """Load + merge + validate configuration into a typed ``AppConfig``."""
    data = _read_yaml(_config_dir() / "default.yaml")
    if config_path:
        data = _deep_merge(data, _read_yaml(Path(config_path)))
    _overlay_env(data)

    cfg = validate_config(data)

    must_tune = cfg.must_tune_fields()
    if must_tune:
        # These carry placeholder defaults in the shipped config; flag them every
        # load so they get confirmed/tuned on-device (we don't track whether an
        # operator has since changed them).
        log.warning(
            "must-tune config values to confirm/tune on-device before production: %s",
            ", ".join(must_tune),
        )
    return cfg


def _config_dir() -> Path:
    override = os.environ.get("OVERWATCH_CONFIG_DIR")
    if override:
        return Path(override)
    # src/overwatch/config/loader.py -> parents[3] is the repo root.
    return Path(__file__).resolve().parents[3] / "configs"


def _read_yaml(path: Path) -> Dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError("Cannot read config file {}: {}".format(path, exc)) from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError("Invalid YAML in {}: {}".format(path, exc)) from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError("Config root must be a mapping in {}".format(path))
    return data


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


def _overlay_env(data: Dict[str, Any]) -> None:
    """Apply env layering in place (secret indirection + documented overrides)."""
    output = data.get("output")
    slack = output.get("slack") if isinstance(output, dict) else None
    if isinstance(slack, dict):
        webhook_env = slack.get("webhook_env")
        # Only resolve when the env var is actually set, so an absent env var does
        # not clobber a webhook provided directly in YAML. A non-string webhook_env
        # is left for schema validation to report (don't index os.environ with it).
        if isinstance(webhook_env, str) and webhook_env in os.environ:
            slack["webhook"] = os.environ[webhook_env]

    capture = data.get("capture")
    if isinstance(capture, dict) and "ZED_SOURCE_ID" in os.environ:
        capture["source_id"] = os.environ["ZED_SOURCE_ID"]


__all__ = ["load_config"]
