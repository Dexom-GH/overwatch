"""Liveness → Slack: turn a source going silent into a throttled alert (#136).

:class:`LivenessMonitor` watches a :class:`~overwatch.output.liveness.LivenessTracker`
and emits an :class:`~overwatch.bus.schemas.Alert` on the **edges**: a source that
falls silent past the window raises one ``source_degraded`` alert; when its frames
resume it raises one ``source_recovered`` alert. Edge detection means one alert per
transition; the existing :class:`~overwatch.output.throttle.AlertThrottle` is the
belt-and-suspenders de-dup the issue calls for, keyed **per source** (the source_id
rides in the originating ``Event.detail`` — no bus schema change).

Drive :meth:`check` on a timer (the supervised app calls it like the retention
sweeper). Pure/host-safe with an injected clock, so the degraded/recovered state
machine is unit-tested off-device; the real source loss on the Jetson is the
on-device bar (AC6). Python 3.8-compatible.
"""

from __future__ import annotations

import time
from typing import Callable, Dict, Optional

from overwatch.bus.schemas import Alert, Event
from overwatch.output.liveness import LivenessTracker, SourceLiveness
from overwatch.output.throttle import AlertThrottle

DEGRADED_KIND = "source_degraded"
RECOVERED_KIND = "source_recovered"

# Default minimum gap between repeated degraded alerts for the same source. Edge
# detection already fires once per transition; this caps any pathological re-fire.
_DEFAULT_COOLDOWN_S = 300.0

AlertSink = Callable[["Alert"], None]
NameFn = Callable[[str], str]


def liveness_alert_key(alert: "Alert"):
    """De-dup key for liveness alerts: ``(kind, source_id)`` from ``Event.detail``.

    The stock :func:`~overwatch.output.throttle.default_alert_key` keys on
    zone/track ids, which liveness has neither of — so the monitor keys on the
    ``source_id`` carried in the originating event's ``detail`` (no schema change).
    """
    event = alert.source_event
    if event is None:
        return (alert.title, None)
    return (event.kind, event.detail.get("source_id"))


class LivenessMonitor:
    """Emits throttled degraded/recovered alerts as sources fall silent / return.

    ``sink`` receives each :class:`Alert` to emit (wire it to the Slack sink).
    ``name_fn`` maps a ``source_id`` to a human label for the message. ``clock`` is
    the monotonic time source shared with the tracker/throttle (injected in tests).
    """

    def __init__(
        self,
        tracker: "LivenessTracker",
        sink: "AlertSink",
        *,
        cooldown_seconds: float = _DEFAULT_COOLDOWN_S,
        clock: "Callable[[], float]" = time.monotonic,
        name_fn: "Optional[NameFn]" = None,
    ) -> None:
        self._tracker = tracker
        self._sink = sink
        self._clock = clock
        self._name_fn = name_fn if name_fn is not None else (lambda sid: sid)
        self._throttle = AlertThrottle(
            cooldown_seconds=cooldown_seconds, key_fn=liveness_alert_key, clock=clock
        )
        # Last observed up-state per source. Seeded optimistically (True) on first
        # sight so a source that starts up normally never crosses a false edge,
        # while a dead-from-start source crosses True->down once the window passes.
        self._prev_up: "Dict[str, bool]" = {}

    def check(self, now: "Optional[float]" = None) -> None:
        """Evaluate liveness once and emit any degraded/recovered edge alerts."""
        now = self._clock() if now is None else now
        snapshot = self._tracker.snapshot(now)
        for source in snapshot.sources:
            prev_up = self._prev_up.get(source.source_id, True)
            if prev_up and not source.up:
                self._emit(self._degraded_alert(source, now))
            elif not prev_up and source.up:
                self._emit(self._recovered_alert(source, now))
            self._prev_up[source.source_id] = source.up

    def _emit(self, alert: "Alert") -> None:
        if self._throttle.allow(alert):
            self._sink(alert)

    def _degraded_alert(self, source: "SourceLiveness", now: float) -> "Alert":
        age = source.last_frame_age_s
        for_str = " for {}s".format(int(age)) if age is not None else ""
        label = self._name_fn(source.source_id)
        return Alert(
            timestamp=now,
            severity="warning",
            title="Source degraded",
            message="Camera `{}` has stopped sending frames{} — check the feed".format(label, for_str),
            source_event=Event(timestamp=now, kind=DEGRADED_KIND, detail={"source_id": source.source_id}),
            detail={"source_id": source.source_id, "kind": "liveness"},
        )

    def _recovered_alert(self, source: "SourceLiveness", now: float) -> "Alert":
        label = self._name_fn(source.source_id)
        return Alert(
            timestamp=now,
            severity="info",
            title="Source recovered",
            message="Camera `{}` is sending frames again".format(label),
            source_event=Event(timestamp=now, kind=RECOVERED_KIND, detail={"source_id": source.source_id}),
            detail={"source_id": source.source_id, "kind": "liveness"},
        )


__all__ = ["LivenessMonitor", "liveness_alert_key", "DEGRADED_KIND", "RECOVERED_KIND"]
