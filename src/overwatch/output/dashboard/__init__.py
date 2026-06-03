"""Operator dashboard — read-only view over the EventStore (#18).

The dashboard *tech* (web app vs native on-device UI) is an OPEN decision with its
own ADR (see README.md) — so V1 ships the tech-agnostic ``view`` layer (the data a
view renders: current zone counts + recent alerts), not a served UI. Whatever tech
is chosen later reads from ``view.build_dashboard_state`` and never reaches into
other stages directly.
"""

from overwatch.output.dashboard.view import (
    DashboardState,
    build_dashboard_state,
    render_text,
)

__all__ = ["DashboardState", "build_dashboard_state", "render_text"]
