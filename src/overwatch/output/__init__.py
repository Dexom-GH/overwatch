"""Output stage — alert sinks, event store, operator dashboard.

Subscribes to ``topics.OUTPUT_ALERT`` (and others) and delivers: real-time Slack
alerts (``slack.py``), persistent time-series/event storage (``store.py``), and
the on-site operator dashboard (``dashboard/`` — interface stub in V1).
"""

from overwatch.output.slack import AlertSink, SlackAlertSink
from overwatch.output.store import EventStore

__all__ = ["AlertSink", "SlackAlertSink", "EventStore"]
