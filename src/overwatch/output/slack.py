"""Slack alert sink.

Delivers ``Alert`` messages to Slack via an incoming webhook. The webhook URL
comes from the ``SLACK_WEBHOOK`` env var (see ``.env.example``) — never hardcode
or commit it. ``AlertSink`` is the generic interface so additional sinks (SMS,
email) can be added without touching the logic layer.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Optional, Tuple

from overwatch.bus.schemas import Alert
from overwatch.output.throttle import AlertThrottle

_LOG = logging.getLogger(__name__)

# severity -> (attachment color, emoji) for the Slack message.
_SEVERITY_STYLE: "Dict[str, Tuple[str, str]]" = {
    "info": ("#36a64f", ":information_source:"),
    "warning": ("#daa038", ":warning:"),
    "critical": ("#a30200", ":rotating_light:"),
}
_FALLBACK_STYLE = ("#cccccc", ":bell:")  # unknown severity still delivers


class AlertSink(ABC):
    """Delivers an alert to some destination."""

    @abstractmethod
    def send(self, alert: Alert) -> None:
        raise NotImplementedError


class ThrottledAlertSink(AlertSink):
    """Wraps a delegate :class:`AlertSink` with de-dup / rate-limit (#42).

    Forwards an alert to ``delegate`` only when the shared
    :class:`~overwatch.output.throttle.AlertThrottle` allows it, so the counting /
    health / fence slices (#16 / #19 / #20 / #33) get "one crossing != an alert
    storm" without each re-implementing de-dup. Suppressed alerts are dropped.
    """

    def __init__(self, delegate: "AlertSink", throttle: "AlertThrottle") -> None:
        self._delegate = delegate
        self._throttle = throttle

    def send(self, alert: Alert) -> None:
        if self._throttle.allow(alert):
            self._delegate.send(alert)


class SlackAlertSink(AlertSink):
    """Posts alerts to a Slack incoming webhook.

    ``webhook_url`` comes from the ``SLACK_WEBHOOK`` env var (resolved by the
    config loader into ``output.slack.webhook``), never committed. The HTTP POST
    is injected via ``poster`` so formatting + delivery are host-testable without
    the network; the default poster uses stdlib ``urllib`` (no extra dependency).
    """

    def __init__(
        self,
        webhook_url: str,
        *,
        poster: "Optional[Callable[[str, bytes], None]]" = None,
        timeout: float = 5.0,
    ) -> None:
        self._webhook_url = webhook_url
        self._timeout = timeout
        self._post = poster if poster is not None else self._default_post

    def send(self, alert: Alert) -> None:
        payload = json.dumps(self._format(alert)).encode("utf-8")
        self._post(self._webhook_url, payload)

    @staticmethod
    def _format(alert: Alert) -> "Dict[str, Any]":
        color, emoji = _SEVERITY_STYLE.get(alert.severity, _FALLBACK_STYLE)
        when = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(alert.timestamp))
        return {
            "text": "{} *{}*\n{}".format(emoji, alert.title, alert.message),
            "attachments": [
                {
                    "color": color,
                    "fallback": "{}: {}".format(alert.title, alert.message),
                    "fields": [
                        {"title": "When", "value": when, "short": True},
                        {"title": "Severity", "value": alert.severity, "short": True},
                    ],
                }
            ],
        }

    def _default_post(self, url: str, payload: bytes) -> None:  # pragma: no cover - network I/O
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            status = getattr(resp, "status", resp.getcode())
            if status >= 300:
                raise RuntimeError("Slack webhook returned HTTP {}".format(status))


__all__ = ["AlertSink", "SlackAlertSink", "ThrottledAlertSink"]
