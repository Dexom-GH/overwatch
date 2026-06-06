"""RTSP / IP camera capture source — mono (depth-less) feed (#31, ADR-0006).

The V2->V1 forward-port (ADR-0006) adds 3-4 non-stereo IP/RTSP cameras alongside
the ZED. Per the capability split, a mono feed has **no depth**: this source
yields ``(Frame, None)`` pairs, and :func:`overwatch.capture.service.run_capture`
publishes only ``capture.frame`` for it (never ``capture.depth``).

Decode backend is OpenCV ``cv2.VideoCapture``, chosen **backend-aware** so one
class serves both host and target:

* **Jetson (target):** pass a GStreamer/NVDEC ``pipeline`` string
  (``rtspsrc ... ! nvv4l2decoder ! nvvidconv ! appsink``) -> hardware decode, the
  performant path the #8 multi-stream benchmark needs.
* **Host:** the default ffmpeg backend against the ``url`` -> unit/integration
  testable off-device.

IMPORT-GUARD: ``cv2`` is wrapped so ``import overwatch`` stays clean even where
OpenCV is absent; the failure is raised only when a real capture is opened
without an injected factory. On the host, ``opencv-python`` is a dev dep; on the
Jetson, the system GStreamer-enabled OpenCV provides ``cv2``.

The read / reconnect / end-of-stream logic is backend-agnostic and is unit-tested
on the host with an injected fake capture (no network, no real cv2). The live
decode against a real IP camera is the on-device sign-off tail (folded into #8).

Per ADR-0006 the RTSP stream is also decoded independently by the DeepStream
inference leg (#32, ``uridecodebin`` -> ``nvstreammux``); this capture-stage
decode deliberately coexists with it, mirroring the ZED RGB-to-bus + RGB-to-
DeepStream hybrid in ADR-0002. The resulting double-decode cost on the Xavier NX
is a benchmark exit criterion in #8.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterator, Optional, Tuple

import numpy as np

from overwatch.bus.schemas import DepthFrame, Frame
from overwatch.capture.base import CaptureSource

try:
    import cv2  # type: ignore

    _CV2_IMPORT_ERROR: Optional[Exception] = None
except Exception as exc:  # pragma: no cover - opencv absent
    cv2 = None  # type: ignore
    _CV2_IMPORT_ERROR = exc

_LOG = logging.getLogger(__name__)


def inject_cred(url: str, cred: "Optional[str]") -> str:
    """Return ``url`` with ``cred`` (``user:pass``) spliced into the userinfo slot.

    ``rtsp://host:554/s`` + ``user:pass`` -> ``rtsp://user:pass@host:554/s``. A
    ``None`` cred (or a non-scheme string) leaves the URL unchanged. ``cred`` must
    be URL-safe already — a password with reserved chars (e.g. ``#``) is expected
    percent-encoded (``%23``), since this is a raw userinfo splice, not an encoder.
    Kept tiny and pure so it is testable without building a real capture; used by
    both this capture source and the DeepStream inference leg (#84). Callers must
    never log the result.
    """
    if not cred:
        return url
    scheme, sep, rest = url.partition("://")
    if not sep:  # not a scheme://... URL; nothing sensible to splice
        return url
    return "{}://{}@{}".format(scheme, cred, rest)


class RtspSource(CaptureSource):
    """An RTSP/IP camera source publishing depth-less ``(Frame, None)`` pairs.

    Parameters mirror ``configs/*.yaml`` ``capture.sources[*]`` for the ``rtsp``
    type (``RtspSourceConfig``): ``url`` / ``fps`` / ``cred`` (resolved from
    ``cred_env`` by the loader — never from YAML, #41). ``pipeline`` is the
    target-only GStreamer/NVDEC override.

    Injected seams (``capture_factory`` / ``clock`` / ``sleep``) keep the
    read/reconnect/EOF logic host-testable without a real cv2 or network.
    """

    def __init__(
        self,
        source_id: str,
        url: str,
        fps: int = 15,
        cred: "Optional[str]" = None,
        pipeline: "Optional[str]" = None,
        *,
        reconnect: bool = True,
        max_reconnects: int = 5,
        backoff_base: float = 0.5,
        backoff_cap: float = 5.0,
        capture_factory: "Optional[Callable[[], Any]]" = None,
        clock: "Optional[Callable[[], float]]" = None,
        sleep: "Optional[Callable[[float], None]]" = None,
    ) -> None:
        self._source_id = source_id
        self._url = url  # base URL; credentials are NEVER stored joined to it
        self._fps = fps
        self._cred = cred
        self._pipeline = pipeline
        self._reconnect = reconnect
        self._max_reconnects = max(0, max_reconnects)
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap
        self._capture_factory = capture_factory
        self._frame_id = 0
        self._cap = None  # the open cv2.VideoCapture (or injected fake)

        if clock is None:
            import time

            clock = time.time
        if sleep is None:
            import time

            sleep = time.sleep
        self._clock = clock
        self._sleep = sleep

    def open(self) -> None:
        cap = self._open_capture()
        if not cap.isOpened():
            raise RuntimeError(
                "RtspSource {}: failed to open stream {}".format(self._source_id, self._url)
            )
        self._cap = cap

    def frames(self) -> Iterator[Tuple[Frame, Optional[DepthFrame]]]:
        if self._cap is None:
            raise RuntimeError("RtspSource.open() must be called before frames()")
        failures = 0
        while True:
            ok, image = self._cap.read()
            if ok:
                failures = 0
                yield self._make_frame(image), None
                continue
            # read() failed: end-of-stream for a finite source, or a live drop.
            if not self._reconnect:
                return  # clean end-of-stream (file / replay)
            failures += 1
            if failures > self._max_reconnects:
                _LOG.warning(
                    "RtspSource %s: giving up after %d reconnect attempts",
                    self._source_id,
                    failures - 1,
                )
                return
            self._sleep(self._backoff(failures))
            self._reopen()

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    # -- internals -----------------------------------------------------------

    def _make_frame(self, image: "Any") -> Frame:
        # Drop any alpha to the HxWx3 BGR the Frame contract expects;
        # ascontiguousarray decouples from a reused decoder buffer.
        rgb = np.ascontiguousarray(image[:, :, :3])
        height, width = rgb.shape[0], rgb.shape[1]
        frame = Frame(
            source_id=self._source_id,
            frame_id=self._frame_id,
            timestamp=self._clock(),
            image=rgb,
            width=width,
            height=height,
        )
        self._frame_id += 1
        return frame

    def _backoff(self, failures: int) -> float:
        return min(self._backoff_cap, self._backoff_base * (2 ** (failures - 1)))

    def _reopen(self) -> None:
        if self._cap is not None:
            self._cap.release()
        self._cap = self._open_capture()

    def _open_capture(self) -> "Any":
        if self._capture_factory is not None:
            return self._capture_factory()
        if cv2 is None:  # checks the live reference (also catches a patched-out cv2)
            raise RuntimeError(
                "OpenCV (cv2) is unavailable — RtspSource needs it to decode the "
                "stream. Install opencv-python on the host (dev dep) or the system "
                "GStreamer-enabled OpenCV on the Jetson. See docs/SOFTWARE_STACK.md."
            ) from _CV2_IMPORT_ERROR
        if self._pipeline is not None:  # pragma: no cover - target GStreamer/NVDEC path
            return cv2.VideoCapture(self._pipeline, cv2.CAP_GSTREAMER)
        return cv2.VideoCapture(  # pragma: no cover - needs a real stream
            inject_cred(self._url, self._cred), cv2.CAP_FFMPEG
        )

    def __repr__(self) -> str:
        # Never expose credentials (AC6): the base URL is shown, cred is omitted.
        return "RtspSource(source_id={!r}, url={!r})".format(self._source_id, self._url)


__all__ = ["RtspSource", "inject_cred"]
