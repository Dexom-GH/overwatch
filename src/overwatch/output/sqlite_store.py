"""SQLite EventStore — the durable tier (#18, ADR-0001 hybrid).

Persists monitoring records (``ZoneCount`` / ``HealthSignal`` / ``Event`` /
``Alert``) to a single SQLite table so they survive a process restart and back
the read-only operator dashboard (``output/dashboard/``). SQLite is stdlib, so
the whole store is host-runnable and unit-tested off-device; only verification on
the real Jetson NVMe is target-side.

Each record is stored as ``(kind, timestamp, payload)`` where ``payload`` is the
dataclass rendered to JSON (``dataclasses.asdict``). These four record types are
JSON-safe (no numpy — unlike Frame/DepthFrame, which are not persisted here).
Retention pruning is the :meth:`prune` hook from #40
(:class:`~overwatch.output.retention.RetentionPolicy` supplies the cutoff).

Python 3.8-compatible.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import threading
from typing import Any, Dict, Iterator, Type

from overwatch.bus.schemas import Alert, Event, HealthSignal, ZoneCount
from overwatch.output.store import EventStore, Record

# Stable kind string <-> record type. The kind is also the dashboard/query key.
_KIND_BY_TYPE: "Dict[type, str]" = {
    ZoneCount: "zone_count",
    HealthSignal: "health_signal",
    Event: "event",
    Alert: "alert",
}
_TYPE_BY_KIND: "Dict[str, Type[Any]]" = {v: k for k, v in _KIND_BY_TYPE.items()}


class SqliteEventStore(EventStore):
    """Durable EventStore backed by SQLite.

    Pass a filesystem path for persistence (created if absent) or ``":memory:"``
    for an ephemeral store in tests. Safe to call from multiple threads (one lock;
    ``check_same_thread=False``) — the V1 supervisor runs stages on threads.
    """

    def __init__(self, path: str) -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._conn:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS records ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  kind TEXT NOT NULL,"
                "  timestamp REAL NOT NULL,"
                "  payload TEXT NOT NULL)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_kind_ts ON records (kind, timestamp)"
            )

    def record(self, item: "Record") -> None:
        kind = _KIND_BY_TYPE.get(type(item))
        if kind is None:
            raise TypeError("EventStore cannot persist {}".format(type(item).__name__))
        payload = json.dumps(dataclasses.asdict(item))
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO records (kind, timestamp, payload) VALUES (?, ?, ?)",
                (kind, float(item.timestamp), payload),
            )

    def query(self, kind: str, start: float, end: float) -> "Iterator[Any]":
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM records WHERE kind = ? AND timestamp BETWEEN ? AND ? "
                "ORDER BY timestamp, id",
                (kind, start, end),
            ).fetchall()
        return iter([_reconstruct(kind, json.loads(payload)) for (payload,) in rows])

    def prune(self, before: float) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM records WHERE timestamp < ?", (before,)
            )
            return cur.rowcount

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "SqliteEventStore":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def _reconstruct(kind: str, payload: "Dict[str, Any]") -> Any:
    cls = _TYPE_BY_KIND[kind]
    if cls is Alert and payload.get("source_event") is not None:
        payload = dict(payload, source_event=Event(**payload["source_event"]))
    return cls(**payload)


__all__ = ["SqliteEventStore"]
