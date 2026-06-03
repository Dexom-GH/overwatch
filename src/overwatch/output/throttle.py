"""Shared alert de-dup / rate-limit utility (#42).

One reusable throttle so that "one crossing != an alert storm" is implemented
*once* and reused by the counting / health / fence-crossing slices
(#16 / #19 / #20 / #33) instead of each carrying its own inline copy. It applies
two independent guards to an :class:`~overwatch.bus.schemas.Alert`:

- **De-dup (cooldown):** identical alerts — same ``(event_type, zone_id|track_id)``
  key — collapse to one within ``cooldown_seconds`` (default 60 s).
- **Rate-limit (burst cap):** at most ``max_per_window`` alerts are emitted within
  any ``rate_window_seconds`` window, across all keys (``None`` = unlimited).

A de-duped alert is suppressed *before* the rate check, so repeats never spend
rate budget. Pure host logic; the clock is injected so windows are testable
without real time. ``ThrottledAlertSink`` (``slack.py``) wraps a sink with this.

Target code — kept Python 3.8-compatible.
"""

from __future__ import annotations

import time
from typing import Callable, Dict, List, Optional, Tuple

from overwatch.bus.schemas import Alert

# De-dup key: (event_type, identifier) where identifier is the zone or track.
AlertKey = Tuple[str, Optional[str]]
KeyFn = Callable[[Alert], AlertKey]


def default_alert_key(alert: "Alert") -> AlertKey:
    """Derive the de-dup key ``(event_type, zone_id|track_id)`` from an alert.

    Uses the originating :class:`~overwatch.bus.schemas.Event`: its ``kind`` is the
    event type, and the identifier is the ``zone_id`` for zone/count events,
    otherwise the ``track_id`` for per-animal events. Alerts with no source event
    fall back to keying on the alert ``title`` with no identifier.
    """
    event = alert.source_event
    if event is None:
        return (alert.title, None)
    if event.zone_id is not None:
        identifier = event.zone_id  # type: Optional[str]
    elif event.track_id is not None:
        identifier = str(event.track_id)
    else:
        identifier = None
    return (event.kind, identifier)


class AlertThrottle:
    """De-dup + rate-limit gate for outbound alerts.

    Call :meth:`allow` for each candidate alert; it returns ``True`` if the alert
    should be emitted and records it, or ``False`` if it is suppressed (a recent
    duplicate, or over the rate cap).
    """

    def __init__(
        self,
        cooldown_seconds: float = 60.0,
        max_per_window: "Optional[int]" = None,
        rate_window_seconds: float = 60.0,
        *,
        key_fn: "KeyFn" = default_alert_key,
        clock: "Callable[[], float]" = time.monotonic,
    ) -> None:
        self._cooldown = cooldown_seconds
        self._max_per_window = max_per_window
        self._rate_window = rate_window_seconds
        self._key_fn = key_fn
        self._clock = clock
        self._last_emit: Dict[AlertKey, float] = {}  # key -> last emit time
        self._emits: List[float] = []                # emit times within rate window

    @classmethod
    def from_config(
        cls,
        cfg: object,
        *,
        key_fn: "KeyFn" = default_alert_key,
        clock: "Callable[[], float]" = time.monotonic,
    ) -> "AlertThrottle":
        """Build a throttle from an output-throttle config object.

        ``cfg`` is duck-typed (the pydantic ``config.schema.ThrottleConfig`` or any
        object exposing ``cooldown_seconds`` / ``max_per_window`` /
        ``rate_window_seconds``), so this module stays free of a config import.
        """
        return cls(
            cooldown_seconds=cfg.cooldown_seconds,        # type: ignore[attr-defined]
            max_per_window=cfg.max_per_window,            # type: ignore[attr-defined]
            rate_window_seconds=cfg.rate_window_seconds,  # type: ignore[attr-defined]
            key_fn=key_fn,
            clock=clock,
        )

    def allow(self, alert: "Alert") -> bool:
        """Whether ``alert`` should be emitted now; records it if so."""
        now = self._clock()

        # De-dup: drop a repeat of the same key inside the cooldown window.
        key = self._key_fn(alert)
        last = self._last_emit.get(key)
        if last is not None and now - last < self._cooldown:
            return False

        # Rate-limit: cap emitted alerts within the trailing rate window.
        if self._max_per_window is not None:
            self._emits = [t for t in self._emits if now - t < self._rate_window]
            if len(self._emits) >= self._max_per_window:
                return False

        self._last_emit[key] = now
        self._emits.append(now)
        return True


__all__ = ["AlertThrottle", "default_alert_key", "AlertKey", "KeyFn"]
