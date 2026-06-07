"""Operator-console backend — serves the prebuilt SPA + a JSON data API (#124).

Resolves ADR-0008 (client architecture): the operator console is a single-page
app **built in CI** and shipped to the Jetson as a static ``dist/`` bundle. This
backend serves that bundle as static assets and exposes a small **JSON data API**
sourced from the durable :class:`~overwatch.output.store.EventStore`. It fully
**supersedes the #18 read-only HTML surface** (no more server-rendered HTML).

**Read-only by construction.** Only ``GET``/``HEAD`` routes are defined; any
mutating method (``POST``/``PUT``/...) gets ``405`` from FastAPI/StaticFiles and
never reaches the store. The dashboard is a *consumer* of stored records.

**Stack:** FastAPI + uvicorn (pure-Python; installs on the Jetson 3.8 runtime).
:func:`make_server` returns a :class:`DashboardServer` whose
``serve_forever``/``shutdown``/``server_close``/``server_address`` surface matches
``http.server`` so the supervised :class:`~overwatch.app.DashboardStage` (#110)
drives it unchanged.

Host vs target: the whole backend is host-runnable and unit-tested (FastAPI
``TestClient`` + in-memory SQLite). The frontend build is a CI/host artifact — the
device only *serves* the prebuilt ``dist/`` and never runs Node. The on-device leg
(DoD) is the SPA shell loading from the bundled ``dist/`` on the Jetson.

Python 3.8-compatible.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from overwatch.output.dashboard.view import build_dashboard_state

if TYPE_CHECKING:
    from fastapi import FastAPI

    from overwatch.bus.schemas import Alert, Event, ZoneCount
    from overwatch.output.dashboard.frame_slot import FrameSlot
    from overwatch.output.dashboard.view import DashboardState
    from overwatch.output.liveness import LivenessSnapshot
    from overwatch.output.store import EventStore

_LOG = logging.getLogger(__name__)

# Where the backend serves the prebuilt SPA from by default: ``web/dist`` next to
# this package. It is produced by the CI SPA build (#124) and shipped by
# ``scripts/target/deploy.sh``; the device never builds it. Absent -> API only.
_DEFAULT_DIST = Path(__file__).resolve().parent / "web" / "dist"


# --- JSON serialization (the SPA data contract) ----------------------------


def _zone_count_dict(c: "ZoneCount") -> "Dict[str, Any]":
    return {
        "zone_id": c.zone_id,
        "timestamp": c.timestamp,
        "count": c.count,
        "class_name": c.class_name,
    }


def _event_dict(e: "Event") -> "Dict[str, Any]":
    return {
        "timestamp": e.timestamp,
        "kind": e.kind,
        "track_id": e.track_id,
        "zone_id": e.zone_id,
    }


def _alert_dict(a: "Alert") -> "Dict[str, Any]":
    return {
        "timestamp": a.timestamp,
        "severity": a.severity,
        "title": a.title,
        "message": a.message,
    }


def dashboard_summary(state: "DashboardState") -> "Dict[str, Any]":
    """At-a-glance rollup of the snapshot for the console's info panel (#121).

    Derived purely from the snapshot's lists, so the counts reflect the trailing
    window the snapshot was built over (alerts/events are the recent, limited
    lists). ``last_activity_at`` is the newest alert-or-event timestamp, or
    ``None`` when the window is quiet.
    """
    alerts = state.recent_alerts
    events = state.recent_events
    times = [a.timestamp for a in alerts] + [e.timestamp for e in events]
    return {
        "total_count": sum(c.count for c in state.zone_counts),
        "zones_reporting": len(state.zone_counts),
        "recent_alert_count": len(alerts),
        "critical_alert_count": sum(1 for a in alerts if a.severity == "critical"),
        "recent_event_count": len(events),
        "last_activity_at": max(times) if times else None,
    }


def _liveness_dict(snapshot: "LivenessSnapshot") -> "Dict[str, Any]":
    """Serialize a liveness snapshot for the SPA (#136) — flat, JSON-safe."""
    return {
        "degraded": snapshot.degraded,
        "sources": [
            {
                "source_id": s.source_id,
                "up": s.up,
                "last_frame_age_s": s.last_frame_age_s,
            }
            for s in snapshot.sources
        ],
        "recent_restarts": snapshot.recent_restarts,
    }


def state_dict(
    state: "DashboardState",
    *,
    refresh_seconds: int = 5,
    liveness: "Optional[Dict[str, Any]]" = None,
) -> "Dict[str, Any]":
    """Serialize a :class:`DashboardState` to the JSON shape the SPA consumes.

    Explicit, flat dicts (not raw dataclasses) so the client contract is stable and
    nothing heavy (numpy, nested ``detail``) leaks across the wire. ``liveness`` is
    the operator-visible degraded/liveness block (#136), or ``None`` when liveness
    tracking is not wired (the SPA hides the badge).
    """
    return {
        "generated_at": state.generated_at,
        "refresh_seconds": refresh_seconds,
        "summary": dashboard_summary(state),
        "zone_counts": [_zone_count_dict(c) for c in state.zone_counts],
        "recent_alerts": [_alert_dict(a) for a in state.recent_alerts],
        "recent_events": [_event_dict(e) for e in state.recent_events],
        "liveness": liveness,
    }


# --- MJPEG live feed (#120, ADR-0008) --------------------------------------

# multipart/x-mixed-replace boundary — the browser renders this stream in an <img>.
_MJPEG_BOUNDARY = "frame"
_MJPEG_BOUNDARY_B = _MJPEG_BOUNDARY.encode("ascii")


def _multipart_chunk(jpeg: bytes) -> bytes:
    """One multipart part wrapping a JPEG frame, per ``multipart/x-mixed-replace``."""
    return (
        b"--" + _MJPEG_BOUNDARY_B + b"\r\n"
        b"Content-Type: image/jpeg\r\n"
        b"Content-Length: " + str(len(jpeg)).encode("ascii") + b"\r\n\r\n"
        + jpeg + b"\r\n"
    )


def mjpeg_stream(
    slot: "FrameSlot",
    *,
    fps: int = 8,
    wait_timeout: float = 1.0,
    sleep: "Callable[[float], None]" = time.sleep,
):
    """Yield multipart MJPEG chunks of the *latest* frame in ``slot``, throttled to ``fps``.

    Blocks on ``slot.wait_for`` so it only emits fresh frames (skipping any produced
    faster than ``fps`` — latest-frame, never a backlog) and burns no CPU while the
    source is idle. Runs until the consumer (the streaming HTTP response) is closed.
    """
    interval = 1.0 / fps if fps > 0 else 0.0
    last_seq = -1
    while True:
        frame, seq = slot.wait_for(last_seq, wait_timeout)
        if frame is None or seq == last_seq:
            continue  # source idle / no fresher frame within the timeout — keep waiting
        last_seq = seq
        yield _multipart_chunk(frame)
        if interval:
            sleep(interval)


# --- FastAPI app -----------------------------------------------------------


# Preferred display order of feed sources (#132); others sort after, alphabetically.
_FEED_ORDER = ("detection", "raw", "mock")


def create_app(
    store: "EventStore",
    *,
    dist_dir: "Optional[str]" = None,
    feeds: "Optional[Dict[str, FrameSlot]]" = None,
    feed_fps: int = 8,
    now: "Callable[[], float]" = time.time,
    window_seconds: float = 3600.0,
    refresh_seconds: int = 5,
    alert_limit: int = 10,
    event_limit: int = 10,
    liveness_provider: "Optional[Callable[[], Optional[LivenessSnapshot]]]" = None,
) -> "FastAPI":
    """Build the read-only dashboard FastAPI app (host-testable without a socket).

    ``GET /api/state`` rebuilds the snapshot from the EventStore at request time
    (so a polling client always sees current data). If a built SPA ``dist/`` exists
    it is mounted at ``/`` (the API routes are registered first, so they win); when
    it is absent the backend still serves the JSON API. ``now`` is injectable so the
    trailing window is deterministic in tests.
    """
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import StreamingResponse
    from fastapi.staticfiles import StaticFiles

    app = FastAPI(title="Overwatch operator console", docs_url=None, redoc_url=None)
    feed_slots: "Dict[str, FrameSlot]" = dict(feeds or {})

    def _feed_names() -> "List[str]":
        ordered = [n for n in _FEED_ORDER if n in feed_slots]
        return ordered + sorted(n for n in feed_slots if n not in _FEED_ORDER)

    @app.get("/api/health")
    def api_health() -> "Dict[str, str]":
        return {"status": "ok"}

    @app.get("/api/state")
    def api_state() -> "Dict[str, Any]":
        state = build_dashboard_state(
            store,
            now=now(),
            window_s=window_seconds,
            alert_limit=alert_limit,
            event_limit=event_limit,
        )
        snapshot = liveness_provider() if liveness_provider is not None else None
        liveness = _liveness_dict(snapshot) if snapshot is not None else None
        return state_dict(state, refresh_seconds=refresh_seconds, liveness=liveness)

    # Live feeds (#120 detection / #132 raw + mock). /api/feeds lists the available
    # sources (so the SPA builds its toggle); /api/feed/{source} streams one as
    # MJPEG (multipart/x-mixed-replace, rendered in an <img> with no client JS).
    # Read-only: GET only; an unknown/absent source -> 404 (SPA shows "offline").
    @app.get("/api/feeds")
    def api_feeds() -> "Dict[str, Any]":
        names = _feed_names()
        return {"feeds": names, "default": names[0] if names else None}

    @app.get("/api/feed/{source}")
    def api_feed(source: str) -> "StreamingResponse":
        slot = feed_slots.get(source)
        if slot is None:
            raise HTTPException(status_code=404, detail="unknown feed source")
        return StreamingResponse(
            mjpeg_stream(slot, fps=feed_fps),
            media_type="multipart/x-mixed-replace; boundary=" + _MJPEG_BOUNDARY,
        )

    dist = Path(dist_dir) if dist_dir is not None else _DEFAULT_DIST
    if dist.is_dir():
        # html=True serves index.html for "/"; registered last so /api/* match first.
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="spa")
    else:
        _LOG.info(
            "dashboard SPA bundle not found at %s — serving JSON API only "
            "(build it with `npm run build` in output/dashboard/web)",
            dist,
        )

    return app


