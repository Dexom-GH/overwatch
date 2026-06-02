"""Transport-agnostic message bus interface.

The concrete transport (Redis vs ZeroMQ) is undecided — ADR-0001. All stage code
depends on this ABC, never on a concrete bus, so the decision can close later
without touching stages. ``redis_bus.py`` and ``zeromq_bus.py`` are parallel
stubs implementing this interface; neither is privileged.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable

# A subscriber callback receives the deserialized message (a schemas.* dataclass).
Handler = Callable[[Any], None]


class MessageBus(ABC):
    """Publish/subscribe over named topics (see ``topics.py``).

    Implementations decide serialization and delivery semantics. Stages should
    treat publish as fire-and-forget and subscribe as registering a handler that
    receives decoded ``schemas.*`` messages.
    """

    @abstractmethod
    def publish(self, topic: str, message: Any) -> None:
        """Publish ``message`` (a ``schemas.*`` dataclass) to ``topic``."""
        raise NotImplementedError

    @abstractmethod
    def subscribe(self, topic: str, handler: Handler) -> None:
        """Register ``handler`` to be called for each message on ``topic``."""
        raise NotImplementedError

    @abstractmethod
    def start(self) -> None:
        """Begin delivering messages (open connections, spawn loops)."""
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        """Stop delivery and release resources."""
        raise NotImplementedError

    def __enter__(self) -> "MessageBus":
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


__all__ = ["MessageBus", "Handler"]
