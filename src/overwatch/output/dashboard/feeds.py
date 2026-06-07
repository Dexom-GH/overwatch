"""Dashboard feed producers (#132): fill a :class:`FrameSlot` with JPEG frames.

These are **testing/dev feed sources** so the operator console can show a live
image without the full DeepStream pipeline:

- :class:`RtspFeeder` — decode an RTSP camera directly with ``cv2`` (host + device)
  and JPEG-encode each frame into the slot. Reuses
  :func:`overwatch.capture.rtsp_source.inject_cred` for credentials and mirrors the
  read/reconnect pattern.
- :class:`MockFeeder` — a synthetic test pattern for fully-offline dev (no camera,
  no pipeline).

The detection feed (#120) is *not* here — it is filled by the DeepStream pipeline
(``InferenceStage``); these are the additional sources the dashboard can switch
between (``/api/feed/{source}``).

``cv2`` is imported lazily (and is injectable) so ``import overwatch`` and the
dashboard module import stay clean where OpenCV is absent. Pure-``threading``
lifecycle. Python 3.8-compatible.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from overwatch.capture.rtsp_source import inject_cred
from overwatch.output.dashboard.frame_slot import FrameSlot

_LOG = logging.getLogger(__name__)


class _ThreadedFeeder:
    """Common start/stop lifecycle for a feed producer running on a daemon thread."""

    _name = "feed"

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: "Optional[threading.Thread]" = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None

    def _run(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError


class RtspFeeder(_ThreadedFeeder):
    """Decode an RTSP camera with ``cv2`` and JPEG-encode frames into ``slot``.

    A standalone read loop (not :class:`RtspSource`'s generator) so :meth:`stop`
    ends it promptly: each iteration checks the stop flag, so a live camera at
    ~15 fps stops within a frame. On read failure it reconnects (bounded backoff)
    unless ``reconnect`` is False (finite sources / tests). ``capture_factory`` and
    ``encode`` are injectable so the loop is host-testable without cv2 or a network.
    """

    _name = "dashboard-feed-rtsp"

    def __init__(
        self,
        slot: "Any",
        url: str,
        *,
        cred: "Optional[str]" = None,
        fps: int = 8,
        reconnect: bool = True,
        backoff_seconds: float = 1.0,
        capture_factory: "Optional[Callable[[], Any]]" = None,
        encode: "Optional[Callable[[Any], Optional[bytes]]]" = None,
        sleep: "Callable[[float], None]" = time.sleep,
    ) -> None:
        super().__init__()
        self._slot = slot
        self._url = url  # base URL; credentials are spliced at open time, never stored joined
        self._cred = cred
        self._reconnect = reconnect
        self._backoff = backoff_seconds
        self._capture_factory = capture_factory
        self._encode = encode
        self._sleep = sleep

    def _open(self) -> "Any":
        if self._capture_factory is not None:
            return self._capture_factory()
        import cv2  # lazy: only needed when actually decoding

        return cv2.VideoCapture(inject_cred(self._url, self._cred), cv2.CAP_FFMPEG)

    def _to_jpeg(self, image: "Any") -> "Optional[bytes]":
        if self._encode is not None:
            return self._encode(image)
        import cv2

        ok, buf = cv2.imencode(".jpg", image)
        return buf.tobytes() if ok else None

    def _run(self) -> None:
        while not self._stop.is_set():
            cap = None
            try:
                cap = self._open()
            except Exception as exc:  # defensive: a factory/cv2 error -> reconnect
                _LOG.warning("rtsp feed: open failed: %s", exc)
            if cap is None or not cap.isOpened():
                if cap is not None:
                    cap.release()
                if not self._reconnect:
                    return
                self._sleep(self._backoff)
                continue
            try:
                while not self._stop.is_set():
                    ok, image = cap.read()
                    if not ok:
                        break
                    jpeg = self._to_jpeg(image)
                    if jpeg is not None:
                        self._slot.put(jpeg)
            finally:
                cap.release()
            if not self._reconnect:
                return
            if not self._stop.is_set():
                self._sleep(self._backoff)


class MockFeeder(_ThreadedFeeder):
    """Fill ``slot`` with a synthetic JPEG test pattern (offline dev, no camera).

    ``render(n) -> jpeg bytes`` is injectable; the default draws a moving box +
    clock with ``cv2``/``numpy``.
    """

    _name = "dashboard-feed-mock"

    def __init__(
        self,
        slot: "Any",
        *,
        fps: int = 8,
        render: "Optional[Callable[[int], Optional[bytes]]]" = None,
        sleep: "Callable[[float], None]" = time.sleep,
    ) -> None:
        super().__init__()
        self._slot = slot
        self._interval = 1.0 / fps if fps > 0 else 0.0
        self._render = render or _render_mock_frame
        self._sleep = sleep

    def _run(self) -> None:
        n = 0
        while not self._stop.is_set():
            jpeg = self._render(n)
            if jpeg is not None:
                self._slot.put(jpeg)
            n += 1
            if self._interval:
                self._sleep(self._interval)


def _render_mock_frame(n: int) -> "Optional[bytes]":  # pragma: no cover - cv2 drawing
    """Draw a synthetic 1280x720 frame (moving box + clock) as JPEG bytes."""
    import cv2
    import numpy as np

    img = np.full((720, 1280, 3), 32, dtype=np.uint8)
    img[:] = (38, 34, 30)
    x = 120 + int(360 + 340 * np.sin(n / 18.0))
    cv2.rectangle(img, (x, 300), (x + 190, 520), (80, 220, 90), 3)
    cv2.putText(img, "sheep 0.94 id:42", (x, 292), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (80, 220, 90), 2)
    cv2.putText(img, "OVERWATCH mock feed", (24, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (220, 220, 220), 2)
    cv2.putText(img, time.strftime("%H:%M:%S"), (24, 700), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (150, 150, 150), 1)
    ok, buf = cv2.imencode(".jpg", img)
    return buf.tobytes() if ok else None


def make_aux_feeds(
    *,
    rtsp_enabled: bool,
    rtsp_url: "Optional[str]",
    rtsp_cred: "Optional[str]" = None,
    mock_enabled: bool,
    fps: int = 8,
) -> "Tuple[Dict[str, FrameSlot], List[_ThreadedFeeder]]":
    """Build the optional (non-pipeline) dashboard feeds from config (#132).

    Returns ``(feeds, feeders)`` where ``feeds`` maps source name -> slot for the
    server and ``feeders`` are the producers to start/stop. The detection feed is
    NOT built here — that slot is filled by the DeepStream pipeline. ``raw`` needs a
    URL (skipped with a warning if enabled without one).
    """
    feeds: "Dict[str, FrameSlot]" = {}
    feeders: "List[_ThreadedFeeder]" = []
    if rtsp_enabled:
        if rtsp_url:
            slot = FrameSlot()
            feeders.append(RtspFeeder(slot, rtsp_url, cred=rtsp_cred, fps=fps))
            feeds["raw"] = slot
        else:
            _LOG.warning("dashboard raw feed enabled but no RTSP url resolved — skipping")
    if mock_enabled:
        slot = FrameSlot()
        feeders.append(MockFeeder(slot, fps=fps))
        feeds["mock"] = slot
    return feeds, feeders


__all__ = ["RtspFeeder", "MockFeeder", "make_aux_feeds"]
