"""Shared host test-data factories (#44).

Synthetic ``Frame`` / ``DepthFrame`` / ``Track`` / ``DepthBBox`` / ``Event`` /
``Alert`` builders, a recording (mocked) Slack sink + webhook, and sample
zone/fence config snippets — so fusion / output / serialization tests run on the
host **without** the Jetson, ZED, or DeepStream, and without each test
hand-rolling its own helpers.

Import directly (modules in ``tests/unit`` are importable by bare name under
pytest's default prepend import mode, like ``_schema_equal``)::

    from factories import make_frame, make_alert, RecordingAlertSink

or use the pytest fixtures that wrap these in ``tests/unit/conftest.py``
(``frame_factory``, ``mock_slack_webhook``, ``recording_sink``, ``sample_zones`` …).
"""

from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from overwatch.bus.schemas import Alert, DepthBBox, DepthFrame, Event, Frame, Track
from overwatch.output.slack import AlertSink

BBoxT = Tuple[float, float, float, float]


def make_frame(
    frame_id: int = 1,
    *,
    source_id: str = "zed-0",
    width: int = 6,
    height: int = 4,
    timestamp: Optional[float] = None,
) -> Frame:
    """A synthetic RGB frame (zeros image of the given size)."""
    ts = float(frame_id) if timestamp is None else timestamp
    return Frame(
        source_id=source_id, frame_id=frame_id, timestamp=ts,
        image=np.zeros((height, width, 3), dtype=np.uint8), width=width, height=height,
    )


def make_depth_frame(
    frame_id: int = 1,
    *,
    source_id: str = "zed-0",
    width: int = 6,
    height: int = 4,
    timestamp: Optional[float] = None,
    fill_m: float = 2.0,
) -> DepthFrame:
    """A synthetic depth frame (constant metric depth), aligned to make_frame by id."""
    ts = float(frame_id) if timestamp is None else timestamp
    return DepthFrame(
        source_id=source_id, frame_id=frame_id, timestamp=ts,
        depth=np.full((height, width), fill_m, dtype=np.float32),
    )


def make_track(
    track_id: int = 1,
    *,
    frame_id: int = 1,
    bbox: BBoxT = (0.0, 0.0, 10.0, 10.0),
    class_id: int = 0,
    class_name: str = "sheep",
    confidence: float = 0.9,
) -> Track:
    """A synthetic tracked detection."""
    return Track(
        track_id=track_id, frame_id=frame_id, bbox=bbox,
        class_id=class_id, class_name=class_name, confidence=confidence,
    )


def make_depth_bbox(
    track_id: int = 1,
    *,
    frame_id: int = 1,
    bbox: BBoxT = (0.0, 0.0, 10.0, 10.0),
    depth_m: float = 2.0,
    size_estimate: Optional[float] = None,
) -> DepthBBox:
    """A synthetic depth-fused detection (the hybrid fusion output)."""
    return DepthBBox(
        track_id=track_id, frame_id=frame_id, bbox=bbox,
        depth_m=depth_m, size_estimate=size_estimate,
    )


def make_event(
    kind: str = "zone_count",
    *,
    timestamp: float = 0.0,
    zone_id: Optional[str] = None,
    track_id: Optional[int] = None,
    detail: Optional[Dict[str, Any]] = None,
) -> Event:
    """A synthetic discrete event."""
    return Event(
        timestamp=timestamp, kind=kind, zone_id=zone_id, track_id=track_id,
        detail=detail or {},
    )


def make_alert(
    *,
    timestamp: float = 0.0,
    severity: str = "warning",
    title: str = "test alert",
    message: str = "m",
    source_event: Optional[Event] = None,
) -> Alert:
    """A synthetic outbound alert."""
    return Alert(
        timestamp=timestamp, severity=severity, title=title, message=message,
        source_event=source_event,
    )


def sample_zone(name: str = "pen-A") -> Dict[str, Any]:
    """A valid image-space zone config snippet (validates against schema.Zone)."""
    return {"name": name, "space": "image", "polygon": [[0, 0], [10, 0], [10, 10], [0, 10]]}


def sample_fence(name: str = "north") -> Dict[str, Any]:
    """A valid image-space fence-line config snippet (validates against schema.FenceLine)."""
    return {"name": name, "space": "image", "line": [[0, 0], [10, 0]], "crossing": "out_to_in"}


class RecordingAlertSink(AlertSink):
    """A mocked Slack sink — captures alerts instead of posting to the network."""

    def __init__(self) -> None:
        self.sent: List[Alert] = []

    def send(self, alert: Alert) -> None:
        self.sent.append(alert)


class RecordingWebhook:
    """A callable standing in for a Slack incoming webhook; records JSON payloads."""

    def __init__(self) -> None:
        self.payloads: List[Dict[str, Any]] = []

    def __call__(self, payload: Dict[str, Any]) -> int:
        self.payloads.append(payload)
        return 200  # mimic Slack's 200 OK


__all__ = [
    "make_frame",
    "make_depth_frame",
    "make_track",
    "make_depth_bbox",
    "make_event",
    "make_alert",
    "sample_zone",
    "sample_fence",
    "RecordingAlertSink",
    "RecordingWebhook",
]
