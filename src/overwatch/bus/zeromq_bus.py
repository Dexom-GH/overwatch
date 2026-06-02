"""ZeroMQ-backed MessageBus — STUB.

One of two parallel transport stubs (the other is ``redis_bus.py``). Neither is
privileged until ADR-0001 (Redis vs ZeroMQ) closes. ``pyzmq`` is NOT yet in
requirements — it is added only if/when this transport is chosen.

TODO(ADR-0001): if ZeroMQ is chosen, implement a PUB/SUB topology, add ``pyzmq``
to requirements.target.txt, and define schema (de)serialization. Note ZeroMQ has
no built-in persistence — the event store would be a separate component.
"""

from __future__ import annotations

from typing import Any, Optional

from overwatch.bus.base import Handler, MessageBus


class ZeroMqBus(MessageBus):
    """PUB/SUB over ZeroMQ. Skeleton only — see module docstring."""

    def __init__(self, pub_addr: str = "tcp://127.0.0.1:5556") -> None:
        self._pub_addr = pub_addr
        self._ctx: Optional[Any] = None  # set in start() once implemented

    def publish(self, topic: str, message: Any) -> None:
        raise NotImplementedError("ZeroMqBus.publish — pending ADR-0001")

    def subscribe(self, topic: str, handler: Handler) -> None:
        raise NotImplementedError("ZeroMqBus.subscribe — pending ADR-0001")

    def start(self) -> None:
        raise NotImplementedError("ZeroMqBus.start — pending ADR-0001")

    def close(self) -> None:
        raise NotImplementedError("ZeroMqBus.close — pending ADR-0001")


__all__ = ["ZeroMqBus"]
