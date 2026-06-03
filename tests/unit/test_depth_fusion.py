"""Host tests for the ADR-0002 hybrid depth-bbox alignment seam.

These exercise the *alignment math* of ``fusion/depth_fusion.py`` — the part of
spike #6 that is separable from the GStreamer/pyzed runtime. The live-pipeline
half (real ZED depth, measured alignment error on a calibrated scene) is
deferred to on-device sign-off; see ``tests/device/test_depth_fusion_device.py``.
"""

import math

import numpy as np
import pytest

from overwatch.bus.schemas import DepthFrame, Track
from overwatch.fusion.depth_fusion import DepthFusion


def _track(track_id, bbox, frame_id=1):
    return Track(
        track_id=track_id,
        frame_id=frame_id,
        bbox=bbox,
        class_id=0,
        class_name="chicken",
        confidence=0.9,
    )


def _depth(array, frame_id=1):
    return DepthFrame(
        source_id="zed-0", frame_id=frame_id, timestamp=1.0, depth=array
    )


# --- representative_depth: the robust spatial sample --------------------------


def test_representative_depth_uniform_region():
    depth = np.full((100, 100), 5.0, dtype=np.float32)
    f = DepthFusion()
    assert f.representative_depth((10, 10, 50, 50), depth) == pytest.approx(5.0)


def test_representative_depth_ignores_zero_nan_inf():
    # ZED returns 0 / NaN / +inf for unmeasured / occluded / too-near pixels.
    depth = np.full((20, 20), 3.0, dtype=np.float32)
    depth[5, 5] = 0.0
    depth[5, 6] = np.nan
    depth[5, 7] = np.inf
    f = DepthFusion(inner_fraction=1.0)
    assert f.representative_depth((0, 0, 20, 20), depth) == pytest.approx(3.0)


def test_representative_depth_no_valid_pixels_is_nan():
    depth = np.zeros((20, 20), dtype=np.float32)
    f = DepthFusion()
    assert math.isnan(f.representative_depth((0, 0, 20, 20), depth))


def test_inner_fraction_rejects_edge_background():
    # Background at 10 m fills the bbox; the animal (2 m) fills the central 60%.
    depth = np.full((100, 100), 10.0, dtype=np.float32)
    depth[38:62, 38:62] = 2.0  # central 24x24 == inner 0.6 of a 40px bbox
    bbox = (30, 30, 70, 70)
    assert DepthFusion(inner_fraction=0.6).representative_depth(
        bbox, depth
    ) == pytest.approx(2.0)
    # Sampling the *whole* bbox is contaminated by background -> the median
    # lands on the majority background, demonstrating why we sample the core.
    assert DepthFusion(inner_fraction=1.0).representative_depth(
        bbox, depth
    ) == pytest.approx(10.0)


def test_representative_depth_clamps_out_of_range():
    depth = np.full((20, 20), 5.0, dtype=np.float32)
    depth[:10, :] = 0.1  # below min_depth_m -> invalid
    depth[10:, :10] = 100.0  # above max_depth_m -> invalid
    f = DepthFusion(inner_fraction=1.0, min_depth_m=0.3, max_depth_m=20.0)
    assert f.representative_depth((0, 0, 20, 20), depth) == pytest.approx(5.0)


def test_representative_depth_clips_bbox_to_array_bounds():
    depth = np.full((20, 20), 4.0, dtype=np.float32)
    f = DepthFusion()
    # bbox partly outside the frame must not blow up; valid pixels still sampled.
    assert f.representative_depth((-5, -5, 10, 10), depth) == pytest.approx(4.0)


# --- fuse: join tracks to the same-frame depth, emit DepthBBox ----------------


def test_fuse_single_track_produces_depthbbox():
    depth = _depth(np.full((100, 100), 6.0, dtype=np.float32))
    out = DepthFusion().fuse([_track(7, (10, 10, 30, 50))], depth)
    assert len(out) == 1
    db = out[0]
    assert db.track_id == 7
    assert db.frame_id == 1
    assert db.bbox == (10, 10, 30, 50)
    assert db.depth_m == pytest.approx(6.0)


def test_fuse_requires_matching_frame_id():
    depth = _depth(np.full((50, 50), 5.0, dtype=np.float32), frame_id=2)
    with pytest.raises(ValueError):
        DepthFusion().fuse([_track(1, (0, 0, 10, 10), frame_id=1)], depth)


def test_fuse_drops_tracks_without_valid_depth():
    depth_arr = np.full((100, 100), 5.0, dtype=np.float32)
    depth_arr[:50, :50] = 0.0  # no valid depth in this quadrant
    depth = _depth(depth_arr)
    tracks = [
        _track(1, (0, 0, 40, 40)),  # over the hole -> dropped
        _track(2, (60, 60, 90, 90)),  # valid -> kept
    ]
    out = DepthFusion().fuse(tracks, depth)
    assert [db.track_id for db in out] == [2]


def test_fuse_size_estimate_is_depth_scaled_apparent_size():
    depth = _depth(np.full((100, 100), 4.0, dtype=np.float32))
    out = DepthFusion().fuse([_track(1, (10, 10, 30, 40))], depth)
    w, h = 20.0, 30.0
    assert out[0].size_estimate == pytest.approx(math.sqrt(w * h) * 4.0)


def test_fuse_empty_tracks_returns_empty():
    depth = _depth(np.full((10, 10), 5.0, dtype=np.float32))
    assert DepthFusion().fuse([], depth) == []


def test_fuse_preserves_order_for_multiple_tracks():
    depth = _depth(np.full((100, 100), 5.0, dtype=np.float32))
    tracks = [_track(3, (0, 0, 10, 10)), _track(1, (20, 20, 40, 40))]
    out = DepthFusion().fuse(tracks, depth)
    assert [db.track_id for db in out] == [3, 1]
