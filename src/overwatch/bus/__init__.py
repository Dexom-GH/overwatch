"""Message bus — the spine connecting pipeline stages.

The schemas (``schemas.py``) and topic names (``topics.py``) are THE contract.
Stages communicate only through the bus, never by importing each other's
internals. The concrete transport (Redis vs ZeroMQ) is an open decision
(docs/DECISIONS/0001) — code targets the transport-agnostic ``MessageBus`` ABC.

To add a stage that speaks this contract, follow the ``bus-stage-conventions``
skill.
"""

from overwatch.bus.base import MessageBus
from overwatch.bus import topics

__all__ = ["MessageBus", "topics"]
