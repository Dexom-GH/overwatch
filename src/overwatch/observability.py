"""Pipeline observability — metrics + structured logging (#13).

The on-device DoD bars across slices (FPS, per-stage latency, ReID dispatch
count/latency, dropped-frame count; see #8) are only trustworthy if we can
measure them. This module is the measurement plumbing the stages instrument:

- thread-safe metric primitives — :class:`Counter`, :class:`Gauge`,
  :class:`LatencyStat` (with a timing context manager), :class:`RateMeter`
  (sliding-window rate, e.g. FPS);
- a :class:`MetricsRegistry` (create-or-get by name + a ``snapshot``);
- structured (JSON) logging — :class:`StructuredFormatter`, :func:`configure_logging`,
  :func:`log_event`, and :func:`log_metrics` to emit a registry snapshot.

Everything is stdlib-only and host-safe; the #38 supervisor runs stages on
threads, so the primitives and registry are thread-safe. The real numbers come
from the on-device pipeline — instrumenting the live DeepStream/ReID/capture
paths is target-side. Use the metric-name constants below so stages agree on
names. Python 3.8-compatible.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, List

# Canonical metric names — the single source of truth so every stage agrees
# (mirrors the bus topics.py convention). The DoD metrics for #13:
FPS = "fps"                              # RateMeter / Gauge, per stage where meaningful
STAGE_LATENCY = "stage_latency"          # LatencyStat, per-stage processing time
REID_DISPATCH_COUNT = "reid_dispatch_count"  # Counter, on-demand ReID fires
REID_LATENCY = "reid_latency"            # LatencyStat, ReID embedding call time
DROPPED_FRAMES = "dropped_frames"        # Counter


class Counter:
    """A monotonically increasing, thread-safe count (e.g. dropped frames)."""

    def __init__(self) -> None:
        self._value = 0
        self._lock = threading.Lock()

    def inc(self, n: int = 1) -> None:
        with self._lock:
            self._value += n

    @property
    def value(self) -> int:
        return self._value


class Gauge:
    """A thread-safe point-in-time value that can go up or down (e.g. current FPS)."""

    def __init__(self) -> None:
        self._value = 0.0
        self._lock = threading.Lock()

    def set(self, value: float) -> None:
        with self._lock:
            self._value = float(value)

    @property
    def value(self) -> float:
        return self._value


class LatencyStat:
    """Thread-safe latency aggregate: count, total, average, and max (seconds)."""

    def __init__(self) -> None:
        self._count = 0
        self._total = 0.0
        self._max = 0.0
        self._lock = threading.Lock()

    def record(self, seconds: float) -> None:
        with self._lock:
            self._count += 1
            self._total += seconds
            if seconds > self._max:
                self._max = seconds

    @contextmanager
    def time(self, clock: "Callable[[], float]" = time.monotonic) -> "Iterator[None]":
        """Time the wrapped block and record its elapsed duration."""
        start = clock()
        try:
            yield
        finally:
            self.record(clock() - start)

    @property
    def count(self) -> int:
        return self._count

    @property
    def total_s(self) -> float:
        return self._total

    @property
    def avg_s(self) -> float:
        return self._total / self._count if self._count else 0.0

    @property
    def max_s(self) -> float:
        return self._max

    def snapshot(self) -> "Dict[str, float]":
        return {
            "count": self._count,
            "total_s": round(self._total, 6),
            "avg_s": round(self.avg_s, 6),
            "max_s": round(self._max, 6),
        }


class RateMeter:
    """Sliding-window rate estimate over recent event times (e.g. FPS).

    ``tick(now)`` records an arrival time (seconds, monotonic) and returns the
    current rate ``(n-1)/(t_last - t_first)`` over the retained window; ``0.0``
    until two samples exist.
    """

    def __init__(self, window: int = 30) -> None:
        self._window = max(2, window)
        self._times: List[float] = []
        self._lock = threading.Lock()

    def tick(self, now: float) -> float:
        with self._lock:
            self._times.append(now)
            if len(self._times) > self._window:
                self._times = self._times[-self._window:]
            return self._rate_locked()

    def rate(self) -> float:
        with self._lock:
            return self._rate_locked()

    def _rate_locked(self) -> float:
        if len(self._times) < 2:
            return 0.0
        span = self._times[-1] - self._times[0]
        if span <= 0:
            return 0.0
        return (len(self._times) - 1) / span


class MetricsRegistry:
    """Named registry of metrics — create-or-get by name, plus a ``snapshot``."""

    def __init__(self) -> None:
        self._metrics: Dict[str, Any] = {}
        self._lock = threading.Lock()

    def _get_or_create(self, name: str, factory: "Callable[[], Any]", kind: type) -> Any:
        with self._lock:
            existing = self._metrics.get(name)
            if existing is None:
                existing = factory()
                self._metrics[name] = existing
            elif not isinstance(existing, kind):
                raise TypeError(
                    "metric '{}' already registered as {}".format(
                        name, type(existing).__name__
                    )
                )
            return existing

    def counter(self, name: str) -> Counter:
        return self._get_or_create(name, Counter, Counter)

    def gauge(self, name: str) -> Gauge:
        return self._get_or_create(name, Gauge, Gauge)

    def latency(self, name: str) -> LatencyStat:
        return self._get_or_create(name, LatencyStat, LatencyStat)

    def rate(self, name: str, window: int = 30) -> RateMeter:
        return self._get_or_create(name, lambda: RateMeter(window=window), RateMeter)

    def snapshot(self) -> "Dict[str, Any]":
        """Flat dict of metric name -> current value (LatencyStat -> sub-dict)."""
        with self._lock:
            items = list(self._metrics.items())
        out: Dict[str, Any] = {}
        for name, metric in items:
            if isinstance(metric, Counter):
                out[name] = metric.value
            elif isinstance(metric, Gauge):
                out[name] = metric.value
            elif isinstance(metric, RateMeter):
                out[name] = metric.rate()
            elif isinstance(metric, LatencyStat):
                out[name] = metric.snapshot()
        return out


class StructuredFormatter(logging.Formatter):
    """Emit each record as a single JSON line, merging any structured fields.

    Fields passed via ``extra={"fields": {...}}`` (see :func:`log_event`) are
    merged into the JSON object alongside the standard ``ts``/``level``/``logger``/``msg``.
    """

    def format(self, record: "logging.LogRecord") -> str:
        payload: Dict[str, Any] = {
            "ts": round(record.created, 3),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", *, stream: "Any" = None, structured: bool = True) -> None:
    """Install a root handler — structured JSON by default, plain text otherwise."""
    handler = logging.StreamHandler(stream)
    if structured:
        handler.setFormatter(StructuredFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


def log_event(logger: "logging.Logger", level: int, msg: str, **fields: Any) -> None:
    """Log ``msg`` with arbitrary structured ``fields`` (rendered by StructuredFormatter)."""
    logger.log(level, msg, extra={"fields": fields})


def log_metrics(
    logger: "logging.Logger",
    registry: "MetricsRegistry",
    msg: str = "metrics",
    level: int = logging.INFO,
) -> None:
    """Emit a registry snapshot as one structured log line."""
    logger.log(level, msg, extra={"fields": registry.snapshot()})


__all__ = [
    "Counter",
    "Gauge",
    "LatencyStat",
    "RateMeter",
    "MetricsRegistry",
    "StructuredFormatter",
    "configure_logging",
    "log_event",
    "log_metrics",
    "FPS",
    "STAGE_LATENCY",
    "REID_DISPATCH_COUNT",
    "REID_LATENCY",
    "DROPPED_FRAMES",
]
