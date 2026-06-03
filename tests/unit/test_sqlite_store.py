"""Host tests for the SQLite EventStore durable tier (#18, ADR-0001).

The EventStore persists monitoring records (ZoneCount / HealthSignal / Event /
Alert) so they survive a process restart and back the operator dashboard. SQLite
is stdlib, so the whole store is host-runnable and tested here; only the
on-device persistence path (real NVMe) is target-deferred.
"""

from overwatch.bus.schemas import Alert, Event, HealthSignal, ZoneCount
from overwatch.output.sqlite_store import SqliteEventStore


def _zc(zone, ts, n):
    return ZoneCount(zone_id=zone, timestamp=ts, count=n)


def test_record_and_query_roundtrip_by_kind_and_window():
    store = SqliteEventStore(":memory:")
    store.record(_zc("pen-A", 10.0, 3))
    store.record(_zc("pen-A", 20.0, 5))
    store.record(HealthSignal(track_id=1, timestamp=15.0, kind="immobility", score=0.8))

    counts = list(store.query("zone_count", 0.0, 100.0))
    assert [c.count for c in counts] == [3, 5]
    assert all(isinstance(c, ZoneCount) for c in counts)
    # window + kind filtering
    assert [c.count for c in store.query("zone_count", 0.0, 15.0)] == [3]
    assert [h.kind for h in store.query("health_signal", 0.0, 100.0)] == ["immobility"]


def test_query_returns_empty_for_absent_kind_or_window():
    store = SqliteEventStore(":memory:")
    store.record(_zc("z", 5.0, 1))
    assert list(store.query("alert", 0.0, 100.0)) == []
    assert list(store.query("zone_count", 100.0, 200.0)) == []


def test_alert_with_nested_source_event_roundtrips():
    store = SqliteEventStore(":memory:")
    ev = Event(timestamp=9.0, kind="fence_crossing", track_id=7, zone_id="pen-A")
    store.record(Alert(timestamp=9.0, severity="critical", title="t", message="m",
                       source_event=ev, detail={"k": "v"}))
    [a] = list(store.query("alert", 0.0, 100.0))
    assert isinstance(a, Alert)
    assert a.severity == "critical"
    assert a.detail == {"k": "v"}
    assert isinstance(a.source_event, Event)
    assert a.source_event.kind == "fence_crossing" and a.source_event.track_id == 7


def test_records_survive_a_reopen(tmp_path):
    db = str(tmp_path / "ow.db")
    store = SqliteEventStore(db)
    store.record(_zc("pen-A", 1.0, 4))
    store.close()
    # A fresh instance on the same path sees the persisted record (restart survival).
    reopened = SqliteEventStore(db)
    assert [c.count for c in reopened.query("zone_count", 0.0, 10.0)] == [4]


def test_prune_deletes_old_records_and_returns_count():
    store = SqliteEventStore(":memory:")
    for ts in (10.0, 20.0, 30.0):
        store.record(_zc("z", ts, 1))
    removed = store.prune(before=25.0)
    assert removed == 2
    assert [c.timestamp for c in store.query("zone_count", 0.0, 100.0)] == [30.0]


def test_retention_policy_drives_store_prune():
    # The #40 RetentionPolicy supplies the cutoff; the #18 store enforces it.
    from overwatch.output.retention import RetentionPolicy

    store = SqliteEventStore(":memory:")
    now = 1000.0
    store.record(_zc("z", now - 100.0, 1))  # older than the 60s budget -> pruned
    store.record(_zc("z", now - 10.0, 1))   # fresh -> kept
    policy = RetentionPolicy(max_age_seconds=60.0)
    removed = store.prune(before=policy.age_cutoff(now))
    assert removed == 1
    assert [c.count for c in store.query("zone_count", 0.0, now)] == [1]
