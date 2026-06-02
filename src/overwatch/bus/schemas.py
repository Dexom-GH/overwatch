"""Bus message schemas — THE contract.

Every message that crosses the bus is one of these dataclasses. This module is
the single most important / most-reviewed surface in the repo: changing a schema
changes the contract between stages. Keep it dependency-light so it imports
cleanly on the host (no pyzed / torch / cv2 at module top level).

Conventions:
- Python 3.8 compatible (``Optional``/``List`` from typing, not ``X | None`` /
  ``list[X]``).
- Image/depth payloads are typed as ``Any`` here to avoid a hard numpy import in
  the contract; in practice they are ``numpy.ndarray``. Annotated under
  ``TYPE_CHECKING`` for tooling without a runtime dependency.
- Bounding boxes are ``(x1, y1, x2, y2)`` in pixels unless noted.

These are skeletons (fields + docstrings). Serialization (to/from the chosen
bus transport) is added when ADR-0001 closes — see ``bus_stage-conventions``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:  # tooling only — never imported at runtime on host or target
    import numpy as np  # noqa: F401

BBox = Tuple[float, float, float, float]  # (x1, y1, x2, y2) in pixels


@dataclass
class Frame:
    """An RGB frame from a capture source."""

    source_id: str
    frame_id: int
    timestamp: float           # epoch seconds
    image: "Any"               # numpy.ndarray HxWx3, BGR/RGB per source contract
    width: int = 0
    height: int = 0


@dataclass
class DepthFrame:
    """A depth frame, time-aligned to a :class:`Frame` by ``frame_id``."""

    source_id: str
    frame_id: int
    timestamp: float
    depth: "Any"               # numpy.ndarray HxW, metric depth (meters)


@dataclass
class Detection:
    """A single object detection within a frame (2D, pre-tracking)."""

    frame_id: int
    bbox: BBox
    class_id: int
    class_name: str
    confidence: float


@dataclass
class Track:
    """A detection associated across frames with a stable ``track_id``."""

    track_id: int
    frame_id: int
    bbox: BBox
    class_id: int
    class_name: str
    confidence: float
    # Identity is attached on-demand (ADR-0003); None until/unless ReID fires.
    identity: Optional["Identity"] = None


@dataclass
class DepthBBox:
    """A detection/track enriched with ZED depth — the hybrid fusion output.

    This is where depth becomes a first-class signal (ADR-0002). Produced by
    ``fusion/depth_fusion.py`` by aligning a depth frame to a 2D bbox/track.
    """

    track_id: int
    frame_id: int
    bbox: BBox
    depth_m: float             # representative depth of the object (meters)
    size_estimate: Optional[float] = None  # coarse body-size cue (ID signal)


@dataclass
class Identity:
    """An on-demand ReID result for a track.

    V1 produces ``embedding`` but has no gallery to match against, so
    ``matched_id``/``score`` stay None (enrollment is V2 — see ROADMAP_V1_V2).
    """

    track_id: int
    embedding: "Any"           # numpy.ndarray, MegaDescriptor feature vector
    # V2: populated once a gallery exists.
    matched_id: Optional[str] = None
    score: Optional[float] = None


@dataclass
class Pose:
    """Pose estimate for a tracked animal (feeds lameness scoring)."""

    track_id: int
    frame_id: int
    keypoints: List[Tuple[float, float, float]] = field(default_factory=list)
    # each keypoint: (x, y, confidence)


@dataclass
class ZoneCount:
    """A per-zone animal count at a point in time (depth-deduplicated)."""

    zone_id: str
    timestamp: float
    count: int
    class_name: Optional[str] = None  # None = all classes


@dataclass
class HealthSignal:
    """A health observation about a track (immobility, lameness, ...)."""

    track_id: int
    timestamp: float
    kind: str                  # e.g. "immobility", "lameness"
    score: float               # severity / confidence, signal-specific
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Event:
    """A discrete event detected by the logic layer (e.g. fence-crossing)."""

    timestamp: float
    kind: str                  # e.g. "fence_crossing"
    track_id: Optional[int] = None
    zone_id: Optional[str] = None
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Alert:
    """An outbound alert delivered to sinks (Slack, store, dashboard)."""

    timestamp: float
    severity: str              # "info" | "warning" | "critical"
    title: str
    message: str
    source_event: Optional[Event] = None
    detail: Dict[str, Any] = field(default_factory=dict)


__all__ = [
    "BBox",
    "Frame",
    "DepthFrame",
    "Detection",
    "Track",
    "DepthBBox",
    "Identity",
    "Pose",
    "ZoneCount",
    "HealthSignal",
    "Event",
    "Alert",
]
