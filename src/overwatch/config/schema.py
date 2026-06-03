"""Config schema + validation (pydantic v2).

Defines the typed ``AppConfig`` model tree validated against ``configs/*.yaml``,
plus ``validate_config`` (wraps pydantic's ``ValidationError`` in an aggregated,
actionable ``ConfigError``) and ``ConfigError`` itself.

Target code — kept Python 3.8-compatible. The bus/store fields follow ADR-0001
(hybrid: ZeroMQ ephemeral tier + SQLite EventStore durable tier).
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

# Fields whose defaults are placeholders that MUST be confirmed/tuned on-device.
_MUST_TUNE: Dict[str, Any] = {"must_tune": True}


class _Strict(BaseModel):
    """Base: reject unknown keys so typos fail fast with a clear error."""

    model_config = ConfigDict(extra="forbid")


class BusConfig(_Strict):
    transport: Literal["zeromq", "redis"]
    # ZeroMQ endpoint lives in YAML (not a secret). For Redis the URL is a secret
    # resolved from the env var named by ``url_env``.
    endpoint: Optional[str] = None
    url_env: Optional[str] = None

    @model_validator(mode="after")
    def _check_transport_fields(self) -> "BusConfig":
        if self.transport == "zeromq" and not self.endpoint:
            raise ValueError("endpoint is required when transport is 'zeromq'")
        if self.transport == "redis" and not self.url_env:
            raise ValueError("url_env is required when transport is 'redis'")
        return self


class CaptureConfig(_Strict):
    source: Literal["zed"]
    source_id: str
    fps: int = Field(gt=0, json_schema_extra=_MUST_TUNE)


class ReidConfig(_Strict):
    engine: str
    refresh_seconds: int = Field(gt=0, json_schema_extra=_MUST_TUNE)
    min_crop_confidence: float = Field(ge=0.0, le=1.0, json_schema_extra=_MUST_TUNE)


class InferenceConfig(_Strict):
    detector_config: str
    tracker_config: str
    reid: ReidConfig


class HealthConfig(_Strict):
    immobility_seconds: int = Field(gt=0, json_schema_extra=_MUST_TUNE)
    lameness_score_threshold: float = Field(ge=0.0, le=1.0, json_schema_extra=_MUST_TUNE)


class EventsConfig(_Strict):
    fence_zones: List[str] = Field(default_factory=list)


class Zone(BaseModel):
    """Permissive on purpose: the real zone format is a soft dep on #12."""

    model_config = ConfigDict(extra="allow")
    name: str


class FusionConfig(_Strict):
    zones: List[Zone] = Field(default_factory=list)
    health: HealthConfig
    events: EventsConfig


class SlackConfig(_Strict):
    webhook_env: str
    min_severity: Literal["info", "warning", "critical"]
    # Resolved from the env var named by ``webhook_env``; never stored in YAML.
    webhook: Optional[str] = None


class StoreConfig(_Strict):
    backend: Literal["sqlite", "redis"]
    path: Optional[str] = None
    url_env: Optional[str] = None

    @model_validator(mode="after")
    def _check_backend_fields(self) -> "StoreConfig":
        if self.backend == "sqlite" and not self.path:
            raise ValueError("path is required when store backend is 'sqlite'")
        if self.backend == "redis" and not self.url_env:
            raise ValueError("url_env is required when store backend is 'redis'")
        return self


class ThrottleConfig(_Strict):
    """Alert de-dup / rate-limit knobs (#42); see output/throttle.py.

    ``cooldown_seconds`` 0 disables de-dup; ``max_per_window`` null = unrate-limited.
    """

    cooldown_seconds: float = Field(default=60.0, ge=0.0)
    max_per_window: Optional[int] = Field(default=None, gt=0)
    rate_window_seconds: float = Field(default=60.0, gt=0.0)


class OutputConfig(_Strict):
    slack: SlackConfig
    store: StoreConfig
    throttle: ThrottleConfig = Field(default_factory=ThrottleConfig)


class AppConfig(_Strict):
    bus: BusConfig
    capture: CaptureConfig
    inference: InferenceConfig
    fusion: FusionConfig
    output: OutputConfig

    def must_tune_fields(self) -> List[str]:
        """Dotted paths of fields that must be confirmed/tuned on-device.

        Returns every field carrying the must-tune marker (and empty ``zones``);
        it does not check whether a value has since been changed from its default.
        """
        paths = _collect_must_tune(self)
        if not self.fusion.zones:
            paths.append("fusion.zones")
        return paths


def _collect_must_tune(model: BaseModel, prefix: str = "") -> List[str]:
    out: List[str] = []
    for name, field in type(model).model_fields.items():
        path = prefix + name
        extra = field.json_schema_extra
        if isinstance(extra, dict) and extra.get("must_tune"):
            out.append(path)
        value = getattr(model, name)
        if isinstance(value, BaseModel):
            out.extend(_collect_must_tune(value, path + "."))
    return out


class ConfigError(Exception):
    """Raised when configuration is invalid; message aggregates all problems."""


def validate_config(data: object) -> AppConfig:
    """Validate a merged config dict into an ``AppConfig`` or raise ``ConfigError``."""
    try:
        return AppConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(_format_errors(exc)) from exc


def _format_errors(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "(root)"
        lines.append("  {}: {}".format(loc, err["msg"]))
    return "Invalid configuration:\n" + "\n".join(lines)
