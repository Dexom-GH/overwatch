"""Layered config loader + secrets resolution (#41).

Resolution order (later overrides earlier): packaged ``configs/default.yaml`` ->
an optional override YAML (``config_path``) -> environment variables. Secrets are
**never** read from YAML — they resolve from the process environment, with a
``.env`` file as a fallback layer (real env vars win). The ``*_env`` config keys
(e.g. ``output.slack.webhook_env`` -> ``SLACK_WEBHOOK``) name *which* env var holds
the secret; the secret value itself is filled in by :func:`_overlay_env`.

``load_config`` stays lenient — a missing secret resolves to ``None`` so host
tests / dev can load without it. :func:`validate_secrets` is the strict gate the
running app (``app.main``) calls at startup to **fail loudly** when a required
secret is absent. See ``.env.example`` for every secret.

The base config directory is ``$OVERWATCH_CONFIG_DIR`` if set, else the repo's
``configs/`` next to the package. The ``.env`` file is ``$OVERWATCH_ENV_FILE`` if
set, else auto-discovered from the cwd. Target code — kept Python 3.8-compatible.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from dotenv import dotenv_values, find_dotenv

from overwatch.config.schema import (
    AppConfig,
    ConfigError,
    RtspSourceConfig,
    validate_config,
)

log = logging.getLogger("overwatch.config")

# Config keys that hold secret *values* and therefore must never appear in YAML
# (dotted path under the merged config). The matching ``*_env`` key names the env
# var instead. Extend this as new secret-bearing config lands (e.g. RTSP, #30).
_YAML_FORBIDDEN_SECRET_PATHS = ("output.slack.webhook",)


def load_config(config_path: Optional[str] = None) -> AppConfig:
    """Load + merge + validate configuration into a typed ``AppConfig``."""
    data = _read_yaml(_config_dir() / "default.yaml")
    if config_path:
        data = _deep_merge(data, _read_yaml(Path(config_path)))

    _reject_yaml_secrets(data)
    _overlay_env(data, _effective_env())

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


def validate_secrets(cfg: AppConfig) -> None:
    """Fail loudly if a required secret did not resolve (the strict startup gate).

    ``load_config`` is lenient (missing secret -> ``None``) so host tests / dev can
    load freely; the running app calls this at startup so it never starts with an
    empty/None secret. Raises ``ConfigError`` listing every missing secret.
    """
    missing = []
    if cfg.output.slack.webhook is None:
        missing.append(
            "{} (output.slack.webhook_env)".format(cfg.output.slack.webhook_env)
        )
    # Per-camera RTSP credentials (#30): required when a source names a cred_env.
    for src in cfg.capture.sources:
        if isinstance(src, RtspSourceConfig) and src.cred_env and src.cred is None:
            missing.append(
                "{} (capture source '{}')".format(src.cred_env, src.source_id)
            )
    if missing:
        raise ConfigError(
            "Missing required secret(s) — set them in the environment or .env "
            "(see .env.example):\n  " + "\n  ".join(missing)
        )


def _config_dir() -> Path:
    override = os.environ.get("OVERWATCH_CONFIG_DIR")
    if override:
        return Path(override)
    # src/overwatch/config/loader.py -> parents[3] is the repo root.
    return Path(__file__).resolve().parents[3] / "configs"


def _effective_env() -> Dict[str, str]:
    """Process env overlaid on the ``.env`` fallback (real env wins; no mutation)."""
    env_file = os.environ.get("OVERWATCH_ENV_FILE") or find_dotenv(usecwd=True)
    env: Dict[str, str] = {}
    if env_file:
        for key, value in dotenv_values(env_file).items():
            if value is not None:
                env[key] = value
    env.update(os.environ)  # a real process env var always wins over .env
    return env


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


def _reject_yaml_secrets(data: Dict[str, Any]) -> None:
    """Raise if a secret value was set in YAML (#41: secrets come from env only)."""
    offenders = []
    for path in _YAML_FORBIDDEN_SECRET_PATHS:
        node: Any = data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                node = None
                break
            node = node[part]
        if node is not None:
            offenders.append(path)
    # Per-source RTSP credentials (#30) live in a list — scan each for a literal cred.
    capture = data.get("capture")
    if isinstance(capture, dict):
        for i, src in enumerate(capture.get("sources", []) or []):
            if isinstance(src, dict) and src.get("cred") is not None:
                offenders.append("capture.sources.{}.cred".format(i))
    if offenders:
        raise ConfigError(
            "Secret(s) must not be set in YAML — provide them via the environment "
            "/ .env instead (see .env.example): " + ", ".join(offenders)
        )


def _overlay_env(data: Dict[str, Any], env: Dict[str, str]) -> None:
    """Apply env layering in place (secret indirection + documented overrides)."""
    output = data.get("output")
    slack = output.get("slack") if isinstance(output, dict) else None
    if isinstance(slack, dict):
        webhook_env = slack.get("webhook_env")
        # Only resolve when the env var is actually set, so an absent env var does
        # not inject None. A non-string webhook_env is left for schema validation
        # to report (don't index env with it).
        if isinstance(webhook_env, str) and webhook_env in env:
            slack["webhook"] = env[webhook_env]

    capture = data.get("capture")
    if isinstance(capture, dict):
        if "ZED_SOURCE_ID" in env:
            capture["source_id"] = env["ZED_SOURCE_ID"]
        # Resolve each RTSP source's credential from the env var it names (#30).
        for src in capture.get("sources", []) or []:
            if isinstance(src, dict) and src.get("type") == "rtsp":
                cred_env = src.get("cred_env")
                if isinstance(cred_env, str) and cred_env in env:
                    src["cred"] = env[cred_env]


__all__ = ["load_config", "validate_secrets"]
