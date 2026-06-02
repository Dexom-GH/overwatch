"""ZED 2i capture source — TARGET-ONLY skeleton.

Delivers synchronized RGB + depth via ``pyzed``. ``pyzed`` is a Jetson-only
wheel installed by the ZED SDK (see docs/SOFTWARE_STACK.md) and is NOT
installable on the Windows host.

IMPORT-GUARD PATTERN (the convention for every target-only module): the heavy
import is wrapped so that importing this module on the host fails loudly only
when you try to *instantiate* the source, not at import time. This keeps
``import overwatch`` working everywhere for unit tests.

Per ADR-0002 (hybrid), RGB is fed to the DeepStream pipeline while depth is
published to the bus/fusion layer; this source surfaces both.
"""

from __future__ import annotations

from typing import Iterator, Optional, Tuple

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
    """ZED 2i RGB+depth source. Skeleton — see module docstring."""

    def __init__(self, source_id: str = "zed-0") -> None:
        if not _PYZED_AVAILABLE:
            raise RuntimeError(
                "pyzed is unavailable — ZedSource is target-only (Jetson). "
                "Install the ZED SDK on device; never on the Windows host. "
                "See docs/SOFTWARE_STACK.md."
            ) from _PYZED_IMPORT_ERROR
        self._source_id = source_id
        self._cam = None  # sl.Camera() once implemented

    def open(self) -> None:
        # TODO: configure sl.InitParameters (resolution, depth mode, polarizer),
        # open the camera, set runtime params.
        raise NotImplementedError("ZedSource.open")

    def frames(self) -> Iterator[Tuple[Frame, Optional[DepthFrame]]]:
        # TODO: grab() loop; retrieve_image (RGB) + retrieve_measure (depth);
        # wrap into Frame / DepthFrame sharing a frame_id and timestamp.
        raise NotImplementedError("ZedSource.frames")

    def close(self) -> None:
        # TODO: self._cam.close()
        raise NotImplementedError("ZedSource.close")


__all__ = ["ZedSource"]
