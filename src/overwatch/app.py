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
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

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
        self,
        source: "CaptureSource",
        bus: "MessageBus",
        name: str = "capture",
        *,
        liveness: "Optional[Any]" = None,
    ) -> None:
        self._source = source
        self._bus = bus
        self._name = name
        self._liveness = liveness  # LivenessTracker (#136); marked per published frame

    @property
    def name(self) -> str:
        return self._name

    def run(self, stop: "threading.Event") -> None:
        on_frame = None
        if self._liveness is not None:
            liveness = self._liveness
            on_frame = lambda source_id: liveness.mark(source_id, time.monotonic())  # noqa: E731
        n = run_capture(self._source, self._bus, stop=stop, on_frame=on_frame)
        _LOG.info("capture stage stopped after %d frames", n)


class FusionStage(Stage):
    """Subscribes ``infer.track``, runs the mono fusion rules, publishes ``output.alert``.

    Host-runnable: wraps the #79 :class:`~overwatch.fusion.mono_alerts.MonoAlertFanout`
    (fence #20 / immobility #19 / count #33). Subscribes in ``__init__`` (before the
    supervisor starts the bus — the ZeroMqBus contract is subscribe-before-start);
    the fanout runs on the bus dispatch thread and publishes alerts via the locked
    bus. ``run`` blocks until shutdown, then flushes the trailing frame.
    """

    def __init__(self, bus: "MessageBus", cfg: "AppConfig") -> None:
        from overwatch.bus import topics
        from overwatch.fusion.mono_alerts import MonoAlertFanout

        self._bus = bus
        # Zone count thresholds are not in the Zone schema yet (follow-up); read
        # defensively so fence + immobility alerts work today, zone-count later.
        thresholds = {}
        for z in cfg.fusion.zones:
            ct = getattr(z, "count_threshold", None)
            if ct is not None:
                thresholds[z.name] = ct
        self._fanout = MonoAlertFanout(
            self._publish_alert,
            fences=cfg.fusion.fences,
            zones=cfg.fusion.zones,
            zone_thresholds=thresholds,
            immobility_seconds=cfg.fusion.health.immobility_seconds,
            immobility_classes=cfg.fusion.health.immobility_classes,
            immobility_class_seconds=cfg.fusion.health.immobility_class_seconds,
            record_sink=self._publish_record,
            # Stamp records with WALL-CLOCK time: the durable store, the operator
            # dashboard's trailing-window query, and age-based retention all compare
            # against time.time(). The fanout defaults to time.monotonic (great for
            # dwell math, but monotonic timestamps fall outside the dashboard window
            # and break age pruning). Dwell elapsed is still correct under wall-clock.
            clock=time.time,
        )
        bus.subscribe(topics.INFER_TRACK, self._fanout.on_track)

    @property
    def name(self) -> str:
        return "fusion"

    def _publish_alert(self, alert: "Any") -> None:
        from overwatch.bus import topics

        self._bus.publish(topics.OUTPUT_ALERT, alert)

    def _publish_record(self, record: "Any") -> None:
        """Publish a raw fusion record on its durable topic (#111).

        Surfaces the monitoring records the fanout produces (counts on change,
        health signals, events) so the durable store (#108) and dashboard (#18)
        populate — not just the Alerts. No schema change; the topics already exist.
        """
        from overwatch.bus import topics
        from overwatch.bus.schemas import Event, HealthSignal, ZoneCount

        if isinstance(record, ZoneCount):
            self._bus.publish(topics.FUSION_COUNT, record)
        elif isinstance(record, HealthSignal):
            self._bus.publish(topics.FUSION_HEALTH, record)
        elif isinstance(record, Event):
            self._bus.publish(topics.FUSION_EVENT, record)

    def run(self, stop: "threading.Event") -> None:
        stop.wait()
        self._fanout.flush()


class OutputStage(Stage):
    """Subscribes ``output.alert`` and delivers via the throttled Slack sink.

    Host-runnable. Subscribes in ``__init__``; delivery runs on the bus dispatch
    thread. ``run`` blocks until shutdown.
    """

    def __init__(
        self, bus: "MessageBus", cfg: "AppConfig", *, sink: "Optional[Any]" = None
    ) -> None:
        from overwatch.bus import topics

        self._bus = bus
        if sink is None:
            from overwatch.output.slack import SlackAlertSink, ThrottledAlertSink
            from overwatch.output.throttle import AlertThrottle

            throttle = AlertThrottle.from_config(cfg.output.throttle)
            sink = ThrottledAlertSink(SlackAlertSink(cfg.output.slack.webhook or ""), throttle)
        self._sink = sink
        bus.subscribe(topics.OUTPUT_ALERT, self._sink.send)

    @property
    def name(self) -> str:
        return "output"

    def run(self, stop: "threading.Event") -> None:
        stop.wait()


