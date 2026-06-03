"""Micro-benchmark for the depth-bbox alignment seam (ADR-0002, spike #6).

Characterises the per-frame cost of ``DepthFusion.fuse`` on a synthetic 720p
metric-depth map across a sweep of box counts. Cross-platform (numpy only), so
it runs both on the Windows host and on the Jetson (``/srv/farmproject/venv``).

    py -3 scripts/dev/bench_depth_fusion.py            # host
    /srv/farmproject/venv/bin/python scripts/dev/bench_depth_fusion.py  # device

Host numbers indicate algorithmic cost only; the Jetson numbers are the ones
ADR-0002 cares about (record them in the ADR / issue #6).
"""

from __future__ import annotations

import platform
import time

import numpy as np

from overwatch.bus.schemas import DepthFrame, Track
from overwatch.fusion.depth_fusion import DepthFusion

H, W = 720, 1280
ITERS = 300


def _scene(seed: int = 0):
    rng = np.random.default_rng(seed)
    depth = rng.uniform(0.5, 12.0, size=(H, W)).astype(np.float32)
    # punch ~15% holes (the ZED's 0/NaN pixels) to exercise the valid-mask path
    holes = rng.random((H, W)) < 0.15
    depth[holes] = 0.0
    return depth


def _tracks(n: int):
    rng = np.random.default_rng(100 + n)
    out = []
    for i in range(n):
        x1 = float(rng.integers(0, W - 100))
        y1 = float(rng.integers(0, H - 140))
        out.append(Track(track_id=i, frame_id=1, bbox=(x1, y1, x1 + 90, y1 + 130),
                         class_id=0, class_name="x", confidence=1.0))
    return out


def main() -> None:
    depth = DepthFrame(source_id="zed-0", frame_id=1, timestamp=0.0, depth=_scene())
    fusion = DepthFusion()
    print("platform: {} | python {} | numpy {}".format(
        platform.machine(), platform.python_version(), np.__version__))
    print("depth map: {}x{}  iters: {}".format(W, H, ITERS))
    print("{:>6}  {:>12}  {:>14}".format("boxes", "ms/frame", "us/box"))
    for n in (1, 5, 10, 20):
        tracks = _tracks(n)
        fusion.fuse(tracks, depth)  # warmup
        t0 = time.perf_counter()
        for _ in range(ITERS):
            fusion.fuse(tracks, depth)
        ms = (time.perf_counter() - t0) / ITERS * 1e3
        print("{:>6}  {:>12.4f}  {:>14.2f}".format(n, ms, ms * 1000 / n))


if __name__ == "__main__":
    main()
