"""Fusion / logic stage.

Where ZED depth becomes a first-class signal (ADR-0002 hybrid) and where the
animal-monitoring logic lives: depth fusion, depth-deduplicated zone counts,
health (immobility, lameness), and discrete events (fence-crossing). All
host-runnable plain Python — this stage is the most unit-testable in the repo.
"""

from overwatch.fusion.depth_fusion import DepthFusion
from overwatch.fusion.zone_counting import ZoneCounter
from overwatch.fusion.health import HealthMonitor
from overwatch.fusion.events import EventDetector

__all__ = ["DepthFusion", "ZoneCounter", "HealthMonitor", "EventDetector"]