class StoreStage(Stage):
    """Persists the pipeline's durable records to the EventStore (#108). Host-runnable.

    The durable-tier **writer**: subscribes the record topics and records each
    message to the configured EventStore so the operator dashboard (#18) and the
    retention sweeper (#106) have real data that survives a restart. Subscribes
    ``output.alert`` (recording each ``Alert`` and, when present, its
    ``source_event`` so fence/immobility ``Event`` rows populate) plus the raw
    ``fusion.count`` / ``fusion.health`` / ``fusion.event`` topics — so the moment
    fusion publishes those (tracked separately) they persist with no sink change.

    The store opens **lazily on first record** (so constructing the stage has no
    filesystem side effect) unless a ``store`` is injected (tests). Subscriptions
    are registered in ``__init__`` (before the bus starts, per the ZeroMqBus
    contract); ``run`` blocks until shutdown, then closes an owned store.
    """

    def __init__(
        self,
        bus: "MessageBus",
        *,
        store: "Optional[Any]" = None,
        store_path: "Optional[str]" = None,
        name: str = "store",
    ) -> None:
        from overwatch.bus import topics

        self._store = store
        self._store_path = store_path
        self._own = store is None
        self._name = name
        self._lock = threading.Lock()
        bus.subscribe(topics.OUTPUT_ALERT, self._on_alert)
        for topic in (topics.FUSION_COUNT, topics.FUSION_HEALTH, topics.FUSION_EVENT):
            bus.subscribe(topic, self._on_record)

    @property
    def name(self) -> str:
        return self._name

    def _ensure_store(self) -> "Any":
        if self._store is None:
            with self._lock:
                if self._store is None:
                    if not self._store_path:
                        raise RuntimeError("StoreStage needs a store or a store_path")
                    from overwatch.output.sqlite_store import SqliteEventStore

                    self._store = SqliteEventStore(self._store_path)
        return self._store

    def _on_record(self, message: "Any") -> None:
        self._ensure_store().record(message)

    def _on_alert(self, alert: "Any") -> None:
        store = self._ensure_store()
        store.record(alert)
        source_event = getattr(alert, "source_event", None)
        if source_event is not None:
            store.record(source_event)

    def run(self, stop: "threading.Event") -> None:
        stop.wait()
        if self._own and self._store is not None:
            self._store.close()


class RetentionStage(Stage):
    """Periodically enforces the EventStore retention budget (#106). Host-runnable.

    A supervised sweeper: every ``interval_seconds`` it applies the configured
    :class:`~overwatch.output.retention.RetentionPolicy` to the durable EventStore
    (age prune + row cap, via ``enforce_event_store``) so 24/7 logging cannot fill
    the NVMe (#40, docs/STORAGE.md). The wait is on the ``stop`` event, so shutdown
    is immediate and clean — no leaked thread.

    The store is opened **lazily in** :meth:`run` from ``store_path`` (so building
    the stage has no filesystem side effect), unless a ``store`` is injected (tests).
    """

    def __init__(
        self,
        policy: "Any",
        *,
        interval_seconds: float,
        store: "Optional[Any]" = None,
        store_path: "Optional[str]" = None,
        now: "Optional[Any]" = None,
        name: str = "retention",
    ) -> None:
        import time

        self._policy = policy
        self._interval = interval_seconds
        self._store = store
        self._store_path = store_path
        self._now = now if now is not None else time.time
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def run(self, stop: "threading.Event") -> None:
        from overwatch.output.retention import enforce_event_store

        store = self._store
        own = False
        if store is None:
            from overwatch.output.sqlite_store import SqliteEventStore

            if not self._store_path:
                raise RuntimeError("RetentionStage needs a store or a store_path")
            store = SqliteEventStore(self._store_path)
            own = True
        try:
            while not stop.wait(self._interval):
                removed = enforce_event_store(store, self._policy, now=self._now())
                if removed:
                    _LOG.info("retention sweep pruned %d EventStore rows", removed)
        finally:
            if own:
                store.close()


