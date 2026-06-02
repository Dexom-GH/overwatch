"""Slack alert sink.

Delivers ``Alert`` messages to Slack via an incoming webhook. The webhook URL
comes from the ``SLACK_WEBHOOK`` env var (see ``.env.example``) — never hardcode
or commit it. ``AlertSink`` is the generic interface so additional sinks (SMS,
email) can be added without touching the logic layer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from overwatch.bus.schemas import Alert


class AlertSink(ABC):
    """Delivers an alert to some destination."""

    @abstractmethod
    def send(self, alert: Alert) -> None:
        raise NotImplementedError


class SlackAlertSink(AlertSink):
    """Posts alerts to a Slack incoming webhook. Skeleton."""

    def __init__(self, webhook_url: str) -> None:
        # webhook_url should be read from env (SLACK_WEBHOOK), not committed.
        self._webhook_url = webhook_url

    def send(self, alert: Alert) -> None:
        # TODO: format alert (severity -> color/emoji) and POST to the webhook.
        raise NotImplementedError("SlackAlertSink.send")


__all__ = ["AlertSink", "SlackAlertSink"]
