"""DeepStream pipeline graph builder — TARGET-ONLY skeleton.

Builds the continuous-load GStreamer pipeline:

    source(RGB) -> nvstreammux -> nvinfer(detect) -> nvtracker -> sink

On-demand ReID and depth fusion are layered on via probes (``probes.py``) and
the fusion stage — not as inline elements (ADR-0002 hybrid, ADR-0003). nvinfer/
nvtracker are configured by the ``.txt`` files under ``configs/``.

GStreamer (``gi``) and ``pyds`` are Jetson-only; the import is guarded so this
module can be imported (not run) on the host. See the ``deepstream-pipeline``
skill.
"""

from __future__ import annotations

from typing import Any, Optional

try:
    import gi  # type: ignore

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst  # type: ignore

    _GST_AVAILABLE = True
    _GST_IMPORT_ERROR: Optional[Exception] = None
except Exception as exc:  # pragma: no cover - host path
    Gst = None  # type: ignore
    _GST_AVAILABLE = False
    _GST_IMPORT_ERROR = exc


class DeepStreamPipeline:
    """Owns the GStreamer pipeline lifecycle. Skeleton — see module docstring."""

    def __init__(
        self, config_dir: str = "src/overwatch/inference/deepstream/configs"
    ) -> None:
        if not _GST_AVAILABLE:
            raise RuntimeError(
                "GStreamer/pyds unavailable — DeepStream is target-only (Jetson). "
                "See docs/SOFTWARE_STACK.md and the deepstream-pipeline skill."
            ) from _GST_IMPORT_ERROR
        self._config_dir = config_dir
        self._pipeline: Optional[Any] = None

    def build(self) -> None:
        # TODO: construct elements (nvstreammux, nvinfer, nvtracker, sink),
        # link them, load nvinfer/nvtracker configs from config_dir.
        raise NotImplementedError("DeepStreamPipeline.build")

    def attach_probes(self) -> None:
        # TODO: attach probes from probes.py (on-demand ReID trigger, depth tap).
        raise NotImplementedError("DeepStreamPipeline.attach_probes")

    def run(self) -> None:
        # TODO: set PLAYING, run the GLib main loop, handle bus messages.
        raise NotImplementedError("DeepStreamPipeline.run")

    def stop(self) -> None:
        # TODO: set NULL, tear down.
        raise NotImplementedError("DeepStreamPipeline.stop")


__all__ = ["DeepStreamPipeline"]