class LivenessStage(Stage):
    """Periodically evaluates pipeline liveness; raises degraded/recovered alerts (#136).

    A supervised sweeper (mirrors :class:`RetentionStage`): every
    ``interval_seconds`` it calls the :class:`~overwatch.output.liveness_monitor.
    LivenessMonitor`, which raises a **throttled Slack degraded** alert when a source
    falls silent and a **recovered** alert when its frames resume. The wait is on the
    ``stop`` event, so shutdown is immediate. Host-runnable (the monitor is pure with
    an injected clock); a real source loss on the Jetson is the on-device bar (AC6).
    """

    def __init__(
        self, monitor: "Any", *, interval_seconds: float, name: str = "liveness"
    ) -> None:
        self._monitor = monitor
        self._interval = interval_seconds
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def run(self, stop: "threading.Event") -> None:
        while not stop.wait(self._interval):
            self._monitor.check()


class DashboardStage(Stage):
    """Serves the read-only operator console as a supervised stage (#110). Host-runnable.

    Wires the dashboard backend (`output/dashboard/server.py`; SPA + JSON API per
    ADR-0008 / #124) into the one supervised app process so the operator screen
    comes up and shuts down with the pipeline — `serve()` exists but otherwise has
    no launcher. Runs the HTTP `serve_forever` on its own thread; on ``stop`` it
    calls ``shutdown`` + ``server_close`` and joins (no leaked socket/thread).

    The server (and its read-only store handle) are built **lazily in** :meth:`run`
    from config (so constructing the stage binds no port), unless a ``server`` is
    injected (tests). For a standalone dashboard process, ``server.serve(cfg)``
    remains the alternative.
    """

    def __init__(
        self,
        cfg: "AppConfig",
        *,
        server: "Optional[Any]" = None,
        store: "Optional[Any]" = None,
        feeds: "Optional[Any]" = None,
        feeders: "Optional[Any]" = None,
        liveness: "Optional[Any]" = None,
        name: str = "dashboard",
    ) -> None:
        self._cfg = cfg
        self._server = server
        self._store = store
        self._liveness = liveness  # LivenessTracker (#136) -> /api/state liveness block
        # Live feeds (#120/#132): {source -> FrameSlot} served at /api/feed/{source};
        # `feeders` are the non-pipeline producers (raw/mock) this stage starts/stops.
        self._feeds = feeds or {}
        self._feeders = feeders or []
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def run(self, stop: "threading.Event") -> None:
        server = self._server
        own_store = None
        if server is None:
            from overwatch.output.dashboard.server import make_server

            dash = self._cfg.output.dashboard
            store = self._store
            if store is None:
                from overwatch.output.sqlite_store import SqliteEventStore

                store_path = self._cfg.output.store.path
                if not store_path:
                    raise RuntimeError("DashboardStage needs a sqlite store path")
                store = SqliteEventStore(store_path)
                own_store = store
            provider = None
            if self._liveness is not None:
                liveness = self._liveness
                provider = lambda: liveness.snapshot(time.monotonic())  # noqa: E731
            server = make_server(
                store,
                host=dash.host,
                port=dash.port,
                dist_dir=dash.dist_dir,
                feeds=self._feeds,
                feed_fps=dash.feed_fps,
                window_seconds=dash.window_seconds,
                refresh_seconds=dash.refresh_seconds,
                alert_limit=dash.alert_limit,
                event_limit=dash.event_limit,
                liveness_provider=provider,
            )
        for feeder in self._feeders:  # start the raw/mock producers (#132)
            feeder.start()
        thread = threading.Thread(
            target=server.serve_forever, name="dashboard-http", daemon=True
        )
        thread.start()
        try:
            stop.wait()
        finally:
            for feeder in self._feeders:
                feeder.stop()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5.0)
            if own_store is not None:
                own_store.close()


