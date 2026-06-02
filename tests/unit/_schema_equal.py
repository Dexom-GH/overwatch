"""Shared test helpers for bus serialization / transport tests.

Underscore-prefixed so pytest does not collect it as a test module. Imported by
sibling test files (pytest 'prepend' import mode puts tests/unit on sys.path).
"""

from __future__ import annotations

import dataclasses
from typing import Any, List, Tuple

import numpy as np

from overwatch.bus import schemas, topics


def assert_schema_equal(a: Any, b: Any) -> None:
    """Deep equality that handles numpy arrays, nested dataclasses, tuples, dicts."""
    assert type(a) is type(b), "type mismatch: {!r} vs {!r}".format(type(a), type(b))
    if dataclasses.is_dataclass(a):
        for f in dataclasses.fields(a):
            assert_schema_equal(getattr(a, f.name), getattr(b, f.name))
    elif isinstance(a, np.ndarray):
        assert a.dtype == b.dtype, "dtype {} != {}".format(a.dtype, b.dtype)
        assert a.shape == b.shape, "shape {} != {}".format(a.shape, b.shape)
        assert np.array_equal(a, b), "array contents differ"
    elif isinstance(a, (list, tuple)):
        assert len(a) == len(b), "length {} != {}".format(len(a), len(b))
        for x, y in zip(a, b):
            assert_schema_equal(x, y)
    elif isinstance(a, dict):
        assert a.keys() == b.keys(), "dict keys differ"
        for k in a:
            assert_schema_equal(a[k], b[k])
    else:
        assert a == b, "{!r} != {!r}".format(a, b)


def sample_messages() -> List[Tuple[str, Any]]:
    """One representative instance of every schema, with realistic payloads."""
    image = np.arange(2 * 3 * 3, dtype=np.uint8).reshape(2, 3, 3)
    depth = np.linspace(0.5, 4.0, 6, dtype=np.float32).reshape(2, 3)
    embedding = np.arange(8, dtype=np.float32)
    event = schemas.Event(
        timestamp=1.0, kind="fence_crossing", track_id=7, zone_id="z1",
        detail={"direction": "in"},
    )
    identity = schemas.Identity(
        track_id=7, embedding=embedding, matched_id=None, score=None,
    )
    return [
        (topics.CAPTURE_FRAME, schemas.Frame(
            source_id="cam0", frame_id=1, timestamp=1.0, image=image,
            width=3, height=2)),
        (topics.CAPTURE_DEPTH, schemas.DepthFrame(
            source_id="cam0", frame_id=1, timestamp=1.0, depth=depth)),
        (topics.INFER_DETECTION, schemas.Detection(
            frame_id=1, bbox=(1.0, 2.0, 3.0, 4.0), class_id=0,
            class_name="sheep", confidence=0.9)),
        (topics.INFER_TRACK, schemas.Track(
            track_id=7, frame_id=1, bbox=(1.0, 2.0, 3.0, 4.0), class_id=0,
            class_name="sheep", confidence=0.9, identity=identity)),
        (topics.FUSION_DEPTH_BBOX, schemas.DepthBBox(
            track_id=7, frame_id=1, bbox=(1.0, 2.0, 3.0, 4.0),
            depth_m=2.5, size_estimate=0.8)),
        (topics.INFER_IDENTITY, identity),
        (topics.INFER_POSE, schemas.Pose(
            track_id=7, frame_id=1,
            keypoints=[(1.0, 2.0, 0.9), (3.0, 4.0, 0.8)])),
        (topics.FUSION_COUNT, schemas.ZoneCount(
            zone_id="z1", timestamp=1.0, count=3, class_name="sheep")),
        (topics.FUSION_HEALTH, schemas.HealthSignal(
            track_id=7, timestamp=1.0, kind="immobility", score=0.7,
            detail={"seconds": 120})),
        (topics.FUSION_EVENT, event),
        (topics.OUTPUT_ALERT, schemas.Alert(
            timestamp=1.0, severity="warning", title="t", message="m",
            source_event=event, detail={"k": "v"})),
    ]
