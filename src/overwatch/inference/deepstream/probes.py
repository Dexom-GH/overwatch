"""GStreamer probe callbacks — detect+track -> bus, plus on-demand ReID hook.

Probes are where work that crosses the bus (or sits outside DeepStream's per-frame
happy path) is hooked into the streaming pipeline:

- **Tracks -> bus (#15):** a probe on the nvtracker src pad reads each tracked
  object's metadata and publishes a :class:`~overwatch.bus.schemas.Track` on
  ``topics.INFER_TRACK``. The metadata->Track mapping is factored into the pure,
  host-testable :func:`track_from_object` (the bbox frame conversion is the
  contract-critical bit); the pyds iteration around it is target-only.
- **On-demand ReID trigger (ADR-0003):** the same tracker-pad metadata is where a
  trigger policy will later decide which tracks ``needs_identity`` and dispatch
  their crops to MegaDescriptor OFF the streaming thread. Hook is noted; #8 closes
  the policy. Not implemented here.

``pyds`` / ``gi`` are Jetson-only; both are import-guarded so this module imports
(not runs the probe) on the host. See the ``deepstream-pipeline`` skill.
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from overwatch.bus.schemas import Track

try:  # target-only: GStreamer (for the PadProbeReturn value) + pyds (metadata)
    import gi  # type: ignore

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst  # type: ignore
    import pyds  # type: ignore

    _PROBE_DEPS_AVAILABLE = True
    _PROBE_IMPORT_ERROR: Optional[Exception] = None
except Exception as exc:  # pragma: no cover - host path
    Gst = None  # type: ignore
    pyds = None  # type: ignore
    _PROBE_DEPS_AVAILABLE = False
    _PROBE_IMPORT_ERROR = exc


def track_from_object(
    *,
    track_id: "Any",
    left: "Any",
    top: "Any",
    width: "Any",
    height: "Any",
    class_id: "Any",
    class_name: str,
    confidence: "Any",
    frame_id: "Any",
) -> Track:
    """Map a DeepStream object's metadata to a :class:`~overwatch.bus.schemas.Track`.

    Pure and host-testable. Converts DeepStream's ``rect_params`` box
    ``(left, top, width, height)`` to the bus contract's ``(x1, y1, x2, y2)``
    pixels, and coerces pyds' numeric types to clean python ``int``/``float``.
    ``identity`` stays ``None`` — ReID attaches on-demand later (ADR-0003).
    """
    x1 = float(left)
    y1 = float(top)
    bbox = (x1, y1, x1 + float(width), y1 + float(height))
    return Track(
        track_id=int(track_id),
        frame_id=int(frame_id),
        bbox=bbox,
        class_id=int(class_id),
        class_name=str(class_name),
        confidence=float(confidence),
    )


def make_tracker_probe(
    on_track: "Callable[[Track], None]",
    *,
    labels: "Optional[List[str]]" = None,
) -> "Callable[[Any, Any, Any], Any]":
    """Build a nvtracker src-pad probe that hands each tracked object to ``on_track``.

    TARGET-ONLY (uses pyds). For every tracked object in the batch it builds a
    :class:`Track` via :func:`track_from_object` and calls ``on_track(track)``.
    ``labels`` (class_id -> name) names classes when the object metadata has no
    label.

    ``on_track`` runs on the **GStreamer streaming thread** and MUST be
    non-blocking — hand the track to a thread-safe queue and publish to the bus
    from another thread. Do NOT call ``MessageBus.publish`` directly here: the PUB
    socket is single-threaded (see ``zeromq_bus.py``), and blocking the streaming
    thread stalls the pipeline.
    """
    if not _PROBE_DEPS_AVAILABLE:
        raise RuntimeError(
            "GStreamer/pyds unavailable — the tracker probe is target-only (Jetson). "
            "See docs/SOFTWARE_STACK.md and the deepstream-pipeline skill."
        ) from _PROBE_IMPORT_ERROR

    def _probe(_pad: "Any", info: "Any", _u: "Any") -> "Any":
        buf = info.get_buffer()
        if buf is None:
            return Gst.PadProbeReturn.OK
        batch = pyds.gst_buffer_get_nvds_batch_meta(hash(buf))
        l_frame = batch.frame_meta_list
        while l_frame is not None:
            frame_meta = pyds.NvDsFrameMeta.cast(l_frame.data)
            l_obj = frame_meta.obj_meta_list
            while l_obj is not None:
                obj = pyds.NvDsObjectMeta.cast(l_obj.data)
                rect = obj.rect_params
                name = obj.obj_label
                if not name and labels is not None and obj.class_id < len(labels):
                    name = labels[obj.class_id]
                on_track(
                    track_from_object(
                        track_id=obj.object_id,
                        left=rect.left,
                        top=rect.top,
                        width=rect.width,
                        height=rect.height,
                        class_id=obj.class_id,
                        class_name=name or str(obj.class_id),
                        confidence=obj.confidence,
                        frame_id=frame_meta.frame_num,
                    )
                )
                l_obj = l_obj.next
            l_frame = l_frame.next
        return Gst.PadProbeReturn.OK

    return _probe


__all__ = ["track_from_object", "make_tracker_probe"]
