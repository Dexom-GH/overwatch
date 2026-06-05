"""Read-only dashboard view-model (#18).

Reads the :class:`~overwatch.output.store.EventStore` and produces the data an
operator view renders: the current per-zone counts and a recent-alerts list, over
a trailing time window. Deliberately **tech-agnostic** — it returns a
:class:`DashboardState` (plain data) plus a simple :func:`render_text`, so the
web-vs-native dashboard decision (its own ADR) stays open. Host-runnable.

Python 3.8-compatible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List

from overwatch.bus.schemas import Alert, Event, ZoneCount

if TYPE_CHECKING:
    from overwatch.output.store import EventStore


@dataclass
class DashboardState:
    """The renderable snapshot: latest count per zone + recent alerts + events."""

    generated_at: float
    zone_counts: List[ZoneCount] = field(default_factory=list)
    recent_alerts: List[Alert] = field(default_factory=list)
    recent_events: List[Event] = field(default_factory=list)


def latest_zone_counts(
    store: "EventStore", *, end: float, start: float = 0.0
) -> "List[ZoneCount]":
    """The most-recent ``ZoneCount`` per zone within ``[start, end]`` (sorted by zone)."""
    latest = {}  # type: dict
    for count in store.query("zone_count", start, end):
        prev = latest.get(count.zone_id)
        if prev is None or count.timestamp >= prev.timestamp:
            latest[count.zone_id] = count
    return [latest[z] for z in sorted(latest)]


def recent_alerts(
    store: "EventStore", *, end: float, start: float = 0.0, limit: int = 10
) -> "List[Alert]":
    """The ``limit`` most recent alerts within ``[start, end]``, newest first."""
    alerts = list(store.query("alert", start, end))
    alerts.sort(key=lambda a: a.timestamp, reverse=True)
    return alerts[:limit]


def recent_events(
    store: "EventStore", *, end: float, start: float = 0.0, limit: int = 10
) -> "List[Event]":
    """The ``limit`` most recent discrete events within ``[start, end]``, newest first.

    These are the logic-layer ``Event`` records (fence-crossing, immobility, ...);
    the dashboard lists them with their type/zone/track_id/timestamp.
    """
    events = list(store.query("event", start, end))
    events.sort(key=lambda e: e.timestamp, reverse=True)
    return events[:limit]


def build_dashboard_state(
    store: "EventStore",
    *,
    now: float,
    window_s: float = 3600.0,
    alert_limit: int = 10,
    event_limit: int = 10,
) -> "DashboardState":
    """Assemble the dashboard snapshot over the trailing ``window_s`` ending at ``now``."""
    start = now - window_s
    return DashboardState(
        generated_at=now,
        zone_counts=latest_zone_counts(store, start=start, end=now),
        recent_alerts=recent_alerts(store, start=start, end=now, limit=alert_limit),
        recent_events=recent_events(store, start=start, end=now, limit=event_limit),
    )


def render_text(state: "DashboardState") -> str:
    """Render a snapshot as plain text (a minimal view; the served UI is deferred)."""
    lines = ["Overwatch — operator dashboard (t={:.0f})".format(state.generated_at)]
    lines.append("Zone counts:")
    if state.zone_counts:
        for count in state.zone_counts:
            lines.append("  {}: {}".format(count.zone_id, count.count))
    else:
        lines.append("  (none)")
    lines.append("Recent alerts:")
    if state.recent_alerts:
        for alert in state.recent_alerts:
            lines.append(
                "  [{}] {}".format(alert.severity, alert.title)
            )
    else:
        lines.append("  (none)")
    return "\n".join(lines)


__all__ = [
    "DashboardState",
    "latest_zone_counts",
    "recent_alerts",
    "recent_events",
    "build_dashboard_state",
    "render_text",
]