# --- serving surface (uvicorn, http.server-compatible lifecycle) ------------


class DashboardServer:
    """uvicorn-backed server with an ``http.server``-compatible lifecycle.

    Exposes ``serve_forever()`` / ``shutdown()`` / ``server_close()`` and a
    ``server_address`` so the supervised :class:`~overwatch.app.DashboardStage`
    (#110) drives it exactly as it drove the old stdlib server. The listening
    socket is bound eagerly in ``__init__`` (``port=0`` -> OS-assigned), so
    ``server_address`` is known *before* serving — which host tests rely on.
    """

    def __init__(self, app: "FastAPI", *, host: str, port: int) -> None:
        import uvicorn

        # log_config=None: don't let uvicorn reconfigure the app's logging.
        self._config = uvicorn.Config(
            app, host=host, port=port, log_level="warning", log_config=None, lifespan="off"
        )
        # uvicorn skips signal handlers automatically when serve() runs off the
        # main thread (which is how DashboardStage runs serve_forever) — so we
        # don't install/override them here.
        self._server = uvicorn.Server(self._config)
        self._sock = self._config.bind_socket()
        sockname = self._sock.getsockname()
        self.server_address = (sockname[0], sockname[1])

    def serve_forever(self) -> None:
        """Block serving on the pre-bound socket until :meth:`shutdown` (run on a thread)."""
        self._server.run(sockets=[self._sock])

    def shutdown(self) -> None:
        """Signal the serve loop to exit (uvicorn polls ``should_exit`` ~10x/s)."""
        self._server.should_exit = True

    def server_close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


