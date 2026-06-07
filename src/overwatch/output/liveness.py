"""Operator-visible pipeline liveness — the degraded/recovered signal (#136).

Today "no alerts" looks identical to "all healthy" and to "camera dead". This
module makes pipeline degradation visible to the operator:

- :class:`LivenessTracker` — a thread-safe, in-process record of per-source
  last-frame time + recent stage restarts. Capture ``mark``s it per published
  frame; the #38 supervisor ``note_restart``s it via its ``on_event`` hook; the
  dashboard and the monitor read its ``snapshot``. **In-process by design** (the
  dashboard runs in the same supervised process), so there is **no bus topic /
  schema change** — liveness never crosses the wire.
- :class:`LivenessMonitor` (see :mod:`overwatch.output.liveness_monitor`) turns a
  source going silent into a throttled Slack alert.

Pure / host-safe (stdlib + injected clock), so the up/down/degraded logic is
unit-tested off-device. The real capture ``mark`` wiring and a live source loss on
the Jetson are the on-device bar (AC6). Python 3.8-compatible.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# How long a recent stage restart keeps the rollup "degraded" (and stays listed).
_DEFAULT_RESTART_WINDOW_S = 300.0
# Bound the retained restart history so a thrashing stage can't grow it unbounded.
_MAX_RESTARTS = 50


@dataclass
class SourceLiveness:
    """Liveness of one capture source at snapshot time."""

    source_id: str
    last_frame_age_s: Optional[float]   # seconds since last frame; None if never seen
    up: bool                            # a frame arrived within the silence window


@dataclass
class LivenessSnapshot:
    """An at-a-glance liveness rollup for the operator surfaces."""

    sources: List[SourceLiveness]
    recent_restarts: List[Dict[str, Any]] = field(default_factory=list)
    degraded: bool = False              # any source down OR a recent stage restart


class LivenessTracker:
    """Per-source last-frame time + recent restarts; derives up/down/degraded.

    ``silence_seconds`` is how long a source may go without a frame before it is
    "down". ``restart_window_seconds`` is how long a stage restart keeps the rollup
    degraded. All times are caller-supplied (monotonic seconds) so the logic is
    deterministic and host-testable; thread-safe because the supervisor runs stages
    on separate threads (#38).
    """

    def __init__(
        self,
        silence_seconds: float,
        *,
        restart_window_seconds: float = _DEFAULT_RESTART_WINDOW_S,
        max_restarts: int = _MAX_RESTARTS,
    ) -> None:
        self._silence = float(silence_seconds)
        self._restart_window = float(restart_window_seconds)
        self._max_restarts = max_restarts
        self._last_seen: "Dict[str, Optional[float]]" = {}  # insertion-ordered
        self._restarts: "List[Dict[str, Any]]" = []  # {"stage": str, "at": float}
        self._lock = threading.Lock()

    def register(self, source_id: str) -> None:
        """Declare a source so it appears (down) before its first frame."""
        with self._lock:
            self._last_seen.setdefault(source_id, None)

    def mark(self, source_id: str, now: float) -> None:
        """Record that ``source_id`` produced a frame at ``now`` (auto-registers)."""
        with self._lock:
            self._last_seen[source_id] = now

    def note_restart(self, stage: str, now: float) -> None:
        """Record that the supervisor restarted ``stage`` at ``now`` (#38, AC3)."""
        with self._lock:
            self._restarts.append({"stage": stage, "at": now})
            if len(self._restarts) > self._max_restarts:
                del self._restarts[: -self._max_restarts]

    def snapshot(self, now: float) -> LivenessSnapshot:
        """Derive the operator-facing liveness rollup at ``now``."""
        with self._lock:
            sources: "List[SourceLiveness]" = []
            for source_id, last in self._last_seen.items():
                age = None if last is None else now - last
                up = age is not None and age <= self._silence
                sources.append(SourceLiveness(source_id=source_id, last_frame_age_s=age, up=up))
            recent = [
                {"stage": r["stage"], "age_s": now - r["at"]}
                for r in self._restarts
                if now - r["at"] <= self._restart_window
            ]
        any_down = any(not s.up for s in sources)
        return LivenessSnapshot(
            sources=sources,
            recent_restarts=recent,
            degraded=any_down or bool(recent),
        )


__all__ = ["LivenessTracker", "LivenessSnapshot", "SourceLiveness"]
