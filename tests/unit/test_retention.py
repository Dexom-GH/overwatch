"""Host tests for the data-retention / storage-growth policy (#40).

24/7 logging + recordings + saved crops grow unbounded; on a 512 GB NVMe that
fills and takes the device down. This covers the host-testable logic: a
size/age/count rotation policy for files (recordings, crops) and the EventStore
pruning contract. Real on-device sizing is target-only (see docs/STORAGE.md).
"""

import os

import pytest

from overwatch.bus.schemas import Alert, ZoneCount
from overwatch.config.schema import RetentionConfig
from overwatch.output.retention import (
    RetentionPolicy,
    enforce_directory,
    enforce_event_store,
)
from overwatch.output.sqlite_store import SqliteEventStore
from overwatch.output.store import EventStore


def _entries(*specs):
    # specs: (id, size_bytes, mtime)
    return list(specs)


class TestSelectForDeletion:
    def test_deletes_files_older_than_max_age(self):
        p = RetentionPolicy(max_age_seconds=100.0)
        now = 1000.0
        # c is older than 100s; a/b are fresh.
        victims = p.select_for_deletion(
            _entries(("a", 10, 950.0), ("b", 10, 990.0), ("c", 10, 800.0)), now=now
        )
        assert victims == ["c"]

    def test_enforces_count_budget_oldest_first(self):
        p = RetentionPolicy(max_count=2)
        victims = p.select_for_deletion(
            _entries(("old", 1, 1.0), ("mid", 1, 2.0), ("new", 1, 3.0)), now=10.0
        )
        assert victims == ["old"]  # keep the 2 newest

    def test_enforces_byte_budget_oldest_first(self):
        p = RetentionPolicy(max_total_bytes=100)
        victims = p.select_for_deletion(
            _entries(("o", 60, 1.0), ("m", 60, 2.0), ("n", 60, 3.0)), now=10.0
        )
        # total 180 > 100 -> drop oldest until <=100: drop o (120>100), drop m (60<=100)
        assert victims == ["o", "m"]

    def test_nothing_deleted_when_within_budget(self):
        p = RetentionPolicy(max_age_seconds=1000.0, max_total_bytes=1000, max_count=10)
        victims = p.select_for_deletion(
            _entries(("a", 10, 5.0), ("b", 10, 6.0)), now=10.0
        )
        assert victims == []

    def test_age_and_size_combine(self):
        p = RetentionPolicy(max_age_seconds=100.0, max_total_bytes=50)
        # d is age-expired; of the rest (40+40=80 > 50) drop oldest until <=50.
        victims = p.select_for_deletion(
            _entries(("d", 10, 800.0), ("a", 40, 950.0), ("b", 40, 990.0)), now=1000.0
        )
        assert victims == ["d", "a"]

    def test_age_cutoff(self):
        assert RetentionPolicy(max_age_seconds=100.0).age_cutoff(1000.0) == 900.0
        assert RetentionPolicy().age_cutoff(1000.0) is None


class TestEnforceDirectory:
    def test_deletes_oldest_over_byte_budget(self, tmp_path):
        paths = []
        for i, name in enumerate(["old.owrec", "mid.owrec", "new.owrec"]):
            f = tmp_path / name
            f.write_bytes(b"x" * 60)
            os.utime(f, (1000 + i, 1000 + i))  # ascending mtime
            paths.append(f)
        deleted = enforce_directory(
            tmp_path, RetentionPolicy(max_total_bytes=100), now=2000.0, glob="*.owrec"
        )
        names_left = sorted(p.name for p in tmp_path.iterdir())
        assert names_left == ["new.owrec"]
        assert sorted(os.path.basename(d) for d in deleted) == ["mid.owrec", "old.owrec"]

    def test_respects_glob(self, tmp_path):
        keep = tmp_path / "keep.txt"
        keep.write_bytes(b"x" * 999)
        rec = tmp_path / "a.owrec"
        rec.write_bytes(b"x" * 999)
        enforce_directory(
            tmp_path, RetentionPolicy(max_total_bytes=0), now=1.0, glob="*.owrec"
        )
        assert keep.exists()        # not matched by the glob
        assert not rec.exists()     # matched + over budget


