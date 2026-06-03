"""On-device sign-off for the ADR-0002 depth-bbox alignment seam (spike #6).

This is the live half the host suite cannot cover: align ``DepthFusion`` to a
*real* ZED depth frame (with its real holes, units, and noise) and confirm a
plausible per-object depth plus the per-frame alignment cost.

Requires a connected ZED on a **USB 3.x** port (the SDK rejects USB 2.0 — see
the spike findings in docs/DECISIONS/0002). It does NOT require the detector
engine: detector validation is #49/#15. We stand in a fixed central bbox so the
alignment seam can be signed off independently of detection.

Run on the Jetson:
    ssh jetson-agent
    /srv/farmproject/venv/bin/python -m pytest -m "zed" \
        tests/device/test_depth_fusion_device.py -s
"""

import time

import pytest

pytestmark = [pytest.mark.device, pytest.mark.zed]

sl = pytest.importorskip("pyzed.sl", reason="pyzed (ZED SDK) not available")

from overwatch.bus.schemas import DepthFrame, Track  # noqa: E402
from overwatch.fusion.depth_fusion import DepthFusion  # noqa: E402


@pytest.fixture(scope="module")
def zed_depth():
    cam = sl.Camera()
    init = sl.InitParameters()
    init.camera_resolution = sl.RESOLUTION.HD720
    init.camera_fps = 15
    init.depth_mode = sl.DEPTH_MODE.PERFORMANCE
    init.coordinate_units = sl.UNIT.METER
    status = cam.open(init)
    if status != sl.ERROR_CODE.SUCCESS:
        pytest.skip("ZED not opened ({}): check USB 3.x connection".format(status))
    try:
        rt = sl.RuntimeParameters()
        mat = sl.Mat()
        if cam.grab(rt) != sl.ERROR_CODE.SUCCESS:
            pytest.skip("ZED grab failed")
        cam.retrieve_measure(mat, sl.MEASURE.DEPTH)
        # numpy view of the metric depth map (HxW float32, meters)
        yield mat.get_data()[:, :, 0].copy() if mat.get_data().ndim == 3 else mat.get_data().copy()
    finally:
        cam.close()


def test_alignment_yields_plausible_depth(zed_depth):
    h, w = zed_depth.shape[:2]
    # central bbox covering the scene centre (stand-in for a detection)
    bbox = (w * 0.4, h * 0.4, w * 0.6, h * 0.6)
    depth = DepthFrame(source_id="zed-0", frame_id=1, timestamp=time.time(), depth=zed_depth)
    track = Track(track_id=1, frame_id=1, bbox=bbox, class_id=0, class_name="x", confidence=1.0)

    fusion = DepthFusion()
    out = fusion.fuse([track], depth)
    assert out, "no valid depth in the central bbox — point the ZED at a scene"
    db = out[0]
    print("\n[device] central-bbox depth = {:.3f} m, size_estimate = {:.1f}".format(
        db.depth_m, db.size_estimate or float("nan")))
    assert 0.3 <= db.depth_m <= 20.0


def test_per_frame_alignment_cost(zed_depth):
    depth = DepthFrame(source_id="zed-0", frame_id=1, timestamp=time.time(), depth=zed_depth)
    h, w = zed_depth.shape[:2]
    # 10 typical animal-sized boxes scattered across the frame
    tracks = [
        Track(track_id=i, frame_id=1,
              bbox=(w * (0.05 + 0.08 * i), h * 0.3, w * (0.05 + 0.08 * i) + 90, h * 0.3 + 130),
              class_id=0, class_name="x", confidence=1.0)
        for i in range(10)
    ]
    fusion = DepthFusion()
    fusion.fuse(tracks, depth)  # warmup
    n = 200
    t0 = time.perf_counter()
    for _ in range(n):
        fusion.fuse(tracks, depth)
    per_frame_ms = (time.perf_counter() - t0) / n * 1e3
    print("\n[device] depth-bbox fuse: {:.3f} ms/frame for {} boxes".format(
        per_frame_ms, len(tracks)))
    # Generous ceiling: alignment must be cheap vs a ~66 ms (15 FPS) budget.
    assert per_frame_ms < 20.0
