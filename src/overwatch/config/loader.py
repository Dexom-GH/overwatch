"""Layered config loader.

Resolution order (later overrides earlier): packaged defaults -> ``configs/*.yaml``
-> environment variables (e.g. ``SLACK_WEBHOOK``, ``REDIS_URL``; see
``.env.example``). Returns a plain dict in V1; a typed config object can come
later if it earns its keep.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load and merge configuration into a single dict.

    TODO: read configs/default.yaml + configs/animals.yaml, deep-merge an
    optional override file at ``config_path``, then overlay select env vars.
    """
    raise NotImplementedError("load_config")


__all__ = ["load_config"]
