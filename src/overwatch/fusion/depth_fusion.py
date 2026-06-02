"""Depth fusion — the hybrid integration seam (ADR-0002).

DeepStream metadata is 2D-bbox-centric with no per-object depth. This module
aligns the matching ZED depth frame (from ``topics.CAPTURE_DEPTH``) to the 2D
tracks (from ``topics.INFER_TRACK``) by ``frame_id``, and produces ``DepthBBox``
messages carrying a representative depth (and a coarse body-size cue) per object.

This is the place to watch for accuracy/latency: spatial+temporal alignment of
depth to bboxes is the cost of the hybrid approach. If it proves limiting, ADR-
0002's custom-source alternative is the escalation path.
"""

from __future__ import annotations

from typing import Any, List

from overwatch.bus.schemas import DepthBBox, DepthFrame, Track


class DepthFusion:
    """Aligns depth to 2D tracks, emitting DepthBBox. Skeleton."""

    def fuse(self, tracks: List[Track], depth: DepthFrame) -> List[DepthBBox]:
        """Return a DepthBBox per track using ``depth`` (same ``frame_id``).

        TODO: sample depth within each bbox (robust statistic, not raw center),
        reject invalid/zero depth, derive a coarse size_estimate from bbox +
        depth (body-size ID cue).
        """
        raise NotImplementedError("DepthFusion.fuse")

    @staticmethod
    def representative_depth(bbox: "Any", depth: "Any") -> float:
        """TODO: robust per-object depth (e.g. median of valid pixels in bbox)."""
        raise NotImplementedError("DepthFusion.representative_depth")


__all__ = ["DepthFusion"]
