"""Event / time-series store interface.

Persists counts, health signals, events, and alerts for logging and to back the
operator dashboard. Concrete backend is open (could ride on the bus transport
choice — e.g. Redis Streams if ADR-0001 picks Redis, or a dedicated TSDB).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Iterable, Union

from overwatch.bus.schemas import Alert, Event, HealthSignal, ZoneCount

# The record types this store persists (the monitoring outputs worth keeping).
Record = Union[ZoneCount, HealthSignal, Event, Alert]


class EventStore(ABC):
    """Append-and-query store for monitoring records. Skeleton."""

    @abstractmethod
    def record(self, item: Record) -> None:
        """Persist a ZoneCount / HealthSignal / Event / Alert."""
        raise NotImplementedError

    @abstractmethod
    def query(self, kind: str, start: float, end: float) -> Iterable[Any]:
        """Return records of ``kind`` within the time window."""
        raise NotImplementedError

    @abstractmethod
    def prune(self, before: float) -> int:
        """Delete records with ``timestamp < before``; return how many were removed.

        The durable-tier retention hook (#40): the EventStore must bound its own
        growth so 24/7 logging cannot fill the NVMe. Callers derive ``before`` from
        a :class:`~overwatch.output.retention.RetentionPolicy` (``age_cutoff``).
        """
        raise NotImplementedError


__all__ = ["EventStore"]
