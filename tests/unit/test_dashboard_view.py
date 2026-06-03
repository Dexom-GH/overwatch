"""Host tests for the read-only operator dashboard view-model (#18).

The dashboard *tech* (web vs native) is an open decision with its own ADR
(see output/dashboard/README.md) — so this layer is deliberately tech-agnostic:
it reads the EventStore and produces the data a view renders (current zone counts
+ recent alerts). Host-runnable; the served UI is deferred until the ADR closes.
"""

from overwatch.bus.schemas import Alert, ZoneCount
from overwatch.output.dashboard.view import (
    DashboardState,
    build_dashboard_state,
    latest_zone_counts,
    recent_alerts,
    render_text,
)
from overwatch.output.sqlite_store import SqliteEventStore


def _store_with_data():
    store = SqliteEventStore(":memory:")
    # pen-A counted twice; the later count should win.
    store.record(ZoneCount(zone_id="pen-A", timestamp=10.0, count=3))
    store.record(ZoneCount(zone_id="pen-A", timestamp=20.0, count=5))
    store.record(ZoneCount(zone_id="pen-B", timestamp=12.0, count=2))
    for ts, title in [(11.0, "old"), (21.0, "newer"), (22.0, "newest")]:
        store.record(Alert(timestamp=ts, severity="warning", title=title, message="m"))
    return store


def test_latest_zone_counts_keeps_most_recent_per_zone():
    counts = latest_zone_counts(_store_with_data(), end=100.0)
    by_zone = {c.zone_id: c.count for c in counts}
    assert by_zone == {"pen-A": 5, "pen-B": 2}  # pen-A's later count (5) wins


def test_recent_alerts_newest_first_and_limited():
    alerts = recent_alerts(_store_with_data(), end=100.0, limit=2)
    assert [a.title for a in alerts] == ["newest", "newer"]


def test_build_dashboard_state_combines_counts_and_alerts():
    state = build_dashboard_state(_store_with_data(), now=100.0, window_s=1000.0, alert_limit=5)
    assert isinstance(state, DashboardState)
    assert {c.zone_id for c in state.zone_counts} == {"pen-A", "pen-B"}
    assert state.recent_alerts[0].title == "newest"
    assert state.generated_at == 100.0


def test_window_excludes_old_records():
    # now=100, window=10 -> only records with ts >= 90 are considered.
    state = build_dashboard_state(_store_with_data(), now=100.0, window_s=10.0)
    assert state.zone_counts == []
    assert state.recent_alerts == []


def test_render_text_shows_counts_and_alert_titles():
    state = build_dashboard_state(_store_with_data(), now=100.0, window_s=1000.0)
    text = render_text(state)
    assert "pen-A" in text and "5" in text
    assert "newest" in text
