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
from typing import Collection, Dict, Optional, Set

from overwatch.bus.schemas import Alert, Event, HealthSignal, Pose, Track
from overwatch.fusion.phrasing import animal_noun, human_duration
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

    ``classes`` optionally restricts immobility to specific detector classes
    (case-insensitive). ``None`` (default) watches every track; a collection (e.g.
    ``{"sheep", "goat", "cow"}``) makes only those classes trip immobility — so a
    stationary TV/chair in a COCO scene no longer fires.

    ``class_seconds`` gives **per-class dwell thresholds** (case-insensitive), so a
    class that legitimately holds still for long (e.g. ``person`` — people sit, rest,
    sleep) needs a much longer dwell before alerting than livestock. A class without
    an override falls back to ``immobility_seconds``. Other rules are unaffected.
    """

    def __init__(
        self,
        immobility_seconds: float,
        move_threshold_px: float = 25.0,
        classes: "Optional[Collection[str]]" = None,
        class_seconds: "Optional[Dict[str, float]]" = None,
    ) -> None:
        self._immobility_seconds = immobility_seconds
        self._move_threshold = move_threshold_px
        # Normalize the allow-list to lower-case for case-insensitive matching;
        # None means "no filter — watch every class" (the original behaviour).
        self._classes: "Optional[Set[str]]" = (
            None if classes is None else {c.lower() for c in classes}
        )
        # Per-class dwell threshold overrides (lower-cased keys); fall back to
        # ``immobility_seconds`` for any class not listed.
        self._class_seconds: "Dict[str, float]" = (
            {} if not class_seconds else {k.lower(): float(v) for k, v in class_seconds.items()}
        )
        self._dwell: Dict[int, _TrackDwell] = {}

    def _threshold_for(self, class_name: str) -> float:
        """The dwell threshold for ``class_name`` — its override, else the default."""
        return self._class_seconds.get(class_name.lower(), self._immobility_seconds)

    @classmethod
    def from_config(cls, cfg: object, move_threshold_px: float = 25.0) -> "HealthMonitor":
        """Build from a health-config object exposing ``immobility_seconds`` (duck-typed).

        Also reads optional ``immobility_classes`` (allow-list) and
        ``immobility_class_seconds`` (per-class threshold overrides) if present.
        """
        return cls(
            immobility_seconds=cfg.immobility_seconds,  # type: ignore[attr-defined]
            move_threshold_px=move_threshold_px,
            classes=getattr(cfg, "immobility_classes", None),
            class_seconds=getattr(cfg, "immobility_class_seconds", None),
        )

    def update_immobility(self, timestamp: float, track: Track) -> Optional[HealthSignal]:
        """Advance the dwell timer for ``track``; return a signal if it just crossed.

        First sighting only seeds the anchor. On later sightings: a centroid that
        drifted past ``move_threshold_px`` resets the anchor/timer (the animal
        moved — AC2); otherwise, once it has been stationary for
        ``immobility_seconds``, one ``HealthSignal`` is emitted for this episode.

        Tracks whose class is outside the configured ``classes`` allow-list are
        ignored entirely (no dwell kept) — immobility is reported only for the
        classes you care about.
        """
        if self._classes is not None and track.class_name.lower() not in self._classes:
            return None
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

        if not dwell.emitted and (timestamp - dwell.since) >= self._threshold_for(track.class_name):
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
        animal = animal_noun(signal.detail.get("class_name"))
        for_str = (
            " for {}".format(human_duration(secs)) if isinstance(secs, (int, float)) else ""
        )
        return Alert(
            timestamp=signal.timestamp,
            severity=_IMMOBILITY_SEVERITY,
            title="Immobility",
            message="{} #{} has been stationary{}".format(
                animal, signal.track_id, for_str
            ),
            source_event=Event(
                timestamp=signal.timestamp,
                kind="immobility",
                track_id=signal.track_id,
            ),
            detail={"track_id": signal.track_id, "kind": signal.kind},
        )

    def score_lameness(self, timestamp: float, pose: Pose) -> Optional[HealthSignal]:
        """Lameness scoring — DEFERRED TO V2 (#22); requires pose + depth over time."""
        raise NotImplementedError("HealthMonitor.score_lameness — V2 (#22)")


__all__ = ["HealthMonitor"]
