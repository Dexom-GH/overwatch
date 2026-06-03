"""ZED 2i capture source — TARGET-ONLY (pyzed).

Delivers synchronized RGB + depth via ``pyzed``. ``pyzed`` is a Jetson-only
wheel installed by the ZED SDK (see docs/SOFTWARE_STACK.md) and is NOT
installable on the Windows host.

IMPORT-GUARD PATTERN (the convention for every target-only module): the heavy
import is wrapped so that importing this module on the host fails loudly only
when you try to *instantiate* the source, not at import time. This keeps
``import overwatch`` working everywhere for unit tests.

Per ADR-0002 (hybrid), RGB is fed to the DeepStream pipeline while depth is
published to the bus/fusion layer; this source surfaces both. RGB and depth are
retrieved from the **same** ``grab()`` so they share ``frame_id`` and
``timestamp`` exactly (zero skew) — :func:`overwatch.capture.service.run_capture`
publishes the pair without re-timing it.

On-device verification of this module is gated on the ZED enumerating over
USB-3 (#54); it cannot be exercised on the host.
"""

from __future__ import annotations

from typing import Iterator, Optional, Tuple

import numpy as np

from overwatch.bus.schemas import DepthFrame, Frame
from overwatch.capture.base import CaptureSource

try:
    import pyzed.sl as sl  # type: ignore

    _PYZED_AVAILABLE = True
    _PYZED_IMPORT_ERROR: Optional[Exception] = None
except Exception as exc:  # pragma: no cover - host path
    sl = None  # type: ignore
    _PYZED_AVAILABLE = False
    _PYZED_IMPORT_ERROR = exc


class ZedSource(CaptureSource):
    """ZED 2i RGB+depth source.

    Parameters mirror ``configs/default.yaml`` ``capture.*``. ``fps`` is the
    committed V1 sustained target (15), confirmed on-device against the power-mode
    decision (#46). Depth is metric (meters), matching ``schemas.DepthFrame``.
    """

    def __init__(
        self,
        source_id: str = "zed-0",
        fps: int = 15,
        resolution: str = "HD720",
        depth_mode: str = "PERFORMANCE",
    ) -> None:
        if not _PYZED_AVAILABLE:
            raise RuntimeError(
                "pyzed is unavailable — ZedSource is target-only (Jetson). "
                "Install the ZED SDK on device; never on the Windows host. "
                "See docs/SOFTWARE_STACK.md."
            ) from _PYZED_IMPORT_ERROR
        self._source_id = source_id
        self._fps = fps
        self._resolution = resolution
        self._depth_mode = depth_mode
        self._frame_id = 0
        self._cam = None  # sl.Camera once open()ed
        self._runtime = None  # sl.RuntimeParameters

    def open(self) -> None:
        init = sl.InitParameters()
        init.camera_resolution = getattr(sl.RESOLUTION, self._resolution)
        init.camera_fps = self._fps
        init.depth_mode = getattr(sl.DEPTH_MODE, self._depth_mode)
        init.coordinate_units = sl.UNIT.METER  # metric depth (schemas.DepthFrame)
        cam = sl.Camera()
        status = cam.open(init)
        if status != sl.ERROR_CODE.SUCCESS:
            raise RuntimeError("ZED open failed: {}".format(status))
        self._cam = cam
        self._runtime = sl.RuntimeParameters()

    def frames(self) -> Iterator[Tuple[Frame, Optional[DepthFrame]]]:
        if self._cam is None:
            raise RuntimeError("ZedSource.open() must be called before frames()")
        image = sl.Mat()
        depth = sl.Mat()
        eof = getattr(sl.ERROR_CODE, "END_OF_SVOFILE_REACHED", None)
        while True:
            status = self._cam.grab(self._runtime)
            if status != sl.ERROR_CODE.SUCCESS:
                if eof is not None and status == eof:
                    break  # SVO playback finished
                continue  # transient (e.g. dropped frame) — try the next grab
            self._cam.retrieve_image(image, sl.VIEW.LEFT)
            self._cam.retrieve_measure(depth, sl.MEASURE.DEPTH)
            timestamp = self._cam.get_timestamp(sl.TIME_REFERENCE.IMAGE).get_seconds()
            frame_id = self._frame_id
            self._frame_id += 1

            # get_data() returns BGRA (HxWx4); drop alpha to the HxWx3 the
            # contract expects. ascontiguousarray decouples from the reused Mat.
            rgb = np.ascontiguousarray(image.get_data()[:, :, :3])
            depth_m = np.ascontiguousarray(depth.get_data())
            height, width = rgb.shape[0], rgb.shape[1]

            frame = Frame(
                source_id=self._source_id,
                frame_id=frame_id,
                timestamp=timestamp,
                image=rgb,
                width=width,
                height=height,
            )
            depth_frame = DepthFrame(
                source_id=self._source_id,
                frame_id=frame_id,
                timestamp=timestamp,
                depth=depth_m,
            )
            yield frame, depth_frame

    def close(self) -> None:
        if self._cam is not None:
            self._cam.close()
            self._cam = None
            self._runtime = None


__all__ = ["ZedSource"]
