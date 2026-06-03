"""Application entrypoint — pipeline orchestration (#38).

Brings the pipeline up as a **single process with internal supervision** (the
2026-06-03 grooming decision; deployed as one systemd unit, see #43). It
constructs the bus and the stages, wraps them in a
:class:`~overwatch.orchestrator.Supervisor`, starts them in order
(capture -> inference -> fusion -> output), supervises/restarts crashed stages,
and shuts everything down cleanly on SIGTERM/SIGINT.

Host vs target: the orchestration spine (``orchestrator.py``) and the glue here
(``CaptureStage``, ``run_pipeline``) are host-unit-tested. The *live* run is
TARGET-ONLY — ``build_supervisor`` constructs the real ZED capture source (and,
as they land, the DeepStream / TensorRT stages), whose imports are guarded so
this module still imports on the host but ``main()`` only runs on the Jetson.

As inference (#15), fusion, and output stages gain runnable services, add them to
``_build_stages`` in pipeline order — the supervisor handles the rest.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any, List, Optional

from overwatch.capture.service import run_capture
from overwatch.orchestrator import Stage, Supervisor

if TYPE_CHECKING:  # avoid importing concrete bus / capture impls at module top
    from overwatch.bus.base import MessageBus
    from overwatch.capture.base import CaptureSource
    from overwatch.config.schema import AppConfig

_LOG = logging.getLogger(__name__)


class CaptureStage(Stage):
    """Supervisable adapter over the capture spine (#14).

    Drives any :class:`~overwatch.capture.base.CaptureSource` via
    :func:`~overwatch.capture.service.run_capture`, republishing its frames onto
    the bus. ``run`` returns when the source is exhausted or the supervisor sets
    ``stop`` — so a live (endless) ZED source blocks until shutdown, while a
    finite replay source completes on its own.
    """

    def __init__(
        self, source: "CaptureSource", bus: "MessageBus", name: str = "capture"
    ) -> None:
        self._source = source
        self._bus = bus
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def run(self, stop: "threading.Event") -> None:
        n = run_capture(self._source, self._bus, stop=stop)
        _LOG.info("capture stage stopped after %d frames", n)


def _build_bus(cfg: "AppConfig") -> "MessageBus":
    """Construct the configured transport (ADR-0001 hybrid: ZeroMQ default)."""
    if cfg.bus.transport == "zeromq":
        from overwatch.bus.zeromq_bus import ZeroMqBus

        return ZeroMqBus(endpoint=cfg.bus.endpoint or "inproc://overwatch-bus")
    from overwatch.bus.redis_bus import RedisBus  # pragma: no cover - pending ADR-0001

    return RedisBus()


def _build_source(src_cfg: "Any") -> "CaptureSource":
    """Construct one capture source from its typed config (ADR-0006 #30/#31).

    Dispatches on the discriminated ``type``: ``zed`` -> the live ``ZedSource``
    (pyzed, target-only — its import is guarded so it only fails on the Jetson when
    pyzed is absent), ``rtsp`` -> the depth-less ``RtspSource`` (OpenCV; host-
    constructible — cv2 is only touched when the stream is opened). Heavy imports
    stay inside their branch so building the *other* source type never drags them in.
    """
    if src_cfg.type == "zed":
        from overwatch.capture.zed_source import ZedSource

        return ZedSource(source_id=src_cfg.source_id, fps=src_cfg.fps)
    if src_cfg.type == "rtsp":
        from overwatch.capture.rtsp_source import RtspSource

        return RtspSource(
            source_id=src_cfg.source_id,
            url=src_cfg.url,
            fps=src_cfg.fps,
            cred=src_cfg.cred,
        )
    raise ValueError("unknown capture source type: {!r}".format(src_cfg.type))


def _build_stages(cfg: "AppConfig", bus: "MessageBus") -> "List[Stage]":
    """Construct the runnable stages in pipeline order.

    One :class:`CaptureStage` per configured source (ADR-0006 multi-source) —
    named ``capture:<source_id>`` so each is a distinct, supervisable stage. The
    ``rtsp`` sources are host-runnable; a ``zed`` source makes this target-only
    (pyzed). Inference (#15), fusion, and output stages are appended here — in
    order — as they land.
    """
    stages: "List[Stage]" = []
    for src_cfg in cfg.capture.sources:
        source = _build_source(src_cfg)
        stages.append(CaptureStage(source, bus, name="capture:" + src_cfg.source_id))
    return stages


def build_supervisor(cfg: "AppConfig") -> Supervisor:
    """Wire the bus + stages into a Supervisor. TARGET-ONLY (constructs live stages)."""
    bus = _build_bus(cfg)
    stages = _build_stages(cfg, bus)
    return Supervisor(stages, bus=bus)


def run_pipeline(
    supervisor: "Supervisor",
    *,
    install_signals: bool = True,
    shutdown_event: "Optional[threading.Event]" = None,
) -> None:
    """Start the supervisor, block until a shutdown is requested, then tear down.

    SIGTERM/SIGINT set the shutdown event (when ``install_signals``); the supervisor
    is always shut down on the way out, giving the ordered, clean stop AC #38 wants.
    ``shutdown_event`` is injectable so the start/shutdown sequence is host-testable
    without delivering real OS signals.
    """
    shutdown_requested = shutdown_event if shutdown_event is not None else threading.Event()

    if install_signals:
        import signal

        def _request_shutdown(signum, frame):  # pragma: no cover - signal path
            _LOG.info("received signal %s; requesting shutdown", signum)
            shutdown_requested.set()

        signal.signal(signal.SIGINT, _request_shutdown)
        signal.signal(signal.SIGTERM, _request_shutdown)

    supervisor.start()
    try:
        shutdown_requested.wait()
    finally:
        supervisor.shutdown()


def main(config_path: "Optional[str]" = None) -> None:  # pragma: no cover - target-only
    """Construct the pipeline from config and run it until signalled. TARGET-ONLY."""
    from overwatch.config.loader import load_config, validate_secrets

    logging.basicConfig(level=logging.INFO)
    cfg = load_config(config_path)
    validate_secrets(cfg)  # fail loudly on a missing required secret before starting (#41)
    supervisor = build_supervisor(cfg)
    _LOG.info("starting overwatch pipeline (bus=%s)", cfg.bus.transport)
    run_pipeline(supervisor)


__all__ = ["CaptureStage", "build_supervisor", "run_pipeline", "main"]


if __name__ == "__main__":  # pragma: no cover
    main()
