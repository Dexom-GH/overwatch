"""Health signals — immobility and lameness.

- **Immobility:** a track whose position is ~stationary for an abnormal duration.
- **Lameness:** gait/posture asymmetry over time, from pose keypoints
  (``Pose``) and depth, scored per track.

Both emit ``HealthSignal`` messages; thresholds come from ``configs/default.yaml``.
This is host-runnable, plain-Python logic — prime unit-test territory.
"""

from __future__ import annotations

from typing import List, Optional

from overwatch.bus.schemas import DepthBBox, HealthSignal, Pose


class HealthMonitor:
    """Derives health signals from track motion, depth, and pose. Skeleton."""

    def __init__(self, thresholds: "object" = None) -> None:
        # TODO: load immobility duration / lameness score thresholds from config.
        self._thresholds = thresholds

    def update_immobility(self, timestamp: float, obj: DepthBBox) -> Optional[HealthSignal]:
        """TODO: track per-id position history; flag prolonged immobility."""
        raise NotImplementedError("HealthMonitor.update_immobility")

    def score_lameness(self, timestamp: float, pose: Pose) -> Optional[HealthSignal]:
        """TODO: gait/posture asymmetry from keypoints over time -> lameness score."""
        raise NotImplementedError("HealthMonitor.score_lameness")


__all__ = ["HealthMonitor"]
