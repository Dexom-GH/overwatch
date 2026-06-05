"""Thin read-only HTML operator dashboard (#18).

Resolves the dashboard-surface decision (2026-06-05): a **thin, local, read-only
HTML view with static refresh** — no SPA/JS build, no web framework. It renders a
single self-contained page from the :mod:`~overwatch.output.dashboard.view`
view-model (current per-zone counts + recent alerts + recent events) and serves it
over stdlib :mod:`http.server`; the browser re-fetches on a
``<meta http-equiv="refresh">`` interval (the "static refresh").

**Read-only by construction.** Only ``GET`` is served; any mutating method
(``POST``/``PUT``/``DELETE``/``PATCH``) is refused with ``405`` and never reaches
the store. The dashboard is a *consumer* of stored records — it never writes.

Host vs target: stdlib + SQLite, so the renderer, the pure request app
(:class:`DashboardApp`) and the serving surface are all host-runnable and
unit-tested. The on-device leg (DoD) is rendering against the store produced by a
live #84 RTSP->Slack run on the Jetson.

Python 3.8-compatible.
"""

from __future__ import annotations

import html
import http.server
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, List, Type

from overwatch.output.dashboard.view import DashboardState, build_dashboard_state

if TYPE_CHECKING:
    from overwatch.output.store import EventStore

_LOG = logging.getLogger(__name__)

_CONTENT_TYPE_HTML = "text/html; charset=utf-8"
_CONTENT_TYPE_TEXT = "text/plain; charset=utf-8"


@dataclass
class Response:
    """A rendered HTTP response, decoupled from the socket layer for testability."""

    status: int
    content_type: str
    body: str


# --- rendering -------------------------------------------------------------


def _fmt_ts(ts: float) -> str:
    """Render an epoch-seconds timestamp as a readable UTC clock time."""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ts))


def _esc(value: "object") -> str:
    """HTML-escape any value (alert/event text is operator-facing, treat as untrusted)."""
    return html.escape(str(value), quote=True)


def _rows(rows: "List[List[str]]") -> str:
    return "".join(
        "<tr>" + "".join("<td>" + c + "</td>" for c in r) + "</tr>" for r in rows
    )


def render_html(state: "DashboardState", *, refresh_seconds: int = 5) -> str:
    """Render the dashboard snapshot as a self-contained, auto-refreshing HTML page.

    No external assets, no JavaScript: a ``<meta http-equiv="refresh">`` drives the
    static refresh, and minimal inline CSS keeps it legible on an on-site screen.
    All dynamic text is HTML-escaped.
    """
    count_rows = _rows(
        [[_esc(c.zone_id), _esc(c.count), _esc(c.class_name or "all")] for c in state.zone_counts]
    ) or '<tr><td colspan="3">(none)</td></tr>'

    alert_rows = _rows(
        [
            [_fmt_ts(a.timestamp), _esc(a.severity), _esc(a.title), _esc(a.message)]
            for a in state.recent_alerts
        ]
    ) or '<tr><td colspan="4">(none)</td></tr>'

    # The AC's columns for the events list: type / track / zone / timestamp.
    event_rows = _rows(
        [
            [
                _fmt_ts(e.timestamp),
                _esc(e.kind),
                _esc("-" if e.track_id is None else e.track_id),
                _esc("-" if e.zone_id is None else e.zone_id),
            ]
            for e in state.recent_events
        ]
    ) or '<tr><td colspan="4">(none)</td></tr>'

    return _PAGE.format(
        refresh=int(refresh_seconds),
        generated=_fmt_ts(state.generated_at),
        count_rows=count_rows,
        alert_rows=alert_rows,
        event_rows=event_rows,
    )


