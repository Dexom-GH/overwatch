"""Health signals — immobility (and lameness, deferred to V2).

- **Immobility (#19):** a track whose centroid stays put longer than
  ``immobility_seconds`` raises an immobility ``HealthSignal`` -> ``Alert``.
- **Lameness:** gait/posture asymmetry from pose + depth — **deferred to V2**
  (#22); the hook stays here but is not implemented.

Immobility is **2D centroid dwell** logic (ADR-0006: mono-capable, no depth — it
fires on ZED and RTSP feeds alike), so it is plain host-runnable Python and prime
unit-test territory. The monitor keeps a per-track anchor + dwell timer; movement
beyond ``move_threshold_px`` resets the timer, and each immobile episode raises at
most one signal (so it does not re-fire every frame while the animal rests).

The downstream wiring — subscribe ``infer.track``, publish ``output.alert``
through the Slack sink (#20/#73) — is the on-device integration step (needs the
live track stream, #15). Thresholds here are placeholders pending a ground-truth
validation method (see the issue).

Target code — kept Python 3.8-compatible.
"""

from __future__ import annotations

import math
from typing import Dict, Optional

from overwatch.bus.schemas import Alert, HealthSignal, Pose, Track
from overwatch.fusion.zones import Point, bbox_centroid

_IMMOBILITY_SEVERITY = "warning"


class _TrackDwell:
    """Per-track immobility state: where it settled, when, and whether flagged."""

    __slots__ = ("anchor", "since", "emitted")

    def __init__(self, anchor: Point, since: float) -> None:
        self.anchor = anchor
        self.since = since
        self.emitted = False


class HealthMonitor:
    """Derives health signals from track motion. V1: immobility (2D dwell).

    ``immobility_seconds`` is the dwell threshold; ``move_threshold_px`` is how far
    a centroid may drift (pixels) and still count as "the same spot". Both are
    placeholders to be tuned against ground truth (see #19).
    """

    def __init__(
        self, immobility_seconds: float, move_threshold_px: float = 25.0
    ) -> None:
        self._immobility_seconds = immobility_seconds
        self._move_threshold = move_threshold_px
        self._dwell: Dict[int, _TrackDwell] = {}

    @classmethod
    def from_config(cls, cfg: object, move_threshold_px: float = 25.0) -> "HealthMonitor":
        """Build from a health-config object exposing ``immobility_seconds`` (duck-typed)."""
        return cls(
            immobility_seconds=cfg.immobility_seconds,  # type: ignore[attr-defined]
            move_threshold_px=move_threshold_px,
        )

    def update_immobility(self, timestamp: float, track: Track) -> Optional[HealthSignal]:
        """Advance the dwell timer for ``track``; return a signal if it just crossed.

        First sighting only seeds the anchor. On later sightings: a centroid that
        drifted past ``move_threshold_px`` resets the anchor/timer (the animal
        moved — AC2); otherwise, once it has been stationary for
        ``immobility_seconds``, one ``HealthSignal`` is emitted for this episode.
        """
        curr = bbox_centroid(track.bbox)
        dwell = self._dwell.get(track.track_id)
        if dwell is None:
            self._dwell[track.track_id] = _TrackDwell(anchor=curr, since=timestamp)
            return None

        if math.dist(curr, dwell.anchor) > self._move_threshold:
            dwell.anchor = curr
            dwell.since = timestamp
            dwell.emitted = False
            return None

        if not dwell.emitted and (timestamp - dwell.since) >= self._immobility_seconds:
            dwell.emitted = True
            stationary = timestamp - dwell.since
            return HealthSignal(
                track_id=track.track_id,
                timestamp=timestamp,
                kind="immobility",
                score=1.0,
                detail={"stationary_seconds": stationary, "class_name": track.class_name},
            )
        return None

    def to_alert(self, signal: HealthSignal) -> Optional[Alert]:
        """Map an immobility ``HealthSignal`` to an ``Alert`` (or ``None`` if N/A)."""
        if signal.kind != "immobility":
            return None
        secs = signal.detail.get("stationary_seconds")
        for_str = " for {:.0f}s".format(secs) if isinstance(secs, (int, float)) else ""
        return Alert(
            timestamp=signal.timestamp,
            severity=_IMMOBILITY_SEVERITY,
            title="Immobility",
            message="Track {} immobile{}".format(signal.track_id, for_str),
            detail={"track_id": signal.track_id, "kind": signal.kind},
        )

    def score_lameness(self, timestamp: float, pose: Pose) -> Optional[HealthSignal]:
        """Lameness scoring — DEFERRED TO V2 (#22); requires pose + depth over time."""
        raise NotImplementedError("HealthMonitor.score_lameness — V2 (#22)")


__all__ = ["HealthMonitor"]
