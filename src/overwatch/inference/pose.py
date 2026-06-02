"""Pose estimation interface — feeds health (lameness) scoring.

Pose keypoints over time give gait/posture signals that ``fusion/health.py``
turns into a lameness score. Kept as an interface in V1; the concrete model and
whether it runs in-pipeline or out-of-band is an implementation choice.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from overwatch.bus.schemas import Frame, Pose, Track


class PoseEstimator(ABC):
    """Estimates pose keypoints for tracked animals in a frame."""

    @abstractmethod
    def estimate(self, frame: Frame, tracks: List[Track]) -> List[Pose]:
        """Return a Pose per track for which pose could be estimated."""
        raise NotImplementedError


__all__ = ["PoseEstimator"]
