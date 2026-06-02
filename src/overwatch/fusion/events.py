"""Discrete events -> alerts (rule engine).

Turns fused observations into discrete ``Event`` messages (e.g. fence-crossing:
a track leaving a defined zone / crossing a boundary) and escalates the ones
that warrant an ``Alert``. Rules and zones come from ``configs/default.yaml``.
"""

from __future__ import annotations

from typing import List, Optional

from overwatch.bus.schemas import Alert, DepthBBox, Event


class EventDetector:
    """Applies rules to fused observations, producing events/alerts. Skeleton."""

    def __init__(self, rules: "object" = None) -> None:
        # TODO: load fence/zone boundaries and alert rules from config.
        self._rules = rules

    def detect_fence_crossing(
        self, timestamp: float, objects: List[DepthBBox]
    ) -> List[Event]:
        """TODO: detect tracks crossing a boundary / leaving their zone."""
        raise NotImplementedError("EventDetector.detect_fence_crossing")

    def to_alert(self, event: Event) -> Optional[Alert]:
        """TODO: map an event to an Alert (severity/title/message) if it warrants one."""
        raise NotImplementedError("EventDetector.to_alert")


__all__ = ["EventDetector"]
