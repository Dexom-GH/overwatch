"""Discrete events -> alerts (fence-crossing rule engine, #20).

Turns the per-frame track stream into discrete ``Event`` messages — a track
whose centroid crosses a configured fence line — and maps the ones that warrant
attention to an ``Alert``. Fences come from the typed ``FenceLine`` config
(``configs/*.yaml`` ``fusion.fences``, #12).

This is **2D image-plane** geometry (ADR-0006): it works off each track's bbox
centroid, so it is **mono-capable** (no depth dependency — fires on ZED and RTSP
feeds alike) and fully host-runnable / unit-tested. The crossing primitive lives
in ``fusion/zones.py``; this module adds the per-track state (remembering each
track's previous position) needed to detect a crossing between observations, and
honours each fence's ``crossing`` direction filter.

The downstream wiring — subscribe ``infer.track``, publish ``output.alert``
through the shared :class:`~overwatch.output.throttle.AlertThrottle` so spurious
re-crossings de-dup (#42) — is the on-device integration step (needs the live
track stream, #15); the rules here are what that stage drives.

Target code — kept Python 3.8-compatible.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional, Sequence

from overwatch.bus.schemas import Alert, Event, Track
from overwatch.fusion.phrasing import animal_noun
from overwatch.fusion.zones import Point, bbox_centroid, fence_crossing

if TYPE_CHECKING:  # annotation only — avoids a runtime pydantic import in fusion
    from overwatch.config.schema import FenceLine

# Default severity for a fence-crossing alert. Severity-gating against
# output.slack.min_severity is an output-stage concern (#16), not done here.
_FENCE_SEVERITY = "warning"

# Plain-language wording for the crossing direction (#91). "out" is left of the
# directed fence line, so out_to_in reads as "entering", in_to_out as "leaving".
_FRIENDLY_DIRECTION = {"out_to_in": "entering", "in_to_out": "leaving"}


class EventDetector:
    """Detects fence-crossings in the track stream and maps them to alerts.

    Construct with the configured fences (duck-typed: each needs ``name`` /
    ``line`` / ``crossing`` — the pydantic ``FenceLine`` satisfies this). Feed
    each frame's tracks to :meth:`detect_fence_crossing`; it remembers every
    track's last centroid and emits an :class:`~overwatch.bus.schemas.Event` the
    moment a track's motion segment crosses a fence.
    """

    def __init__(self, fences: "Sequence[FenceLine]") -> None:
        self._fences = list(fences)
        self._prev: Dict[int, Point] = {}  # track_id -> last centroid

    def detect_fence_crossing(
        self, timestamp: float, tracks: "Sequence[Track]"
    ) -> List[Event]:
        """Return fence-crossing events for this frame's ``tracks``.

        For each track, compares its current centroid against the one remembered
        from its previous observation; a motion segment that crosses a fence (in a
        direction the fence accepts) yields one ``Event``. A track's first
        observation only seeds its position — it cannot have crossed yet.
        """
        events: List[Event] = []
        for track in tracks:
            curr = bbox_centroid(track.bbox)
            prev = self._prev.get(track.track_id)
            self._prev[track.track_id] = curr
            if prev is None:
                continue
            for fence in self._fences:
                direction = fence_crossing(prev, curr, fence.line)
                if direction is None:
                    continue
                if fence.crossing != "any" and fence.crossing != direction:
                    continue
                events.append(
                    Event(
                        timestamp=timestamp,
                        kind="fence_crossing",
                        track_id=track.track_id,
                        zone_id=fence.name,
                        detail={"direction": direction, "class_name": track.class_name},
                    )
                )
        return events

    def to_alert(self, event: Event) -> Optional[Alert]:
        """Map a fence-crossing ``Event`` to an ``Alert`` (or ``None`` if N/A)."""
        if event.kind != "fence_crossing":
            return None
        direction = event.detail.get("direction", "crossed")
        friendly = _FRIENDLY_DIRECTION.get(direction, "crossing")
        fence = event.zone_id or "fence"
        animal = animal_noun(event.detail.get("class_name"))
        return Alert(
            timestamp=event.timestamp,
            severity=_FENCE_SEVERITY,
            title="Fence crossing",
            message="{} #{} crossed the '{}' fence ({})".format(
                animal, event.track_id, fence, friendly
            ),
            source_event=event,
            detail={"direction": direction},
        )


__all__ = ["EventDetector"]
