"""GStreamer probe callbacks — TARGET-ONLY skeleton.

Probes are where work that sits OUTSIDE DeepStream's per-frame happy path is
hooked in:

- **On-demand ReID trigger (ADR-0003):** a probe on the tracker's src pad reads
  track metadata, asks the trigger policy which tracks ``needs_identity``, and
  dispatches their crops to the MegaDescriptor TRT engine OFF the streaming
  thread (so the pipeline doesn't stall). The resulting embedding is attached
  to the track / published on ``topics.INFER_IDENTITY``.
- **Depth tap (ADR-0002 hybrid):** a probe can read the current frame_id so the
  fusion layer can align the matching ZED depth frame to the detections.

``pyds`` is Jetson-only. These are signatures + docstrings; wiring happens with
``DeepStreamPipeline.attach_probes``. See the ``deepstream-pipeline`` skill.
"""

from __future__ import annotations

from typing import Any


def on_tracker_src_pad(pad: "Any", info: "Any", user_data: "Any") -> "Any":
    """Probe: decide + dispatch on-demand ReID for tracks needing identity.

    Returns a Gst.PadProbeReturn value in the real implementation. Must NOT block
    the streaming thread — dispatch embeddings off-thread / batched.
    """
    raise NotImplementedError("on_tracker_src_pad probe")


def on_osd_sink_pad(pad: "Any", info: "Any", user_data: "Any") -> "Any":
    """Probe: read frame metadata (e.g. frame_id) for depth alignment / logging."""
    raise NotImplementedError("on_osd_sink_pad probe")


__all__ = ["on_tracker_src_pad", "on_osd_sink_pad"]