def make_server(
    store: "EventStore",
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    dist_dir: "Optional[str]" = None,
    feeds: "Optional[Dict[str, FrameSlot]]" = None,
    feed_fps: int = 8,
    now: "Callable[[], float]" = time.time,
    window_seconds: float = 3600.0,
    refresh_seconds: int = 5,
    alert_limit: int = 10,
    event_limit: int = 10,
    liveness_provider: "Optional[Callable[[], Optional[LivenessSnapshot]]]" = None,
) -> "DashboardServer":
    """Build (binding the port, but not yet serving) the read-only dashboard server.

    Pass ``port=0`` to bind an ephemeral port — the chosen ``(host, port)`` is then
    on ``server.server_address`` (used by host tests to serve on localhost). Pass
    ``feeds`` (name -> FrameSlot) to expose the live MJPEG feeds (#120/#132), and
    ``liveness_provider`` to surface the degraded/liveness block (#136).
    """
    app = create_app(
        store,
        dist_dir=dist_dir,
        feeds=feeds,
        feed_fps=feed_fps,
        now=now,
        window_seconds=window_seconds,
        refresh_seconds=refresh_seconds,
        alert_limit=alert_limit,
        event_limit=event_limit,
        liveness_provider=liveness_provider,
    )
    return DashboardServer(app, host=host, port=port)


