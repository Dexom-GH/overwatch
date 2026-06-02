"""Tracking interface + track lifecycle.

In V1 tracking runs inside DeepStream's ``nvtracker``. This ABC abstracts the
"associate detections across frames into Tracks" behavior and is also where the
**on-demand ReID trigger policy** is conceptually anchored (ADR-0003): the
tracker decides which tracks currently need identity, and the DeepStream probe
acts on that.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from overwatch.bus.schemas import Detection, Track


class Tracker(ABC):
    """Associates per-frame detections into persistent tracks."""

    @abstractmethod
    def update(self, frame_id: int, detections: List[Detection]) -> List[Track]:
        """Advance the tracker by one frame; return current tracks."""
        raise NotImplementedError

    @abstractmethod
    def needs_identity(self, track: Track) -> bool:
        """Trigger policy for on-demand ReID (ADR-0003).

        Return True when ``track`` should have a MegaDescriptor embedding
        computed (e.g. new track, stale/low-confidence identity, periodic
        refresh, crop quality gate). Config-driven in the real implementation.
        """
        raise NotImplementedError


__all__ = ["Tracker"]