class _MemStore(EventStore):
    """In-memory reference store to exercise the EventStore.prune contract."""

    def __init__(self):
        self.items = []

    def record(self, item):
        self.items.append(item)

    def query(self, kind, start, end):
        return [i for i in self.items if start <= i.timestamp <= end]

    def prune(self, before):
        keep = [i for i in self.items if i.timestamp >= before]
        removed = len(self.items) - len(keep)
        self.items = keep
        return removed


class TestEventStorePruneContract:
    def test_prune_removes_records_before_cutoff_and_returns_count(self):
        store = _MemStore()
        for ts in (10.0, 20.0, 30.0):
            store.record(ZoneCount(zone_id="z", timestamp=ts, count=1))
        removed = store.prune(before=25.0)
        assert removed == 2
        assert [i.timestamp for i in store.items] == [30.0]

    def test_prune_is_abstract_on_the_base(self):
        # A store missing prune cannot be instantiated (contract is mandatory).
        class _Incomplete(EventStore):
            def record(self, item):
                ...

            def query(self, kind, start, end):
                return []

        with pytest.raises(TypeError):
            _Incomplete()


class TestFromConfig:
    """RetentionPolicy.from_config maps output.store.retention -> a policy (#40)."""

    def test_maps_age_days_and_rows(self):
        p = RetentionPolicy.from_config(RetentionConfig(max_age_days=90, max_rows=1000))
        assert p.max_age_seconds == 90 * 86400
        assert p.max_count == 1000

    def test_unset_bounds_are_none(self):
        p = RetentionPolicy.from_config(RetentionConfig(max_age_days=None, max_rows=None))
        assert p.max_age_seconds is None
        assert p.max_count is None


class TestPruneToMaxRows:
    """SqliteEventStore enforces a global row-count budget, newest-first (#40)."""

    def test_keeps_newest_n_rows(self):
        store = SqliteEventStore(":memory:")
        for ts in (10.0, 20.0, 30.0, 40.0):
            store.record(ZoneCount(zone_id="z", timestamp=ts, count=1))
        removed = store.prune_to_max_rows(2)
        assert removed == 2
        kept = sorted(c.timestamp for c in store.query("zone_count", 0.0, 100.0))
        assert kept == [30.0, 40.0]  # the 2 newest survive

    def test_noop_when_within_budget(self):
        store = SqliteEventStore(":memory:")
        store.record(ZoneCount(zone_id="z", timestamp=10.0, count=1))
        assert store.prune_to_max_rows(5) == 0

    def test_base_default_is_zero(self):
        # The row-cap is an optional backend capability; the ABC default is a no-op.
        assert _MemStore().prune_to_max_rows(1) == 0


class TestEnforceEventStore:
    """enforce_event_store applies a policy's age + row-count bounds to a store (#40)."""

    def _seed(self):
        store = SqliteEventStore(":memory:")
        for ts in (10.0, 20.0, 30.0, 40.0):
            store.record(Alert(timestamp=ts, severity="info", title="t", message="m"))
        return store

    def test_prunes_by_age(self):
        store = self._seed()
        # now=100, max_age=75s -> cutoff 25 -> drop ts 10 & 20.
        removed = enforce_event_store(store, RetentionPolicy(max_age_seconds=75.0), now=100.0)
        assert removed == 2
        assert sorted(a.timestamp for a in store.query("alert", 0.0, 100.0)) == [30.0, 40.0]

    def test_prunes_by_row_cap(self):
        store = self._seed()
        removed = enforce_event_store(store, RetentionPolicy(max_count=1), now=100.0)
        assert removed == 3
        assert [a.timestamp for a in store.query("alert", 0.0, 100.0)] == [40.0]

    def test_age_and_rows_combine(self):
        store = self._seed()
        # age drops 10 (cutoff 15); row cap 2 then drops oldest survivor (20).
        removed = enforce_event_store(
            store, RetentionPolicy(max_age_seconds=85.0, max_count=2), now=100.0
        )
        assert sorted(a.timestamp for a in store.query("alert", 0.0, 100.0)) == [30.0, 40.0]
        assert removed == 2