def serve(cfg: "Any") -> None:  # pragma: no cover - runtime entry (blocking)
    """Open the configured EventStore and serve the dashboard until interrupted.

    Standalone runtime entry. Reads ``output.store.path`` for the durable store and
    ``output.dashboard.*`` for the surface knobs. Builds the **non-pipeline** feeds
    (raw RTSP / mock, #132) so the console can show a live image with no DeepStream —
    handy for host/dev testing; the detection feed needs the supervised pipeline.
    Blocking; stop with Ctrl-C / SIGTERM.
    """
    from overwatch.output.dashboard.feeds import make_aux_feeds
    from overwatch.output.sqlite_store import SqliteEventStore

    out = cfg.output
    dash = out.dashboard
    if out.store.backend != "sqlite" or not out.store.path:
        raise RuntimeError("dashboard requires a sqlite EventStore path (output.store)")
    store = SqliteEventStore(out.store.path)

    rtsp_url, rtsp_cred = dash.feed_rtsp_url, None
    if dash.feed_rtsp_enabled and not rtsp_url:
        for s in cfg.capture.sources:
            if getattr(s, "type", None) == "rtsp":
                rtsp_url, rtsp_cred = s.url, getattr(s, "cred", None)
                break
    feeds, feeders = make_aux_feeds(
        rtsp_enabled=dash.feed_rtsp_enabled,
        rtsp_url=rtsp_url,
        rtsp_cred=rtsp_cred,
        mock_enabled=dash.feed_mock_enabled,
        fps=dash.feed_fps,
    )
    server = make_server(
        store,
        host=dash.host,
        port=dash.port,
        dist_dir=dash.dist_dir,
        feeds=feeds,
        feed_fps=dash.feed_fps,
        window_seconds=dash.window_seconds,
        refresh_seconds=dash.refresh_seconds,
        alert_limit=dash.alert_limit,
        event_limit=dash.event_limit,
    )
    for feeder in feeders:
        feeder.start()
    _LOG.info("operator console serving on http://%s:%s", dash.host, dash.port)
    try:
        server.serve_forever()
    finally:
        for feeder in feeders:
            feeder.stop()
        server.server_close()
        store.close()


__all__ = [
    "create_app",
    "dashboard_summary",
    "state_dict",
    "mjpeg_stream",
    "DashboardServer",
    "make_server",
    "serve",
]
