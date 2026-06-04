"""ZeroMQ-backed MessageBus (ADR-0001 ephemeral tier).

Single-process V1 transport: one PUB socket binds an ``inproc://`` endpoint, one
SUB socket connects to it; a background thread decodes incoming multipart messages
(via ``bus/serialization.py``) and dispatches them to handlers by topic.

``pyzmq`` ships host wheels, so this imports it directly and is exercised by host
unit tests — no import guard needed (unlike pyzed/torch).

Lifecycle: register all ``subscribe()`` handlers BEFORE ``start()``. The SUB
socket is owned exclusively by the dispatch thread; subscriptions are not mutated
cross-thread in V1. ``start()`` settles briefly to cover the PUB/SUB slow-joiner
so the first ``publish()`` is delivered. ``publish()`` sends on the shared PUB
socket from the caller's thread; ZeroMQ sockets are not thread-safe, so sends are
serialized by an internal lock — supervised multi-stage pipelines fan out
producers (capture / inference / fusion publish from their own threads, #38).

Out of scope here (separate issues): HWM/CONFLATE backpressure policy (#39 — wire
it via the ``socket_options`` seam); a tcp + XPUB/XSUB topology for cross-process
consumers (ADR-0001 "revisit if").
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

import zmq

from overwatch.bus import serialization
from overwatch.bus.base import Handler, MessageBus

_LOG = logging.getLogger(__name__)

# Time given to the SUB socket's subscription to propagate before start() returns.
_SLOW_JOINER_SETTLE_S = 0.1


class ZeroMqBus(MessageBus):
    """PUB/SUB over ZeroMQ, single shared in-process instance."""

    def __init__(
        self,
        endpoint: str = "inproc://overwatch-bus",
        socket_options: Optional[Dict[str, Dict[int, int]]] = None,
    ) -> None:
        self._endpoint = endpoint
        # Seam for #39: per-topic SUB socket options (e.g. RCVHWM/CONFLATE).
        self._socket_options = socket_options or {}
        self._ctx = None  # type: Optional[Any]
        self._pub = None  # type: Optional[Any]
        self._sub = None  # type: Optional[Any]
        self._handlers = {}  # type: Dict[str, List[Handler]]
        self._thread = None  # type: Optional[threading.Thread]
        self._stop = threading.Event()
        self._ready = threading.Event()
        # Serialize sends: the PUB socket is not thread-safe, and a supervised
        # multi-stage pipeline fans out producers (capture / inference / fusion all
        # publish from their own threads, #38). One lock keeps sends atomic.
        self._pub_lock = threading.Lock()

    def subscribe(self, topic: str, handler: Handler) -> None:
        if self._thread is not None:
            raise RuntimeError("subscribe() must be called before start()")
        self._handlers.setdefault(topic, []).append(handler)

    def publish(self, topic: str, message: Any) -> None:
        if self._pub is None:
            raise RuntimeError("publish() called before start()")
        frames = serialization.encode(message)
        # Lock the send: multiple stage threads may publish concurrently (#38) and
        # ZeroMQ sockets are not thread-safe.
        with self._pub_lock:
            self._pub.send_multipart([topic.encode("utf-8")] + frames)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._ctx = zmq.Context()
        self._pub = self._ctx.socket(zmq.PUB)
        self._pub.bind(self._endpoint)
        self._sub = self._ctx.socket(zmq.SUB)
        self._sub.connect(self._endpoint)
        for topic in self._handlers:
            self._sub.setsockopt(zmq.SUBSCRIBE, topic.encode("utf-8"))
            for opt, val in self._socket_options.get(topic, {}).items():
                self._sub.setsockopt(opt, val)
        self._stop.clear()
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._run, name="zeromq-bus", daemon=True
        )
        self._thread.start()
        self._ready.wait(timeout=1.0)
        time.sleep(_SLOW_JOINER_SETTLE_S)

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        for sock in (self._sub, self._pub):
            if sock is not None:
                sock.close(linger=0)
        self._sub = None
        self._pub = None
        if self._ctx is not None:
            self._ctx.term()
            self._ctx = None

    def _run(self) -> None:
        sub = self._sub  # bound once; owned by this thread for its lifetime
        if sub is None:  # start() always sets it before launching the thread
            return
        poller = zmq.Poller()
        poller.register(sub, zmq.POLLIN)
        self._ready.set()
        while not self._stop.is_set():
            try:
                events = dict(poller.poll(timeout=100))
                if sub in events:
                    frames = sub.recv_multipart()
                    self._dispatch(frames)
            except zmq.ZMQError:
                # Transport-level error: break cleanly if we're shutting down
                # (e.g. ETERM), otherwise log and keep the dispatch loop alive.
                if self._stop.is_set():
                    break
                _LOG.exception("ZeroMQ error in dispatch loop")

    def _dispatch(self, frames: List[bytes]) -> None:
        if not frames:
            return
        topic = frames[0].decode("utf-8")
        try:
            message = serialization.decode(frames[1:])
        except serialization.SerializationError:
            _LOG.exception("failed to decode message on topic %s", topic)
            return
        for handler in self._handlers.get(topic, []):
            try:
                handler(message)
            except Exception:  # one bad handler must not kill the bus
                _LOG.exception("handler error on topic %s", topic)


__all__ = ["ZeroMqBus"]