class InferenceStage(Stage):
    """Runs the DeepStream detect+track pipeline, publishing ``infer.track``. TARGET-ONLY.

    Wraps the #15 ``DeepStreamPipeline`` + tracker probe. The probe enqueues tracks
    (non-blocking, streaming thread); ``run`` drains the queue and publishes on the
    GLib main loop (this stage's thread) via the locked bus. Heavy imports
    (gi/pyds/DeepStream) are deferred to ``run`` so this module imports on the host.
    """

    def __init__(
        self,
        bus: "MessageBus",
        *,
        pgie_config: str,
        source: str,
        tracker_config: "Optional[str]" = None,
        labels: "Optional[List[str]]" = None,
        frame_slot: "Optional[Any]" = None,
        feed_fps: int = 8,
    ) -> None:
        self._bus = bus
        self._pgie_config = pgie_config
        self._source = source
        self._tracker_config = tracker_config
        self._labels = labels
        # Live-feed tap (#120): when set, the pipeline grows a burned-in MJPEG
        # branch off a tee whose appsink writes encoded frames into this slot for
        # the dashboard to stream. None -> no feed branch (pure detect+track).
        self._frame_slot = frame_slot
        self._feed_fps = feed_fps

    @property
    def name(self) -> str:
        return "inference"

    def run(self, stop: "threading.Event") -> None:  # pragma: no cover - target-only
        import queue

        from gi.repository import GLib  # type: ignore

        from overwatch.bus import topics
        from overwatch.inference.deepstream.pipeline import DeepStreamPipeline
        from overwatch.inference.deepstream.probes import make_tracker_probe

        pipe = DeepStreamPipeline(
            pgie_config=self._pgie_config, tracker_config=self._tracker_config
        )
        pipe.build(self._source, frame_slot=self._frame_slot, feed_fps=self._feed_fps)
        q: "queue.Queue" = queue.Queue(maxsize=20000)
        pipe.attach_probe(
            make_tracker_probe(
                lambda t: q.put_nowait(t) if not q.full() else None, labels=self._labels
            )
        )

        def _drain() -> bool:
            while True:
                try:
                    track = q.get_nowait()
                except queue.Empty:
                    break
                self._bus.publish(topics.INFER_TRACK, track)
            return True

        GLib.timeout_add(50, _drain)

        def _watch_stop() -> None:
            stop.wait()
            pipe.quit()

        threading.Thread(target=_watch_stop, daemon=True).start()
        pipe.run()  # blocks on the GLib loop until EOS / stop
        _drain()


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


