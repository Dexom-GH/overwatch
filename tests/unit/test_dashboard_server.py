"""Host tests for the operator-console backend (#124, ADR-0008).

ADR-0008 supersedes the #18 read-only HTML surface with an SPA served as a static
``dist/`` bundle plus a JSON data API. These tests cover the backend half — the
JSON API shape, static SPA serving (and API-only fallback when no bundle is
built), and the read-only guarantee (mutating methods are refused). All
host-runnable: FastAPI ``TestClient`` over in-memory SQLite, no real sockets.
"""

from fastapi.testclient import TestClient

from overwatch.bus.schemas import Alert, Event, ZoneCount
from overwatch.output.dashboard.server import (
    create_app,
    dashboard_summary,
    make_server,
    state_dict,
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


# --- state_dict (the SPA data contract) ------------------------------------


def test_state_dict_is_flat_json_serializable_shape():
    state = build_dashboard_state(_store_with_data(), now=100.0, window_s=1000.0)
    payload = state_dict(state, refresh_seconds=7)
    assert payload["refresh_seconds"] == 7
    assert payload["generated_at"] == 100.0
    zones = {z["zone_id"]: z for z in payload["zone_counts"]}
    assert zones["pen-A"]["count"] == 5
    assert set(payload["recent_alerts"][0]) == {"timestamp", "severity", "title", "message"}
    assert set(payload["recent_events"][0]) == {"timestamp", "kind", "track_id", "zone_id"}


# --- dashboard_summary (info-panel rollup, #121) ---------------------------


def test_summary_rolls_up_counts_alerts_and_last_activity():
    state = build_dashboard_state(_store_with_data(), now=100.0, window_s=1000.0)
    summary = dashboard_summary(state)
    assert summary["total_count"] == 7  # pen-A 5 + pen-B 2
    assert summary["zones_reporting"] == 2
    assert summary["recent_alert_count"] == 1
    assert summary["critical_alert_count"] == 1
    assert summary["recent_event_count"] == 1
    assert summary["last_activity_at"] == 22.0  # newest of alert@22 / event@21


def test_summary_is_quiet_when_window_empty():
    summary = dashboard_summary(build_dashboard_state(SqliteEventStore(":memory:"), now=100.0))
    assert summary["total_count"] == 0
    assert summary["zones_reporting"] == 0
    assert summary["recent_alert_count"] == 0
    assert summary["critical_alert_count"] == 0
    assert summary["last_activity_at"] is None


def test_api_state_includes_summary():
    client = TestClient(create_app(_store_with_data(), now=lambda: 100.0, window_seconds=1000.0))
    summary = client.get("/api/state").json()["summary"]
    assert summary["total_count"] == 7
    assert summary["critical_alert_count"] == 1


# --- GET /api/state --------------------------------------------------------


def test_api_state_returns_counts_alerts_events_json():
    app = create_app(_store_with_data(), now=lambda: 100.0, window_seconds=1000.0, refresh_seconds=5)
    client = TestClient(app)
    resp = client.get("/api/state")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    data = resp.json()

    zones = {z["zone_id"]: z for z in data["zone_counts"]}
    assert zones["pen-A"]["count"] == 5 and zones["pen-B"]["count"] == 2

    assert [a["title"] for a in data["recent_alerts"]] == ["Sheep down"]
    assert data["recent_alerts"][0]["severity"] == "critical"

    ev = data["recent_events"][0]
    assert ev["kind"] == "fence_crossing" and ev["track_id"] == 7 and ev["zone_id"] == "north-gate"
    assert data["refresh_seconds"] == 5


def test_api_state_returns_untrusted_text_verbatim_for_client_side_escaping():
    # The backend ships raw text as JSON; the SPA (React) escapes at render time.
    store = SqliteEventStore(":memory:")
    store.record(Alert(timestamp=22.0, severity="warning", title="<script>x</script>", message="m"))
    client = TestClient(create_app(store, now=lambda: 100.0, window_seconds=1000.0))
    data = client.get("/api/state").json()
    assert data["recent_alerts"][0]["title"] == "<script>x</script>"


def test_api_health_ok():
    client = TestClient(create_app(_store_with_data(), now=lambda: 100.0))
    assert client.get("/api/health").json() == {"status": "ok"}


# --- read-only guarantee ---------------------------------------------------


def test_api_state_is_read_only_rejects_mutations():
    client = TestClient(create_app(_store_with_data(), now=lambda: 100.0))
    assert client.post("/api/state").status_code == 405
    assert client.put("/api/state").status_code == 405
    assert client.delete("/api/state").status_code == 405


# --- static SPA serving ----------------------------------------------------


def test_serves_spa_index_from_built_dist(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>Overwatch operator console</title>")
    app = create_app(_store_with_data(), dist_dir=str(dist), now=lambda: 100.0)
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Overwatch operator console" in resp.text


def test_api_works_and_root_404s_when_no_dist_built(tmp_path):
    # No built bundle -> backend still serves the API; "/" has nothing to serve.
    app = create_app(_store_with_data(), dist_dir=str(tmp_path / "missing"), now=lambda: 100.0)
    client = TestClient(app)
    assert client.get("/").status_code == 404
    assert client.get("/api/health").json() == {"status": "ok"}


# --- make_server: binds a real port, http.server-compatible address --------


def test_make_server_binds_port_and_reports_address():
    server = make_server(_store_with_data(), host="127.0.0.1", port=0, now=lambda: 100.0)
    try:
        host, port = server.server_address
        assert host == "127.0.0.1"
        assert isinstance(port, int) and port > 0
    finally:
        server.server_close()
