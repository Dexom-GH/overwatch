"""Capture-stage service — drive a CaptureSource and publish onto the bus (#14).

This is the capture spine the whole pipeline stands on. It takes any
:class:`~overwatch.capture.base.CaptureSource` — the live ``ZedSource`` on the
Jetson, or a ``ReplaySource`` off-device — and republishes its
``(Frame, Optional[DepthFrame])`` stream onto the bus as ``capture.frame`` /
``capture.depth`` (see ``bus/topics.py``).

The driver (:func:`run_capture`) and the FPS / summary helpers
(:class:`FpsMeter`, :class:`FrameLogger`) are transport-agnostic and
host-runnable, so they are unit-tested off-device. Only the ``__main__`` demo —
which constructs the live ``ZedSource`` (pyzed) and a real bus — is target-only;
it is the AC5 "tiny subscriber" that prints frame ids + shapes + FPS on-device.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Callable, List, Optional

from overwatch.bus import topics
from overwatch.bus.schemas import Frame

if TYPE_CHECKING:  # avoid importing concrete bus / capture impls at module top
    from overwatch.bus.base import MessageBus
    from overwatch.capture.base import CaptureSource

_LOG = logging.getLogger(__name__)


def run_capture(
    source: "CaptureSource",
    bus: "MessageBus",
    *,
    max_frames: Optional[int] = None,
    stop: "Optional[threading.Event]" = None,
    on_frame: "Optional[Callable[[str], None]]" = None,
) -> int:
    """Publish a capture source's frames onto the bus; return the frame count.

    Opens ``source``, and for each ``(frame, depth)`` pair publishes the
    :class:`~overwatch.bus.schemas.Frame` on ``capture.frame`` and, when present,
    the paired :class:`~overwatch.bus.schemas.DepthFrame` on ``capture.depth``.
    RGB and depth come from a single ZED ``grab()`` so they share ``frame_id`` and
    ``timestamp`` — this driver preserves that pairing, it does not re-time them.

    Stops after ``max_frames`` pairs when given (handy for the demo / tests). When
    a ``stop`` event is supplied (the orchestrator's shutdown signal, see #38) the
    loop checks it before each frame and returns cleanly once it is set — letting
    the capture stage participate in an ordered pipeline shutdown. Always closes
    the source on exit, including on error.

    ``on_frame`` (if given) is called with the source id after each published
    frame — the per-frame liveness mark hook (#136); it never sees depth/bytes.
    """
    count = 0
    source.open()
    try:
        for frame, depth in source.frames():
            if stop is not None and stop.is_set():
                break
            bus.publish(topics.CAPTURE_FRAME, frame)
            if depth is not None:
                bus.publish(topics.CAPTURE_DEPTH, depth)
            if on_frame is not None:
                on_frame(frame.source_id)   # liveness mark (#136) — per published frame
            count += 1
            if max_frames is not None and count >= max_frames:
                break
    finally:
        source.close()
    return count


class FpsMeter:
    """Sliding-window frame-rate estimate over recent arrival times.

    ``tick(now)`` records a timestamp (seconds, monotonic) and returns the current
    estimate: ``(n-1) / (t_last - t_first)`` over the retained window. Returns
    ``0.0`` until at least two samples exist.
    """

    def __init__(self, window: int = 30) -> None:
        self._window = max(2, window)
        self._times: List[float] = []

    def tick(self, now: float) -> float:
        self._times.append(now)
        if len(self._times) > self._window:
            self._times = self._times[-self._window:]
        return self.fps()

    def fps(self) -> float:
        if len(self._times) < 2:
            return 0.0
        span = self._times[-1] - self._times[0]
        if span <= 0:
            return 0.0
        return (len(self._times) - 1) / span


class FrameLogger:
    """Bus handler that reports each frame's id + shape + rolling FPS (AC5).

    Subscribe an instance to ``capture.frame``; it emits one line per frame via
    ``sink`` (defaults to the module logger). ``clock`` and ``sink`` are injected
    so the formatting/rate logic is host-testable without real time or I/O.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = None,  # type: ignore[assignment]
        sink: Callable[[str], None] = None,  # type: ignore[assignment]
        window: int = 30,
    ) -> None:
        import time

        self._clock = clock if clock is not None else time.monotonic
        self._sink = sink if sink is not None else _LOG.info
        self._meter = FpsMeter(window=window)

    def __call__(self, frame: Frame) -> None:
        fps = self._meter.tick(self._clock())
        self._sink(
            "capture: frame {} {}x{} fps={:.1f}".format(
                frame.frame_id, frame.width, frame.height, fps
            )
        )


def _demo(argv: "Optional[List[str]]" = None) -> int:  # pragma: no cover - target-only
    """On-device demo (AC5): live ZED -> bus -> FrameLogger printing id/shape/FPS.

    TARGET-ONLY: constructs the live ``ZedSource`` (pyzed) and a real ZeroMQ bus,
    so it is not exercised on the host. Verified on the Jetson once the ZED
    enumerates on USB-3 (#54). Run: ``python -m overwatch.capture.service --frames 90``.
    """
    import argparse

    from overwatch.bus.zeromq_bus import ZeroMqBus
    from overwatch.capture.zed_source import ZedSource
    from overwatch.config.loader import load_config

    parser = argparse.ArgumentParser(description="ZED capture spine demo (#14)")
    parser.add_argument("--frames", type=int, default=None, help="stop after N frames")
    parser.add_argument("--config", default=None, help="override config YAML path")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO)
    cfg = load_config(args.config)
    source = ZedSource(source_id=cfg.capture.source_id, fps=cfg.capture.fps)
    logger = FrameLogger(sink=print)

    bus = ZeroMqBus(endpoint=cfg.bus.endpoint or "inproc://overwatch-bus")
    bus.subscribe(topics.CAPTURE_FRAME, logger)  # subscribe before start()
    bus.start()
    try:
        n = run_capture(source, bus, max_frames=args.frames)
    finally:
        bus.close()
    print("captured {} frames".format(n))
    return 0


__all__ = ["run_capture", "FpsMeter", "FrameLogger"]


if __name__ == "__main__":  # pragma: no cover - target-only entrypoint
    raise SystemExit(_demo())