def _build_stages(
    cfg: "AppConfig", bus: "MessageBus", *, liveness: "Optional[Any]" = None
) -> "List[Stage]":
    """Construct the runnable stages in pipeline order.

    Full pipeline order (#38): capture -> inference -> fusion -> output. One
    :class:`CaptureStage` per configured source (ADR-0006), then a single
    :class:`InferenceStage` (DeepStream; multi-source nvstreammux batching is #32),
    :class:`FusionStage`, and :class:`OutputStage`. The ``rtsp``/fusion/output
    stages are host-runnable; ``zed`` capture and the live InferenceStage run are
    target-only (pyzed / DeepStream).
    """
    stages: "List[Stage]" = []
    # capture (one per source; ZED is target-only/import-guarded)
    for src_cfg in cfg.capture.sources:
        source = _build_source(src_cfg)
        if liveness is not None:
            liveness.register(src_cfg.source_id)  # show the source as down before its first frame
        stages.append(
            CaptureStage(source, bus, name="capture:" + src_cfg.source_id, liveness=liveness)
        )

    # inference -> fusion -> output, in pipeline order. One InferenceStage drives
    # the DeepStream pipeline; multi-source nvstreammux batching is #32. The
    # inference source is the first source's URL (RTSP); a ZED source feeds
    # DeepStream via the #6 seam (deferred) — source_id is a placeholder until then.
    src0 = cfg.capture.sources[0]
    # The DeepStream leg decodes the RTSP stream independently (ADR-0006), so an
    # authenticated camera needs the credential spliced into the inference source
    # too — else nvurisrcbin gets a bare URL and the camera 401s (no detections,
    # #84). inject_cred is a no-op for a None cred or a non-URL (ZED/file) source,
    # so this is safe for every source type. The credentialed URL must not be logged.
    from overwatch.capture.rtsp_source import inject_cred

    infer_source = inject_cred(
        getattr(src0, "url", None) or src0.source_id, getattr(src0, "cred", None)
    )
    # Resolve detector class names (#91) so Track.class_name — and thus the
    # operator's Slack alert — reads "sheep", not "0". None falls back to ids.
    from overwatch.inference.deepstream.pipeline import load_detector_labels

    labels = load_detector_labels(cfg.inference.detector_config)

    # Dashboard live feeds (#120/#132). The dashboard serves a map of {source ->
    # FrameSlot} and the SPA toggles between them:
    #   detection — burned-in DeepStream tap; the slot is shared with InferenceStage
    #               (producer) and stays off the bus.
    #   raw/mock  — non-pipeline producers (cv2 RTSP / synthetic) owned by the
    #               DashboardStage. Built only when the dashboard is on.
    dash_cfg = cfg.output.dashboard
    store_cfg = cfg.output.store
    dashboard_on = dash_cfg.enabled and store_cfg.backend == "sqlite" and bool(store_cfg.path)
    feeds: "Dict[str, Any]" = {}
    feeders: "List[Any]" = []
    detection_slot = None
    if dashboard_on:
        from overwatch.output.dashboard.feeds import make_aux_feeds
        from overwatch.output.dashboard.frame_slot import FrameSlot

        if dash_cfg.feed_enabled:
            detection_slot = FrameSlot()
            feeds["detection"] = detection_slot
        # raw feed url: explicit override, else the first rtsp capture source (+cred)
        rtsp_url, rtsp_cred = dash_cfg.feed_rtsp_url, None
        if dash_cfg.feed_rtsp_enabled and not rtsp_url:
            for s in cfg.capture.sources:
                if getattr(s, "type", None) == "rtsp":
                    rtsp_url, rtsp_cred = getattr(s, "url", None), getattr(s, "cred", None)
                    break
        aux_feeds, feeders = make_aux_feeds(
            rtsp_enabled=dash_cfg.feed_rtsp_enabled,
            rtsp_url=rtsp_url,
            rtsp_cred=rtsp_cred,
            mock_enabled=dash_cfg.feed_mock_enabled,
            fps=dash_cfg.feed_fps,
        )
        feeds.update(aux_feeds)

    stages.append(
        InferenceStage(
            bus,
            pgie_config=cfg.inference.detector_config,
            source=infer_source,
            tracker_config=cfg.inference.tracker_config,
            labels=labels,
            frame_slot=detection_slot,
            feed_fps=dash_cfg.feed_fps,
        )
    )
    stages.append(FusionStage(bus, cfg))
    stages.append(OutputStage(bus, cfg))

    # Liveness monitor (#136): a sweeper that raises a throttled Slack degraded /
    # recovered alert as sources fall silent / return. Independent of the durable
    # store (Slack-only path); `enabled` gates the Slack signal, not the dashboard
    # badge. Keyed per-source via its own throttle, so it posts directly to Slack
    # rather than through the main (zone/track-keyed) output throttle.
    if liveness is not None and cfg.output.liveness.enabled:
        from overwatch.output.liveness_monitor import LivenessMonitor
        from overwatch.output.slack import SlackAlertSink

        monitor = LivenessMonitor(
            liveness,
            SlackAlertSink(cfg.output.slack.webhook or "").send,
            cooldown_seconds=cfg.output.throttle.cooldown_seconds,
        )
        stages.append(
            LivenessStage(monitor, interval_seconds=cfg.output.liveness.check_interval_seconds)
        )

    # Durable tier (sqlite backend): the StoreStage writes records and the
    # RetentionStage bounds them during 24/7 operation. Both open the store lazily
    # from the configured path (no filesystem side effect at construction).
    if store_cfg.backend == "sqlite" and store_cfg.path:
        from overwatch.output.retention import RetentionPolicy

        stages.append(StoreStage(bus, store_path=store_cfg.path))
        stages.append(
            RetentionStage(
                RetentionPolicy.from_config(store_cfg.retention),
                interval_seconds=store_cfg.retention.interval_seconds,
                store_path=store_cfg.path,
            )
        )
        if dash_cfg.enabled:
            stages.append(DashboardStage(cfg, feeds=feeds, feeders=feeders, liveness=liveness))
    return stages


def _liveness_on_event(liveness: "Any") -> "Any":
    """A Supervisor ``on_event`` hook that records stage restarts into the tracker (#136, AC3)."""

    def on_event(event: str, name: "Optional[str]") -> None:
        _LOG.info("orchestrator event: %s%s", event, "" if name is None else " [" + name + "]")
        if event == "restart" and name is not None:
            liveness.note_restart(name, time.monotonic())

    return on_event


def build_supervisor(cfg: "AppConfig") -> Supervisor:
    """Wire the bus + stages into a Supervisor. TARGET-ONLY (constructs live stages)."""
    from overwatch.output.liveness import LivenessTracker

    bus = _build_bus(cfg)
    # One shared, in-process liveness tracker (#136): capture marks it, the dashboard
    # reads it, the monitor alerts on it, and the supervisor records restarts into it.
    liveness = LivenessTracker(silence_seconds=cfg.output.liveness.silence_seconds)
    stages = _build_stages(cfg, bus, liveness=liveness)
    return Supervisor(stages, bus=bus, on_event=_liveness_on_event(liveness))


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


__all__ = [
    "CaptureStage",
    "StoreStage",
    "RetentionStage",
    "DashboardStage",
    "build_supervisor",
    "run_pipeline",
    "main",
]


if __name__ == "__main__":  # pragma: no cover
    main()
