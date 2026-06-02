"""Redis-backed MessageBus — STUB.

One of two parallel transport stubs (the other is ``zeromq_bus.py``). Neither is
privileged until ADR-0001 (Redis vs ZeroMQ) closes. ``redis`` is NOT yet in
requirements — it is added only if/when this transport is chosen.

TODO(ADR-0001): if Redis is chosen, implement pub/sub (or Redis Streams for the
event log), add ``redis`` to requirements.target.txt, and define schema
(de)serialization.
"""

from __future__ import annotations

from typing import Any, Optional

from overwatch.bus.base import Handler, MessageBus


class RedisBus(MessageBus):
    """Pub/sub over Redis. Skeleton only — see module docstring."""

    def __init__(self, url: str = "redis://localhost:6379/0") -> None:
        self._url = url
        self._client: Optional[Any] = None  # set in start() once implemented

    def publish(self, topic: str, message: Any) -> None:
        raise NotImplementedError("RedisBus.publish — pending ADR-0001")

    def subscribe(self, topic: str, handler: Handler) -> None:
        raise NotImplementedError("RedisBus.subscribe — pending ADR-0001")

    def start(self) -> None:
        raise NotImplementedError("RedisBus.start — pending ADR-0001")

    def close(self) -> None:
        raise NotImplementedError("RedisBus.close — pending ADR-0001")


__all__ = ["RedisBus"]
