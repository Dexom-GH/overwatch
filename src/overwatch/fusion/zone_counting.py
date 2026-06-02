"""Zone counting with depth-based de-duplication.

Plain 2D counting double-counts overlapping animals. Using ``DepthBBox`` depth,
animals that overlap in 2D but sit at different ranges are separated, giving a
truthful per-zone count. Zones are defined in ``configs/default.yaml``.
"""

from __future__ import annotations

from typing import List

from overwatch.bus.schemas import DepthBBox, ZoneCount


class ZoneCounter:
    """Counts animals per configured zone, depth-deduplicated. Skeleton."""

    def __init__(self, zones: "object" = None) -> None:
        # TODO: load zone polygons/ranges from config.
        self._zones = zones

    def count(self, timestamp: float, objects: List[DepthBBox]) -> List[ZoneCount]:
        """Return per-zone counts for the given depth-fused objects.

        TODO: assign each object to a zone (2D polygon + depth band), de-dup by
        depth separation, aggregate counts (optionally per class_name).
        """
        raise NotImplementedError("ZoneCounter.count")


__all__ = ["ZoneCounter"]