_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="{refresh}">
<title>Overwatch — operator dashboard</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 1.5rem; color: #1a1a1a; }}
  h1 {{ font-size: 1.2rem; }}
  h2 {{ font-size: 1rem; margin-top: 1.5rem; }}
  table {{ border-collapse: collapse; width: 100%; max-width: 60rem; }}
  th, td {{ border: 1px solid #ccc; padding: 0.3rem 0.6rem; text-align: left; }}
  th {{ background: #f0f0f0; }}
  .meta {{ color: #666; font-size: 0.85rem; }}
</style>
</head>
<body>
<h1>Overwatch — operator dashboard</h1>
<p class="meta">generated {generated} UTC · auto-refresh {refresh}s · read-only</p>
<h2>Zone counts</h2>
<table><thead><tr><th>zone</th><th>count</th><th>class</th></tr></thead>
<tbody>{count_rows}</tbody></table>
<h2>Recent alerts</h2>
<table><thead><tr><th>time</th><th>severity</th><th>title</th><th>message</th></tr></thead>
<tbody>{alert_rows}</tbody></table>
<h2>Recent events</h2>
<table><thead><tr><th>time</th><th>type</th><th>track</th><th>zone</th></tr></thead>
<tbody>{event_rows}</tbody></table>
</body>
</html>
"""


# --- request app (pure; no sockets) ----------------------------------------


class DashboardApp:
    """Pure request logic for the read-only dashboard — host-testable without sockets.

    On each ``GET /`` it rebuilds the snapshot from the EventStore at request time
    (so a static browser refresh always shows current data) and renders it. ``now``
    is injectable so the window is deterministic in tests; in production it defaults
    to wall-clock :func:`time.time`.
    """

    def __init__(
        self,
        store: "EventStore",
        *,
        now: "Callable[[], float]" = time.time,
        window_seconds: float = 3600.0,
        refresh_seconds: int = 5,
        alert_limit: int = 10,
        event_limit: int = 10,
    ) -> None:
        self._store = store
        self._now = now
        self._window = window_seconds
        self._refresh = refresh_seconds
        self._alert_limit = alert_limit
        self._event_limit = event_limit

    def get(self, path: str) -> "Response":
        """Handle a GET. ``/`` (any query string) renders the page; anything else 404s."""
        if path.split("?", 1)[0] not in ("/", "/index.html"):
            return Response(404, _CONTENT_TYPE_TEXT, "not found")
        state = build_dashboard_state(
            self._store,
            now=self._now(),
            window_s=self._window,
            alert_limit=self._alert_limit,
            event_limit=self._event_limit,
        )
        return Response(200, _CONTENT_TYPE_HTML, render_html(state, refresh_seconds=self._refresh))


# --- serving surface (stdlib http.server) ----------------------------------


def _make_handler(app: "DashboardApp") -> "Type[http.server.BaseHTTPRequestHandler]":
    class _Handler(http.server.BaseHTTPRequestHandler):
        server_version = "OverwatchDashboard/1.0"

        def do_GET(self) -> None:  # noqa: N802 - http.server dispatch name
            self._respond(app.get(self.path))

        def _reject(self) -> None:
            # Read-only: never process a mutating request against the store.
            self._respond(Response(405, _CONTENT_TYPE_TEXT, "method not allowed (read-only)"))

        do_POST = _reject  # noqa: N815
        do_PUT = _reject  # noqa: N815
        do_DELETE = _reject  # noqa: N815
        do_PATCH = _reject  # noqa: N815

        def _respond(self, resp: "Response") -> None:
            body = resp.body.encode("utf-8")
            self.send_response(resp.status)
            self.send_header("Content-Type", resp.content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: "object") -> None:
            # Route access logging through the module logger, not stderr.
            _LOG.debug("dashboard %s - %s", self.address_string(), fmt % args)

    return _Handler


def make_server(
    store: "EventStore",
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    **app_kwargs: "object",
) -> "http.server.HTTPServer":
    """Build (but don't start) the read-only dashboard HTTP server.

    Pass ``port=0`` to bind an ephemeral port (the chosen port is then on
    ``server.server_address``) — used by host tests to serve on localhost.
    """
    app = DashboardApp(store, **app_kwargs)  # type: ignore[arg-type]
    return http.server.HTTPServer((host, port), _make_handler(app))


def serve(cfg: "object") -> None:  # pragma: no cover - runtime entry (blocking)
    """Open the configured EventStore and serve the dashboard until interrupted.

    Runtime entry for the on-device dashboard. Reads ``output.store.path`` for the
    durable store and ``output.dashboard.*`` for the surface knobs (host/port/refresh
    window). Blocking; stop with Ctrl-C / SIGTERM.
    """
    from overwatch.output.sqlite_store import SqliteEventStore

    out = cfg.output  # type: ignore[attr-defined]
    dash = out.dashboard
    if out.store.backend != "sqlite" or not out.store.path:
        raise RuntimeError("dashboard requires a sqlite EventStore path (output.store)")
    store = SqliteEventStore(out.store.path)
    server = make_server(
        store,
        host=dash.host,
        port=dash.port,
        window_seconds=dash.window_seconds,
        refresh_seconds=dash.refresh_seconds,
        alert_limit=dash.alert_limit,
        event_limit=dash.event_limit,
    )
    _LOG.info("operator dashboard serving on http://%s:%s (read-only)", dash.host, dash.port)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        store.close()


__all__ = ["Response", "render_html", "DashboardApp", "make_server", "serve"]
