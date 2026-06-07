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
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

from overwatch.output.dashboard.view import build_dashboard_state

if TYPE_CHECKING:
    from fastapi import FastAPI

    from overwatch.bus.schemas import Alert, Event, ZoneCount
    from overwatch.output.dashboard.view import DashboardState
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


def state_dict(state: "DashboardState", *, refresh_seconds: int = 5) -> "Dict[str, Any]":
    """Serialize a :class:`DashboardState` to the JSON shape the SPA consumes.

    Explicit, flat dicts (not raw dataclasses) so the client contract is stable and
    nothing heavy (numpy, nested ``detail``) leaks across the wire.
    """
    return {
        "generated_at": state.generated_at,
        "refresh_seconds": refresh_seconds,
        "summary": dashboard_summary(state),
        "zone_counts": [_zone_count_dict(c) for c in state.zone_counts],
        "recent_alerts": [_alert_dict(a) for a in state.recent_alerts],
        "recent_events": [_event_dict(e) for e in state.recent_events],
    }


# --- FastAPI app -----------------------------------------------------------


def create_app(
    store: "EventStore",
    *,
    dist_dir: "Optional[str]" = None,
    now: "Callable[[], float]" = time.time,
    window_seconds: float = 3600.0,
    refresh_seconds: int = 5,
    alert_limit: int = 10,
    event_limit: int = 10,
) -> "FastAPI":
    """Build the read-only dashboard FastAPI app (host-testable without a socket).

    ``GET /api/state`` rebuilds the snapshot from the EventStore at request time
    (so a polling client always sees current data). If a built SPA ``dist/`` exists
    it is mounted at ``/`` (the API routes are registered first, so they win); when
    it is absent the backend still serves the JSON API. ``now`` is injectable so the
    trailing window is deterministic in tests.
    """
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles

    app = FastAPI(title="Overwatch operator console", docs_url=None, redoc_url=None)

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
        return state_dict(state, refresh_seconds=refresh_seconds)

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
    now: "Callable[[], float]" = time.time,
    window_seconds: float = 3600.0,
    refresh_seconds: int = 5,
    alert_limit: int = 10,
    event_limit: int = 10,
) -> "DashboardServer":
    """Build (binding the port, but not yet serving) the read-only dashboard server.

    Pass ``port=0`` to bind an ephemeral port — the chosen ``(host, port)`` is then
    on ``server.server_address`` (used by host tests to serve on localhost).
    """
    app = create_app(
        store,
        dist_dir=dist_dir,
        now=now,
        window_seconds=window_seconds,
        refresh_seconds=refresh_seconds,
        alert_limit=alert_limit,
        event_limit=event_limit,
    )
    return DashboardServer(app, host=host, port=port)


def serve(cfg: "Any") -> None:  # pragma: no cover - runtime entry (blocking)
    """Open the configured EventStore and serve the dashboard until interrupted.

    Standalone runtime entry. Reads ``output.store.path`` for the durable store and
    ``output.dashboard.*`` for the surface knobs (host/port/window/dist). Blocking;
    stop with Ctrl-C / SIGTERM.
    """
    from overwatch.output.sqlite_store import SqliteEventStore

    out = cfg.output
    dash = out.dashboard
    if out.store.backend != "sqlite" or not out.store.path:
        raise RuntimeError("dashboard requires a sqlite EventStore path (output.store)")
    store = SqliteEventStore(out.store.path)
    server = make_server(
        store,
        host=dash.host,
        port=dash.port,
        dist_dir=dash.dist_dir,
        window_seconds=dash.window_seconds,
        refresh_seconds=dash.refresh_seconds,
        alert_limit=dash.alert_limit,
        event_limit=dash.event_limit,
    )
    _LOG.info("operator console serving on http://%s:%s", dash.host, dash.port)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        store.close()


__all__ = [
    "create_app",
    "dashboard_summary",
    "state_dict",
    "DashboardServer",
    "make_server",
    "serve",
]
