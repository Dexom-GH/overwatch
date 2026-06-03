"""Zone counting -> threshold alert.

Two paths, one per camera type (ADR-0006 capability split):

- **Mono 2D (#33, implemented):** :meth:`ZoneCounter.count_2d` counts tracks whose
  bbox centroid falls inside each configured zone — plain image-plane membership,
  **no depth de-dup**. Mono-capable (fires on RTSP feeds), fully host-runnable.
- **Depth-deduped (#16, ZED):** :meth:`ZoneCounter.count` would use ``DepthBBox``
  depth to separate animals that overlap in 2D but sit at different ranges — the
  ZED-only "truthful count". Still a skeleton; #16 owns it.

A zone whose count crosses a configured threshold escalates to an ``Alert`` via
:meth:`to_alert`, tagged with the **zone's** ``source_id`` (a ``Track`` carries no
source — per-track→camera attribution is #32/#34). Sustained crossings de-dup
through the shared :class:`~overwatch.output.throttle.AlertThrottle` (#42): the
alert carries an ``Event(kind="zone_count", zone_id=...)`` so the throttle keys
de-dup per zone. Zones are the typed ``Zone`` config (#12).

Target code — kept Python 3.8-compatible.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional, Sequence

from overwatch.bus.schemas import Alert, DepthBBox, Event, Track, ZoneCount
from overwatch.fusion.zones import bbox_centroid, point_in_polygon

if TYPE_CHECKING:  # annotation only — avoids a runtime pydantic import in fusion
    from overwatch.config.schema import Zone

_COUNT_SEVERITY = "warning"


class ZoneCounter:
    """Counts tracks per configured zone and escalates threshold crossings.

    Construct with the configured zones (duck-typed: each needs ``name`` /
    ``polygon`` / ``source_id`` — the pydantic ``Zone`` satisfies this) and a
    threshold policy: a per-zone ``thresholds`` map (zone name -> count) and/or a
    ``default_threshold`` applied to zones without an explicit one. A zone with no
    threshold is counted but never alerts.
    """

    def __init__(
        self,
        zones: "Sequence[Zone]",
        thresholds: "Optional[Dict[str, int]]" = None,
        default_threshold: "Optional[int]" = None,
    ) -> None:
        self._zones = list(zones)
        self._thresholds = dict(thresholds or {})
        self._default_threshold = default_threshold

    def count_2d(self, timestamp: float, tracks: "Sequence[Track]") -> List[ZoneCount]:
        """Per-zone 2D counts: how many track centroids fall inside each zone.

        Image-plane membership only — depth is ignored (mono path, #33). Returns
        one :class:`~overwatch.bus.schemas.ZoneCount` per configured zone.
        """
        counts: List[ZoneCount] = []
        for zone in self._zones:
            n = 0
            for track in tracks:
                if point_in_polygon(bbox_centroid(track.bbox), zone.polygon):
                    n += 1
            counts.append(ZoneCount(zone_id=zone.name, timestamp=timestamp, count=n))
        return counts

    def to_alert(self, zone_count: ZoneCount) -> Optional[Alert]:
        """Escalate a zone count to an ``Alert`` when it meets the zone's threshold.

        Returns ``None`` if the zone has no configured threshold or the count is
        below it. The alert is tagged with the zone's ``source_id`` and carries a
        ``zone_count`` source event so the shared throttle de-dups per zone.
        """
        threshold = self._threshold_for(zone_count.zone_id)
        if threshold is None or zone_count.count < threshold:
            return None
        zone = self._zone_by_id(zone_count.zone_id)
        source_id = getattr(zone, "source_id", None) if zone is not None else None
        tag = " [{}]".format(source_id) if source_id else ""
        event = Event(
            timestamp=zone_count.timestamp,
            kind="zone_count",
            zone_id=zone_count.zone_id,
            detail={"count": zone_count.count, "threshold": threshold, "source_id": source_id},
        )
        return Alert(
            timestamp=zone_count.timestamp,
            severity=_COUNT_SEVERITY,
            title="Zone count",
            message="Zone '{}'{}: {} (>= {})".format(
                zone_count.zone_id, tag, zone_count.count, threshold
            ),
            source_event=event,
            detail={"source_id": source_id, "count": zone_count.count},
        )

    # -- internals -----------------------------------------------------------

    def _threshold_for(self, zone_id: str) -> "Optional[int]":
        return self._thresholds.get(zone_id, self._default_threshold)

    def _zone_by_id(self, zone_id: str) -> "Optional[Zone]":
        for zone in self._zones:
            if zone.name == zone_id:
                return zone
        return None

    def count(self, timestamp: float, objects: List[DepthBBox]) -> List[ZoneCount]:
        """Depth-deduplicated count (ZED) — DEFERRED to #16; still a skeleton."""
        raise NotImplementedError("ZoneCounter.count — depth de-dup is #16")


__all__ = ["ZoneCounter"]
