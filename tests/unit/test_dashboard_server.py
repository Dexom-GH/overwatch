"""Host tests for the thin read-only HTML dashboard surface (#18).

The dashboard-surface decision (2026-06-05) is a **thin local read-only HTML view
with static refresh** — no SPA/JS build, no web framework. These tests cover the
HTML renderer, the pure request app (host-testable without sockets), and a real
localhost serve proving the surface is read-only (GET renders; mutating methods
are rejected). All host-runnable: stdlib http.server + in-memory SQLite.
"""

import http.client
import threading

from overwatch.bus.schemas import Alert, Event, ZoneCount
from overwatch.output.dashboard.server import (
    DashboardApp,
    Response,
    make_server,
    render_html,
)
from overwatch.output.dashboard.view import build_dashboard_state
from overwatch.output.sqlite_store import SqliteEventStore


def _store_with_data():
    store = SqliteEventStore(":memory:")
    store.record(ZoneCount(zone_id="pen-A", timestamp=20.0, count=5))
    store.record(ZoneCount(zone_id="pen-B", timestamp=12.0, count=2))
    store.record(Alert(timestamp=22.0, severity="critical", title="Sheep down", message="immobile 11m"))
    store.record(Event(timestamp=21.0, kind="fence_crossing", track_id=7, zone_id="north-gate"))
    return store


# --- render_html -----------------------------------------------------------


def test_render_html_is_a_document_with_a_table():
    state = build_dashboard_state(_store_with_data(), now=100.0, window_s=1000.0)
    html = render_html(state, refresh_seconds=5)
    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert "<table" in html


def test_render_html_includes_static_refresh_meta_with_configured_interval():
    state = build_dashboard_state(_store_with_data(), now=100.0, window_s=1000.0)
    html = render_html(state, refresh_seconds=7)
    assert '<meta http-equiv="refresh" content="7">' in html


def test_render_html_shows_zone_counts_alerts_and_events():
    state = build_dashboard_state(_store_with_data(), now=100.0, window_s=1000.0)
    html = render_html(state, refresh_seconds=5)
    # zone counts
    assert "pen-A" in html and ">5<" in html
    # alert (severity + title)
    assert "critical" in html and "Sheep down" in html
    # event with type / zone / track_id / timestamp (the AC's columns)
    assert "fence_crossing" in html
    assert "north-gate" in html
    assert ">7<" in html  # track_id


def test_render_html_is_read_only_no_forms():
    state = build_dashboard_state(_store_with_data(), now=100.0, window_s=1000.0)
    html = render_html(state, refresh_seconds=5)
    assert "<form" not in html.lower()
    assert "<input" not in html.lower()


def test_render_html_escapes_untrusted_text():
    store = SqliteEventStore(":memory:")
    store.record(Alert(timestamp=22.0, severity="warning", title="<script>x</script>", message="m"))
    state = build_dashboard_state(store, now=100.0, window_s=1000.0)
    html = render_html(state, refresh_seconds=5)
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;" in html


# --- DashboardApp (pure request logic, no sockets) -------------------------


def test_app_get_root_returns_html_built_from_store():
    app = DashboardApp(_store_with_data(), now=lambda: 100.0, window_seconds=1000.0, refresh_seconds=5)
    resp = app.get("/")
    assert isinstance(resp, Response)
    assert resp.status == 200
    assert resp.content_type.startswith("text/html")
    assert "Sheep down" in resp.body
    assert '<meta http-equiv="refresh" content="5">' in resp.body


def test_app_unknown_path_returns_404():
    app = DashboardApp(_store_with_data(), now=lambda: 100.0)
    resp = app.get("/nope")
    assert resp.status == 404


def test_app_ignores_query_string_on_root():
    app = DashboardApp(_store_with_data(), now=lambda: 100.0, window_seconds=1000.0)
    assert app.get("/?t=1").status == 200


# --- real localhost serve: GET renders, mutating methods are rejected ------


def test_server_serves_get_and_rejects_mutations():
    server = make_server(
        _store_with_data(), host="127.0.0.1", port=0, now=lambda: 100.0,
        window_seconds=1000.0, refresh_seconds=5,
    )
    host, port = server.server_address
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request("GET", "/")
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")
        assert resp.status == 200
        assert resp.getheader("Content-Type", "").startswith("text/html")
        assert "Sheep down" in body
        conn.close()

        # read-only: a mutating method must be refused (405), never processed
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request("POST", "/")
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 405
        conn.close()
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=5)
