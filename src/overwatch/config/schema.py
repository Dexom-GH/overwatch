"""Config schema + validation (pydantic v2).

Defines the typed ``AppConfig`` model tree validated against ``configs/*.yaml``,
plus ``validate_config`` (wraps pydantic's ``ValidationError`` in an aggregated,
actionable ``ConfigError``) and ``ConfigError`` itself.

Target code — kept Python 3.8-compatible. The bus/store fields follow ADR-0001
(hybrid: ZeroMQ ephemeral tier + SQLite EventStore durable tier).
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from typing_extensions import Annotated  # typing.Annotated is 3.9+; target is 3.8

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


class ZedSourceConfig(_Strict):
    """A ZED RGB+depth source (the V1 spine)."""

    type: Literal["zed"] = "zed"
    source_id: str
    fps: int = Field(gt=0, json_schema_extra=_MUST_TUNE)


class RtspSourceConfig(_Strict):
    """An RTSP camera source (multi-source, ADR-0006; #30).

    ``url`` carries NO credentials — they resolve from the env var named by
    ``cred_env`` (mirrors ``SlackConfig.webhook_env``). ``cred`` is filled in by
    the loader from that env var; a literal ``cred`` in YAML is rejected (#41).
    """

    type: Literal["rtsp"]
    source_id: str
    url: str
    fps: int = Field(gt=0, json_schema_extra=_MUST_TUNE)
    cred_env: Optional[str] = None
    cred: Optional[str] = None


# Discriminated on ``type`` so a bad/missing type fails fast with a clear error.
SourceConfig = Annotated[
    Union[ZedSourceConfig, RtspSourceConfig], Field(discriminator="type")
]


class CaptureConfig(_Strict):
    """A list of typed capture sources (#30).

    Backward-compat (decided in #30): the legacy scalar form
    ``{source, source_id, fps}`` is normalized to a one-element ``sources`` list,
    so the shipped single-ZED ``default.yaml`` and existing consumers
    (``cfg.capture.source_id`` / ``.fps``) keep working unchanged.
    """

    sources: List[SourceConfig]

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_scalar(cls, data: Any) -> Any:
        if isinstance(data, dict) and "sources" not in data:
            legacy: Dict[str, Any] = {"type": data.get("source", "zed")}
            for key in ("source_id", "fps"):
                if key in data:
                    legacy[key] = data[key]
            rest = {
                k: v for k, v in data.items() if k not in ("source", "source_id", "fps")
            }
            rest["sources"] = [legacy]
            return rest
        return data

    @model_validator(mode="after")
    def _check_sources(self) -> "CaptureConfig":
        if not self.sources:
            raise ValueError("capture.sources must list at least one source")
        ids = [s.source_id for s in self.sources]
        if len(set(ids)) != len(ids):
            raise ValueError("capture source_id values must be unique: {}".format(ids))
        return self

    # Compat accessors so single-source consumers need not change.
    @property
    def primary(self) -> "Union[ZedSourceConfig, RtspSourceConfig]":
        return self.sources[0]

    @property
    def source_id(self) -> str:
        return self.primary.source_id

    @property
    def fps(self) -> int:
        return self.primary.fps

    @property
    def source(self) -> str:
        return self.primary.type


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


# A 2D point. In ``image`` space these are pixels (x,y) in the source frame; in
# ``ground`` space they are metres on the ground plane (requires the on-device
# depth<->ground calibration, #6 — target-side, deferred). See fusion/zones.py.
Point = Tuple[float, float]


class Zone(_Strict):
    """A named counting/region polygon consumed by ``fusion/`` (#12).

    ``space`` selects the coordinate frame (image-plane pixels by default; ground
    metres per ADR-0006). The optional depth band (``depth_min_m``/``depth_max_m``)
    scopes the zone to a range slab for ZED depth de-dup. ``source_id`` ties the
    zone to one camera (None = the single V1 source).
    """

    name: str
    polygon: List[Point]
    space: Literal["image", "ground"] = "image"
    source_id: Optional[str] = None
    depth_min_m: Optional[float] = Field(default=None, ge=0.0)
    depth_max_m: Optional[float] = Field(default=None, ge=0.0)

    @model_validator(mode="after")
    def _check_zone(self) -> "Zone":
        if len(self.polygon) < 3:
            raise ValueError("zone '{}' polygon needs >= 3 points".format(self.name))
        if (
            self.depth_min_m is not None
            and self.depth_max_m is not None
            and self.depth_min_m >= self.depth_max_m
        ):
            raise ValueError(
                "zone '{}' depth_min_m must be < depth_max_m".format(self.name)
            )
        return self


class FenceLine(_Strict):
    """A named boundary line consumed by fence-crossing (#20).

    ``line`` is an ordered polyline (>= 2 points) in ``space`` coords; ``crossing``
    selects which traversal direction fires an event (``any``, or the signed
    ``in_to_out`` / ``out_to_in``, where 'out' is the left side of the directed
    line). ``source_id`` ties the fence to one camera.
    """

    name: str
    line: List[Point]
    space: Literal["image", "ground"] = "image"
    crossing: Literal["any", "in_to_out", "out_to_in"] = "any"
    source_id: Optional[str] = None

    @model_validator(mode="after")
    def _check_line(self) -> "FenceLine":
        if len(self.line) < 2:
            raise ValueError("fence '{}' line needs >= 2 points".format(self.name))
        return self


class FusionConfig(_Strict):
    zones: List[Zone] = Field(default_factory=list)
    fences: List[FenceLine] = Field(default_factory=list)
    health: HealthConfig
    events: EventsConfig


class SlackConfig(_Strict):
    webhook_env: str
    min_severity: Literal["info", "warning", "critical"]
    # Resolved from the env var named by ``webhook_env``; never stored in YAML.
    webhook: Optional[str] = None


class RetentionConfig(_Strict):
    """EventStore retention budget (#40); see output/retention.py + docs/STORAGE.md.

    Bounds the durable tier so 24/7 logging cannot fill the 512 GB NVMe. Either
    bound null = not enforced. Recording/crop rotation budgets are filesystem-side
    (``RetentionPolicy`` / ``enforce_directory``), documented in docs/STORAGE.md.
    """

    max_age_days: Optional[int] = Field(default=90, gt=0)
    max_rows: Optional[int] = Field(default=None, gt=0)
    # How often the supervised retention sweeper enforces the budget (#106).
    interval_seconds: float = Field(default=3600.0, gt=0.0)


class StoreConfig(_Strict):
    backend: Literal["sqlite", "redis"]
    path: Optional[str] = None
    url_env: Optional[str] = None
    retention: RetentionConfig = Field(default_factory=RetentionConfig)

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


class DashboardConfig(_Strict):
    """Operator-console surface (ADR-0008, #124); see output/dashboard/server.py.

    ADR-0008 (supersedes #18): a single-page app built in CI and served as a
    static ``dist/`` bundle, backed by a JSON data API. ``refresh_seconds`` is the
    SPA's poll interval (exposed to the client at ``/api/state``); ``window_seconds``
    is the trailing window of records shown. ``dist_dir`` overrides where the
    backend serves the prebuilt SPA from (``None`` -> the package's ``web/dist``;
    absent -> the backend serves the JSON API only).
    """

    enabled: bool = True  # run the supervised dashboard server with the pipeline (#110)
    host: str = "127.0.0.1"
    port: int = Field(default=8080, gt=0, le=65535)
    refresh_seconds: int = Field(default=5, gt=0)
    window_seconds: float = Field(default=3600.0, gt=0.0)
    alert_limit: int = Field(default=10, gt=0)
    event_limit: int = Field(default=10, gt=0)
    dist_dir: Optional[str] = None  # serve prebuilt SPA from here (None -> web/dist)


class OutputConfig(_Strict):
    slack: SlackConfig
    store: StoreConfig
    throttle: ThrottleConfig = Field(default_factory=ThrottleConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)


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
        # Per-source fps carries the must-tune marker, but it lives in a list (which
        # _collect_must_tune does not recurse), so surface each source's fps here.
        for i in range(len(self.capture.sources)):
            paths.append("capture.sources.{}.fps".format(i))
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
