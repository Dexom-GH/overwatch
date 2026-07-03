"""Host unit tests for the #6 ZED->DeepStream seam spike's pure core.

The spike harness lives in ``scripts/dev/`` (target-only GStreamer at runtime),
but its frame_id-carry logic — the ADR-0002 risk the spike exists to de-risk — is
a pure helper that MUST be host-testable: ``depth_fusion.fuse`` *raises* on a
frame_id mismatch, so a track must reach fusion stamped with the ZED grab's
frame_id, not DeepStream's own frame counter. ``FrameIdStash`` carries that id
across the DeepStream leg via the buffer PTS; these tests pin its contract.

Loaded by path (mirroring ``test_spike_yolo11_export.py``) since ``scripts/dev/``
is dev tooling, not part of the shipped package.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SPIKE = (
    Path(__file__).resolve().parents[2]
    / "scripts" / "dev" / "spike_zed_deepstream_seam.py"
)


def _load_spike():
    spec = importlib.util.spec_from_file_location("spike_zed_deepstream_seam", _SPIKE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


spike = _load_spike()


def test_record_then_resolve_returns_frame_id():
    stash = spike.FrameIdStash()
    stash.record(pts=1000, frame_id=0)
    stash.record(pts=2000, frame_id=1)
    assert stash.resolve(1000) == 0
    assert stash.resolve(2000) == 1


def test_resolve_unknown_pts_returns_none():
    stash = spike.FrameIdStash()
    stash.record(pts=1000, frame_id=0)
    assert stash.resolve(9999) is None


def test_dropped_frame_does_not_shift_mapping():
    """The core property: a frame the pipeline drops must not desync the rest.

    If the seam relied on a running counter, a single dropped/rebatched frame
    would shift every later frame_id by one and ``depth_fusion.fuse`` would
    raise (or, worse, silently align the wrong depth). Keying on PTS means the
    frames that DO arrive still resolve to their original ZED frame_id.
    """
    stash = spike.FrameIdStash()
    for fid, pts in enumerate([100, 200, 300, 400, 500]):
        stash.record(pts=pts, frame_id=fid)
    # The pipeline drops frame_id=2 (pts=300): the probe never resolves it.
    assert stash.resolve(100) == 0
    assert stash.resolve(200) == 1
    assert stash.resolve(400) == 3  # NOT 2 — the drop must not shift this
    assert stash.resolve(500) == 4


def test_stash_is_bounded_evicting_oldest():
    """Bounded memory: a long live run can't grow the stash without limit.

    Eviction is FIFO by insertion (record) order; recent frames still resolve,
    the oldest fall off.
    """
    stash = spike.FrameIdStash(maxlen=4)
    for fid in range(10):
        stash.record(pts=fid * 10, frame_id=fid)
    assert stash.resolve(90) == 9
    assert stash.resolve(60) == 6
    assert stash.resolve(0) is None  # evicted long ago
