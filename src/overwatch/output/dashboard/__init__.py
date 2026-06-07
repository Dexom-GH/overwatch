"""Operator console — SPA + JSON data API over the EventStore (ADR-0008, #124).

The console is a single-page app built in CI and served as a static ``dist/``
bundle (`server.py`); the tech-agnostic ``view`` layer produces the data a view
renders (current zone counts + recent alerts + events) and is what the JSON API
serializes. The dashboard is a *consumer* of stored records and never reaches into
other stages directly.
"""

from overwatch.output.dashboard.view import (
    DashboardState,
    build_dashboard_state,
    render_text,
)

__all__ = ["DashboardState", "build_dashboard_state", "render_text"]
