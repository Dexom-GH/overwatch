"""Depth fusion — the hybrid integration seam (ADR-0002).

DeepStream metadata is 2D-bbox-centric with no per-object depth. This module
aligns the matching ZED depth frame (from ``topics.CAPTURE_DEPTH``) to the 2D
tracks (from ``topics.INFER_TRACK``) by ``frame_id``, and produces ``DepthBBox``
messages carrying a representative depth (and a coarse body-size cue) per object.

This is the place to watch for accuracy/latency: spatial+temporal alignment of
depth to bboxes is the cost of the hybrid approach. If it proves limiting, ADR-
0002's custom-source alternative is the escalation path.

Alignment strategy (recorded in ADR-0002, spike #6):

- **Temporal:** RGB and depth come from the *same* ZED ``grab()`` and share a
  ``frame_id`` (see ``capture/zed_source.py``). Fusion joins strictly on that
  ``frame_id`` — zero temporal skew by construction. ``fuse`` raises if asked to
  align a track to a depth frame with a different ``frame_id`` (a wiring bug).
  The on-device requirement this implies is that the capture ``frame_id`` must
  survive the DeepStream leg so the probe can tag each ``Track`` with it (see
  the spike findings in ADR-0002 / the ``deepstream-pipeline`` skill).
- **Spatial:** a bbox corner-to-corner depth crop is contaminated by background
  (animals don't fill the box). We sample the central ``inner_fraction`` of the
  box and take the **median of valid pixels** — robust to the holes/outliers the
  ZED leaves (0 / NaN / +inf for unmeasured, occluded, too-near/too-far).

``numpy`` is available on host and device; this module is import-safe on the
host (no pyzed/DeepStream), so the alignment math is unit-tested off-device.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, List

import numpy as np

from overwatch.bus.schemas import DepthBBox, DepthFrame, Track

if TYPE_CHECKING:  # tooling only
    from overwatch.bus.schemas import BBox


class DepthFusion:
    """Aligns depth to 2D tracks, emitting ``DepthBBox``.

    Parameters tune the robust spatial sample:

    - ``inner_fraction``: fraction of the bbox (per axis, centered) to sample;
      < 1.0 trims edge background. ``1.0`` samples the whole box.
    - ``min_depth_m`` / ``max_depth_m``: valid metric-depth window; pixels
      outside it (and non-finite pixels) are rejected before the median.
    """

    def __init__(
        self,
        *,
        inner_fraction: float = 0.6,
        min_depth_m: float = 0.3,
        max_depth_m: float = 20.0,
    ) -> None:
        if not 0.0 < inner_fraction <= 1.0:
            raise ValueError("inner_fraction must be in (0, 1]")
        if not 0.0 <= min_depth_m < max_depth_m:
            raise ValueError("require 0 <= min_depth_m < max_depth_m")
        self._inner_fraction = inner_fraction
        self._min_depth_m = min_depth_m
        self._max_depth_m = max_depth_m

    def fuse(self, tracks: List[Track], depth: DepthFrame) -> List[DepthBBox]:
        """Return a ``DepthBBox`` per track that has a valid depth sample.

        Tracks are matched to ``depth`` by ``frame_id`` (temporal contract).
        A track whose bbox yields no valid depth (all holes / out of range) is
        dropped — emitting a ``NaN`` depth would poison downstream counting.
        """
        out: List[DepthBBox] = []
        for track in tracks:
            if track.frame_id != depth.frame_id:
                raise ValueError(
                    "frame_id mismatch: track {} vs depth {} — fuse aligns a "
                    "track to the depth frame of the SAME grab".format(
                        track.frame_id, depth.frame_id
                    )
                )
            depth_m = self.representative_depth(track.bbox, depth.depth)
            if math.isnan(depth_m):
                continue
            x1, y1, x2, y2 = track.bbox
            apparent = math.sqrt(abs(x2 - x1) * abs(y2 - y1))
            out.append(
                DepthBBox(
                    track_id=track.track_id,
                    frame_id=track.frame_id,
                    bbox=track.bbox,
                    depth_m=depth_m,
                    # Coarse, *relative* body-size cue: apparent (pixel) size
                    # scaled by depth is monotonic in true size without needing
                    # camera intrinsics. Calibrate to metric under #12.
                    size_estimate=apparent * depth_m,
                )
            )
        return out

    def representative_depth(self, bbox: "BBox", depth: "np.ndarray") -> float:
        """Robust per-object depth: median of valid pixels in the bbox core.

        Returns ``NaN`` when no pixel in the sampled region is valid.
        """
        h, w = depth.shape[:2]
        x1, y1, x2, y2 = self._inner_box(bbox, w, h)
        if x2 <= x1 or y2 <= y1:
            return float("nan")
        crop = depth[y1:y2, x1:x2]
        valid = (
            np.isfinite(crop)
            & (crop >= self._min_depth_m)
            & (crop <= self._max_depth_m)
        )
        if not bool(valid.any()):
            return float("nan")
        return float(np.median(crop[valid]))

    def _inner_box(self, bbox: "BBox", w: int, h: int):
        """Central ``inner_fraction`` sub-box, clipped to the array bounds.

        Guarantees at least a 1px region around the bbox center so a thin/edge
        box still samples its center pixel.
        """
        x1, y1, x2, y2 = bbox
        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0
        half_w = abs(x2 - x1) * self._inner_fraction / 2.0
        half_h = abs(y2 - y1) * self._inner_fraction / 2.0
        ix1 = int(math.floor(cx - half_w))
        iy1 = int(math.floor(cy - half_h))
        ix2 = int(math.ceil(cx + half_w))
        iy2 = int(math.ceil(cy + half_h))
        # clip to bounds
        ix1 = max(0, min(ix1, w))
        ix2 = max(0, min(ix2, w))
        iy1 = max(0, min(iy1, h))
        iy2 = max(0, min(iy2, h))
        # ensure at least the center pixel if the box overlaps the frame
        if ix2 <= ix1 and 0 <= cx < w:
            ix1 = int(min(max(int(cx), 0), w - 1))
            ix2 = ix1 + 1
        if iy2 <= iy1 and 0 <= cy < h:
            iy1 = int(min(max(int(cy), 0), h - 1))
            iy2 = iy1 + 1
        return ix1, iy1, ix2, iy2


__all__ = ["DepthFusion"]
