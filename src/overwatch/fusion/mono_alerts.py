"""Mono end-to-end fusion glue: live track stream -> per-frame rules -> alerts (#79).

#15 publishes one :class:`~overwatch.bus.schemas.Track` per object on
``infer.track``, but the fusion rules want a per-frame view. This module bridges
that:

- :class:`FrameAssembler` reassembles per-frame ``Track`` lists from the per-object
  stream by grouping on ``frame_id`` — flushing a frame when a later ``frame_id``
  arrives (plus an explicit final ``flush()`` for the trailing frame). No bus
  contract change (#79 decision (a)); assumes the single-source non-decreasing
  ``frame_id`` order #15 produces. ``Track`` carries no timestamp, so the frame's
  processing time (an injected ``clock``) is used for the rules.
- :class:`MonoAlertFanout` feeds each completed frame to the merged consumers —
  fence-crossing (#20), immobility (#19), zone counting (#33) — and emits each
  resulting :class:`~overwatch.bus.schemas.Alert` to an injected ``sink`` callable
  (the runner wraps a throttled :class:`~overwatch.output.slack.SlackAlertSink`).
  ``sink`` is a plain callable so this stays free of an ``output`` import.

Pure host logic — unit-tested off-device; the on-device runner (#79) drives it
from the live #15 pipeline. Target code — Python 3.8 compatible.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Sequence

from overwatch.bus.schemas import Alert, Track
from overwatch.fusion.events import EventDetector
from overwatch.fusion.health import HealthMonitor
from overwatch.fusion.zone_counting import ZoneCounter

if TYPE_CHECKING:  # annotations only — keep this module free of a runtime pydantic import
    from overwatch.config.schema import FenceLine, Zone

FrameSink = Callable[[float, "List[Track]"], None]
AlertSink = Callable[[Alert], None]


class FrameAssembler:
    """Reassemble per-frame ``Track`` lists from the per-object ``infer.track`` stream.

    Calls ``on_frame(timestamp, tracks)`` once per completed frame. A frame is
    considered complete when a ``Track`` with a higher ``frame_id`` arrives; call
    :meth:`flush` at shutdown to emit the trailing frame. ``timestamp`` is the
    frame's processing time from ``clock`` (``Track`` carries none).
    """

    def __init__(
        self,
        on_frame: "FrameSink",
        *,
        clock: "Callable[[], float]" = time.monotonic,
    ) -> None:
        self._on_frame = on_frame
        self._clock = clock
        self._frame_id: "Optional[int]" = None
        self._buffer: "List[Track]" = []

    def add(self, track: Track) -> None:
        if self._frame_id is None:
            self._frame_id = track.frame_id
        elif track.frame_id > self._frame_id:
            self.flush()
            self._frame_id = track.frame_id
        self._buffer.append(track)

    def flush(self) -> None:
        if not self._buffer:
            return
        tracks = self._buffer
        self._buffer = []
        self._on_frame(self._clock(), tracks)


class MonoAlertFanout:
    """Drive the fence / immobility / count rules from reassembled frames -> alerts.

    Construct with the configured fences/zones + thresholds and an ``sink``
    callable; feed the per-object stream via :meth:`on_track` and call
    :meth:`flush` at shutdown. Each frame runs all three rules; every produced
    :class:`Alert` is handed to ``sink``.
    """

    def __init__(
        self,
        sink: "AlertSink",
        *,
        fences: "Optional[Sequence[FenceLine]]" = None,
        zones: "Optional[Sequence[Zone]]" = None,
        zone_thresholds: "Optional[Dict[str, int]]" = None,
        immobility_seconds: float = 600.0,
        move_threshold_px: float = 25.0,
        clock: "Callable[[], float]" = time.monotonic,
    ) -> None:
        self._sink = sink
        self._events = EventDetector(fences or [])
        self._health = HealthMonitor(immobility_seconds, move_threshold_px=move_threshold_px)
        self._counter = ZoneCounter(zones or [], thresholds=zone_thresholds)
        self._assembler = FrameAssembler(self._on_frame, clock=clock)

    def on_track(self, track: Track) -> None:
        """Feed one ``Track`` from the ``infer.track`` stream (bus dispatch thread)."""
        self._assembler.add(track)

    def flush(self) -> None:
        """Flush the trailing frame at shutdown."""
        self._assembler.flush()

    def _on_frame(self, timestamp: float, tracks: "List[Track]") -> None:
        for event in self._events.detect_fence_crossing(timestamp, tracks):
            self._emit(self._events.to_alert(event))
        for track in tracks:
            signal = self._health.update_immobility(timestamp, track)
            if signal is not None:
                self._emit(self._health.to_alert(signal))
        for zone_count in self._counter.count_2d(timestamp, tracks):
            self._emit(self._counter.to_alert(zone_count))

    def _emit(self, alert: "Optional[Alert]") -> None:
        if alert is not None:
            self._sink(alert)


__all__ = ["FrameAssembler", "MonoAlertFanout", "FrameSink", "AlertSink"]
