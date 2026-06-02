"""Inference stage — detection, tracking, on-demand ReID, pose.

The continuous load is a DeepStream/GStreamer pipeline (decode -> nvinfer ->
nvtracker). On top: on-demand MegaDescriptor ReID (fired via a probe) and pose.

The DeepStream and ReID submodules are target-only and import-guarded — they are
NOT imported here, so ``import overwatch.inference`` is host-safe. Import the
concrete pieces explicitly on the target.
"""

from overwatch.inference.detection import Detector
from overwatch.inference.tracking import Tracker

__all__ = ["Detector", "Tracker"]
