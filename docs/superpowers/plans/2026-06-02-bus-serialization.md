# Bus (de)serialization over ZeroMQ — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every `schemas.*` dataclass round-trip `serialize → ZeroMQ → deserialize` over a working `ZeroMqBus`, satisfying issue #10.

**Architecture:** A transport-agnostic, socket-free codec (`bus/serialization.py`) encodes each dataclass to a ZMQ multipart payload — a JSON header (type tag + recursive field tree with `__ndarray__`/`__type__`/`__tuple__` sentinels) plus raw numpy buffers. A minimal `ZeroMqBus` wires PUB/SUB over an in-process `inproc://` endpoint with a background dispatch thread, using the codec for the wire format. The contract surface (`schemas.py`/`topics.py`) is untouched.

**Tech Stack:** Python 3.8-compatible, numpy, `pyzmq` (host-installable), stdlib `json`/`threading`/`logging`, pytest.

**Spec:** [docs/superpowers/specs/2026-06-02-bus-serialization-design.md](../specs/2026-06-02-bus-serialization-design.md). ADR-0001 (hybrid bus, accepted).

**Conventions (apply to every code step):**
- Target code is **Python 3.8-compatible**: `from __future__ import annotations`; `typing.List/Dict/Optional/Tuple`; no `X | None`, no `match`, no walrus in shipped code.
- Run host tests with the real interpreter: `& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m pytest ...` (the bare `python` on this host is a dead Store stub). Shorthand below: `<py> -m pytest`.
- Default host test selection excludes target-only markers: `-m "not device and not gpu and not zed"`.

---

## File Structure

| Action | Path | Responsibility |
| --- | --- | --- |
| NEW | `src/overwatch/bus/serialization.py` | The codec: `encode`/`decode`/`SerializationError` + type registry. Socket-free. |
| REWRITE | `src/overwatch/bus/zeromq_bus.py` | Working `MessageBus` over ZeroMQ inproc PUB/SUB + dispatch thread. |
| EDIT | `requirements.dev.txt` | Add `pyzmq` (host dev). |
| EDIT | `requirements.target.txt` | Activate `pyzmq` line. |
| EDIT | `pyproject.toml` | Add `pyzmq` to `[project].dependencies`. |
| NEW | `tests/unit/_schema_equal.py` | Shared test helpers: `assert_schema_equal`, `sample_messages`. (Underscore = not collected.) |
| NEW | `tests/unit/test_serialization.py` | Codec round-trip + error tests. |
| NEW | `tests/unit/test_zeromq_bus.py` | Real inproc loopback + lifecycle + handler-isolation tests. |
| NEW | `tests/device/test_zeromq_tcp.py` | `device`-marked tcp round-trip (on-device transport check). |
| EDIT | `tests/unit/test_imports.py` | Add `overwatch.bus.serialization` to the smoke list. |
| NEW | `scripts/dev/bus_tap.py` | Dev tap: subscribe-all, print decoded typed messages. |
| EDIT | `CHANGELOG.md` | Keep a Changelog entry under Unreleased. |

---

## Task 1: Add `pyzmq` dependency (host + target)

**Files:**
- Modify: `requirements.dev.txt`
- Modify: `requirements.target.txt:22-25`
- Modify: `pyproject.toml:20-24`

- [ ] **Step 1: Install pyzmq into the host dev environment**

Run:
```
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m pip install pyzmq
```
Expected: `Successfully installed pyzmq-<version>`.

- [ ] **Step 2: Verify it imports**

Run:
```
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -c "import zmq; print('pyzmq', zmq.__version__, 'libzmq', zmq.zmq_version())"
```
Expected: a version line, e.g. `pyzmq 26.x libzmq 4.3.x`.

- [ ] **Step 3: Add pyzmq to `requirements.dev.txt`**

Append under the "Host-runnable runtime deps" block so it reads:
```
# Host-runnable runtime deps (mirror pyproject [project].dependencies)
numpy
pyyaml
pydantic>=2
pyzmq
```

- [ ] **Step 4: Activate pyzmq in `requirements.target.txt`**

Replace lines 22-25 (the commented transport block) with:
```
# Transport client (ADR-0001 closed -> hybrid: ZeroMQ ephemeral tier). Plain PyPI
# install (aarch64 / Python 3.8 wheels exist; the bus impl is issue #10).
pyzmq
# redis            (only if the on-device benchmark flips ADR-0001 back to Redis)
```

- [ ] **Step 5: Add pyzmq to `pyproject.toml` dependencies**

Edit the `dependencies` list (lines 20-24) to:
```toml
dependencies = [
    "numpy",
    "pyyaml",
    "pydantic>=2",
    "pyzmq",
]
```

- [ ] **Step 6: Commit**

```
git add requirements.dev.txt requirements.target.txt pyproject.toml
git commit -m "build: add pyzmq dependency for the ZeroMQ bus (#10)"
```

---

## Task 2: Shared test helpers (`assert_schema_equal`, `sample_messages`)

These are used by both the codec and bus tests, so they come first. numpy-bearing dataclasses break Python `==` (ambiguous array truth), hence a custom comparator. `sample_messages()` is one representative instance of **every** schema, exercising arrays, nested dataclasses, tuples, and dicts.

**Files:**
- Create: `tests/unit/_schema_equal.py`

- [ ] **Step 1: Write the helper module**

```python
"""Shared test helpers for bus serialization / transport tests.

Underscore-prefixed so pytest does not collect it as a test module. Imported by
sibling test files (pytest 'prepend' import mode puts tests/unit on sys.path).
"""

from __future__ import annotations

import dataclasses
from typing import Any, List, Tuple

import numpy as np

from overwatch.bus import schemas, topics


def assert_schema_equal(a: Any, b: Any) -> None:
    """Deep equality that handles numpy arrays, nested dataclasses, tuples, dicts."""
    assert type(a) is type(b), "type mismatch: {!r} vs {!r}".format(type(a), type(b))
    if dataclasses.is_dataclass(a):
        for f in dataclasses.fields(a):
            assert_schema_equal(getattr(a, f.name), getattr(b, f.name))
    elif isinstance(a, np.ndarray):
        assert a.dtype == b.dtype, "dtype {} != {}".format(a.dtype, b.dtype)
        assert a.shape == b.shape, "shape {} != {}".format(a.shape, b.shape)
        assert np.array_equal(a, b), "array contents differ"
    elif isinstance(a, (list, tuple)):
        assert len(a) == len(b), "length {} != {}".format(len(a), len(b))
        for x, y in zip(a, b):
            assert_schema_equal(x, y)
    elif isinstance(a, dict):
        assert a.keys() == b.keys(), "dict keys differ"
        for k in a:
            assert_schema_equal(a[k], b[k])
    else:
        assert a == b, "{!r} != {!r}".format(a, b)


def sample_messages() -> List[Tuple[str, Any]]:
    """One representative instance of every schema, with realistic payloads."""
    image = np.arange(2 * 3 * 3, dtype=np.uint8).reshape(2, 3, 3)
    depth = np.linspace(0.5, 4.0, 6, dtype=np.float32).reshape(2, 3)
    embedding = np.arange(8, dtype=np.float32)
    event = schemas.Event(
        timestamp=1.0, kind="fence_crossing", track_id=7, zone_id="z1",
        detail={"direction": "in"},
    )
    identity = schemas.Identity(
        track_id=7, embedding=embedding, matched_id=None, score=None,
    )
    return [
        (topics.CAPTURE_FRAME, schemas.Frame(
            source_id="cam0", frame_id=1, timestamp=1.0, image=image,
            width=3, height=2)),
        (topics.CAPTURE_DEPTH, schemas.DepthFrame(
            source_id="cam0", frame_id=1, timestamp=1.0, depth=depth)),
        (topics.INFER_DETECTION, schemas.Detection(
            frame_id=1, bbox=(1.0, 2.0, 3.0, 4.0), class_id=0,
            class_name="sheep", confidence=0.9)),
        (topics.INFER_TRACK, schemas.Track(
            track_id=7, frame_id=1, bbox=(1.0, 2.0, 3.0, 4.0), class_id=0,
            class_name="sheep", confidence=0.9, identity=identity)),
        (topics.FUSION_DEPTH_BBOX, schemas.DepthBBox(
            track_id=7, frame_id=1, bbox=(1.0, 2.0, 3.0, 4.0),
            depth_m=2.5, size_estimate=0.8)),
        (topics.INFER_IDENTITY, identity),
        (topics.INFER_POSE, schemas.Pose(
            track_id=7, frame_id=1,
            keypoints=[(1.0, 2.0, 0.9), (3.0, 4.0, 0.8)])),
        (topics.FUSION_COUNT, schemas.ZoneCount(
            zone_id="z1", timestamp=1.0, count=3, class_name="sheep")),
        (topics.FUSION_HEALTH, schemas.HealthSignal(
            track_id=7, timestamp=1.0, kind="immobility", score=0.7,
            detail={"seconds": 120})),
        (topics.FUSION_EVENT, event),
        (topics.OUTPUT_ALERT, schemas.Alert(
            timestamp=1.0, severity="warning", title="t", message="m",
            source_event=event, detail={"k": "v"})),
    ]
```

- [ ] **Step 2: Sanity-check the helper imports and covers all schemas**

Run:
```
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -c "import sys; sys.path.insert(0, 'tests/unit'); import _schema_equal as h; ms=h.sample_messages(); names={type(m).__name__ for _,m in ms}; from overwatch.bus import schemas; want={n for n in schemas.__all__ if n!='BBox'}; print('covered:', names==want, sorted(want-names))"
```
Expected: `covered: True []`.

- [ ] **Step 3: Commit**

```
git add tests/unit/_schema_equal.py
git commit -m "test: shared schema-equality + sample-message helpers (#10)"
```

---

## Task 3: The codec (`bus/serialization.py`)

**Files:**
- Create: `src/overwatch/bus/serialization.py`
- Test: `tests/unit/test_serialization.py`

- [ ] **Step 1: Write the failing round-trip + error tests**

Create `tests/unit/test_serialization.py`:
```python
"""Codec round-trip tests — host-only (no sockets, numpy present)."""

import json

import pytest

from _schema_equal import assert_schema_equal, sample_messages
from overwatch.bus import serialization

_SAMPLES = sample_messages()
_IDS = [type(m).__name__ for _, m in _SAMPLES]


@pytest.mark.parametrize("topic,message", _SAMPLES, ids=_IDS)
def test_codec_round_trip(topic, message):
    frames = serialization.encode(message)
    assert isinstance(frames, list)
    assert all(isinstance(f, (bytes, bytearray)) for f in frames)
    decoded = serialization.decode(frames)
    assert_schema_equal(message, decoded)


def test_encode_rejects_non_schema():
    with pytest.raises(serialization.SerializationError):
        serialization.encode({"not": "a schema"})


def test_decode_rejects_unknown_type():
    bad = [json.dumps({"type": "Nope", "tree": {}}).encode("utf-8")]
    with pytest.raises(serialization.SerializationError):
        serialization.decode(bad)


def test_decode_rejects_empty_frames():
    with pytest.raises(serialization.SerializationError):
        serialization.decode([])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m pytest tests/unit/test_serialization.py -v
```
Expected: collection/import error — `ModuleNotFoundError: No module named 'overwatch.bus.serialization'`.

- [ ] **Step 3: Implement the codec**

Create `src/overwatch/bus/serialization.py`:
```python
"""Bus wire (de)serialization — the codec for the schemas.* contract.

Encodes any ``schemas.*`` dataclass to a ZeroMQ-friendly multipart payload and
back. Transport-agnostic and socket-free, so it is fully host-testable on its own
(``ZeroMqBus`` is the only thing that touches sockets).

Wire shape (the transport prepends the topic as a leading frame):

    frame[0] = header   (JSON, utf-8): {"type": "<TypeName>", "tree": <field-tree>}
    frame[1..] = raw numpy buffers, indexed by the header's __ndarray__ descriptors

The field-tree uses three self-describing sentinels so round-trips are exact:
    {"__ndarray__": {"buf": i, "dtype": <str>, "shape": [...]}}  -> numpy array
    {"__type__": "<TypeName>", "tree": {...}}                    -> nested dataclass
    {"__tuple__": [...]}                                         -> tuple (vs list)

Typed/explicit (not pickle) for cross-numpy-version stability, schema-evolution
tolerance, and introspectability. Reserved sentinel keys (``__ndarray__``,
``__type__``, ``__tuple__``) must not appear as keys in a ``detail`` dict. See
docs/superpowers/specs/2026-06-02-bus-serialization-design.md and ADR-0001.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any, Dict, List

import numpy as np

from overwatch.bus import schemas


class SerializationError(Exception):
    """Raised when a message cannot be encoded or decoded."""


def _build_registry() -> Dict[str, type]:
    registry = {}  # type: Dict[str, type]
    for name in schemas.__all__:
        obj = getattr(schemas, name)
        if isinstance(obj, type) and dataclasses.is_dataclass(obj):
            registry[name] = obj
    return registry


_REGISTRY = _build_registry()  # type: Dict[str, type]


def _encode_value(value: Any, buffers: List[bytes]) -> Any:
    if isinstance(value, np.ndarray):
        descriptor = {
            "buf": len(buffers),
            "dtype": str(value.dtype),
            "shape": list(value.shape),
        }
        buffers.append(value.tobytes())
        return {"__ndarray__": descriptor}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            "__type__": type(value).__name__,
            "tree": _encode_dataclass(value, buffers),
        }
    if isinstance(value, tuple):
        return {"__tuple__": [_encode_value(v, buffers) for v in value]}
    if isinstance(value, list):
        return [_encode_value(v, buffers) for v in value]
    if isinstance(value, dict):
        return {k: _encode_value(v, buffers) for k, v in value.items()}
    return value


def _encode_dataclass(obj: Any, buffers: List[bytes]) -> Dict[str, Any]:
    return {
        f.name: _encode_value(getattr(obj, f.name), buffers)
        for f in dataclasses.fields(obj)
    }


def encode(message: Any) -> List[bytes]:
    """Encode a ``schemas.*`` dataclass to ``[header_json, *array_buffers]``."""
    if not (dataclasses.is_dataclass(message) and not isinstance(message, type)):
        raise SerializationError(
            "cannot encode {!r}: not a dataclass instance".format(type(message))
        )
    type_name = type(message).__name__
    if type_name not in _REGISTRY:
        raise SerializationError(
            "cannot encode {!r}: not a registered schema type".format(type_name)
        )
    buffers = []  # type: List[bytes]
    tree = _encode_dataclass(message, buffers)
    header = {"type": type_name, "tree": tree}
    try:
        header_bytes = json.dumps(header).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SerializationError(
            "message {} is not JSON-serializable (check detail fields): {}".format(
                type_name, exc
            )
        )
    return [header_bytes] + buffers


def _decode_value(value: Any, buffers: List[bytes]) -> Any:
    if isinstance(value, dict):
        if "__ndarray__" in value:
            descriptor = value["__ndarray__"]
            index = descriptor["buf"]
            if index < 0 or index >= len(buffers):
                raise SerializationError(
                    "array buffer index {} out of range".format(index)
                )
            array = np.frombuffer(buffers[index], dtype=descriptor["dtype"])
            return array.reshape(descriptor["shape"])
        if "__type__" in value:
            name = value["__type__"]
            if name not in _REGISTRY:
                raise SerializationError("unknown nested schema type: {!r}".format(name))
            fields = _decode_tree(value["tree"], buffers)
            return _REGISTRY[name](**fields)
        if "__tuple__" in value:
            return tuple(_decode_value(v, buffers) for v in value["__tuple__"])
        return {k: _decode_value(v, buffers) for k, v in value.items()}
    if isinstance(value, list):
        return [_decode_value(v, buffers) for v in value]
    return value


def _decode_tree(tree: Dict[str, Any], buffers: List[bytes]) -> Dict[str, Any]:
    return {k: _decode_value(v, buffers) for k, v in tree.items()}


def decode(frames: List[bytes]) -> Any:
    """Decode ``[header_json, *array_buffers]`` back to a ``schemas.*`` dataclass."""
    if not frames:
        raise SerializationError("cannot decode an empty frame list")
    try:
        header = json.loads(bytes(frames[0]).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise SerializationError("invalid header JSON: {}".format(exc))
    buffers = [bytes(f) for f in frames[1:]]
    type_name = header.get("type")
    if type_name not in _REGISTRY:
        raise SerializationError("unknown schema type: {!r}".format(type_name))
    fields = _decode_tree(header.get("tree", {}), buffers)
    try:
        return _REGISTRY[type_name](**fields)
    except TypeError as exc:
        raise SerializationError(
            "cannot construct {}: {}".format(type_name, exc)
        )


__all__ = ["encode", "decode", "SerializationError"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m pytest tests/unit/test_serialization.py -v
```
Expected: all PASS (11 round-trip cases + 3 error cases).

- [ ] **Step 5: Lint and type-check**

Run:
```
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m ruff check src/overwatch/bus/serialization.py tests/unit/test_serialization.py tests/unit/_schema_equal.py
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m mypy src
```
Expected: `All checks passed!` / no errors.

- [ ] **Step 6: Commit**

```
git add src/overwatch/bus/serialization.py tests/unit/test_serialization.py
git commit -m "feat(bus): typed codec for schema (de)serialization (#10)"
```

---

## Task 4: Add the codec to the host import smoke test

**Files:**
- Modify: `tests/unit/test_imports.py` (HOST_SAFE_MODULES list)

- [ ] **Step 1: Add the module to the smoke list**

In `tests/unit/test_imports.py`, add `"overwatch.bus.serialization",` to `HOST_SAFE_MODULES`, immediately after `"overwatch.bus.schemas",`:
```python
    "overwatch.bus.schemas",
    "overwatch.bus.serialization",
    "overwatch.bus.redis_bus",
```

- [ ] **Step 2: Run the smoke test**

Run:
```
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m pytest tests/unit/test_imports.py -v
```
Expected: all PASS (now includes `overwatch.bus.serialization`).

- [ ] **Step 3: Commit**

```
git add tests/unit/test_imports.py
git commit -m "test: cover bus.serialization in host import smoke test (#10)"
```

---

## Task 5: `ZeroMqBus` over inproc PUB/SUB

**Files:**
- Rewrite: `src/overwatch/bus/zeromq_bus.py`
- Test: `tests/unit/test_zeromq_bus.py`

- [ ] **Step 1: Write the failing transport tests**

Create `tests/unit/test_zeromq_bus.py`:
```python
"""Real in-process ZeroMQ loopback tests (pyzmq is a host dev dep)."""

import threading

import pytest

from _schema_equal import assert_schema_equal, sample_messages
from overwatch.bus import schemas, topics
from overwatch.bus.zeromq_bus import ZeroMqBus

_SAMPLES = sample_messages()
_IDS = [type(m).__name__ for _, m in _SAMPLES]


@pytest.mark.parametrize("topic,message", _SAMPLES, ids=_IDS)
def test_inproc_round_trip(topic, message):
    received = []
    done = threading.Event()

    def handler(msg):
        received.append(msg)
        done.set()

    bus = ZeroMqBus()
    bus.subscribe(topic, handler)
    bus.start()
    try:
        bus.publish(topic, message)
        assert done.wait(timeout=2.0), "handler was not called"
    finally:
        bus.close()

    assert len(received) == 1
    assert_schema_equal(message, received[0])


def test_handler_exception_does_not_kill_bus():
    good = []
    done = threading.Event()

    def bad(_msg):
        raise ValueError("boom")

    def ok(msg):
        good.append(msg)
        done.set()

    bus = ZeroMqBus()
    bus.subscribe(topics.FUSION_COUNT, bad)
    bus.subscribe(topics.FUSION_COUNT, ok)
    bus.start()
    try:
        bus.publish(
            topics.FUSION_COUNT,
            schemas.ZoneCount(zone_id="z1", timestamp=1.0, count=1),
        )
        assert done.wait(timeout=2.0), "good handler not reached after bad one raised"
    finally:
        bus.close()

    assert len(good) == 1


def test_subscribe_after_start_raises():
    bus = ZeroMqBus()
    bus.start()
    try:
        with pytest.raises(RuntimeError):
            bus.subscribe(topics.FUSION_COUNT, lambda _m: None)
    finally:
        bus.close()


def test_publish_before_start_raises():
    bus = ZeroMqBus()
    with pytest.raises(RuntimeError):
        bus.publish(
            topics.FUSION_COUNT,
            schemas.ZoneCount(zone_id="z1", timestamp=1.0, count=1),
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m pytest tests/unit/test_zeromq_bus.py -v
```
Expected: FAIL — `ZeroMqBus.publish — pending ADR-0001` `NotImplementedError` (the current stub) and/or assertion failures.

- [ ] **Step 3: Rewrite `ZeroMqBus`**

Replace the entire contents of `src/overwatch/bus/zeromq_bus.py` with:
```python
"""ZeroMQ-backed MessageBus (ADR-0001 ephemeral tier).

Single-process V1 transport: one PUB socket binds an ``inproc://`` endpoint, one
SUB socket connects to it; a background thread decodes incoming multipart messages
(via ``bus/serialization.py``) and dispatches them to handlers by topic.

``pyzmq`` ships host wheels, so this imports it directly and is exercised by host
unit tests — no import guard needed (unlike pyzed/torch).

Lifecycle: register all ``subscribe()`` handlers BEFORE ``start()``. The SUB
socket is owned exclusively by the dispatch thread; subscriptions are not mutated
cross-thread in V1. ``start()`` settles briefly to cover the PUB/SUB slow-joiner
so the first ``publish()`` is delivered.

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

    def subscribe(self, topic: str, handler: Handler) -> None:
        if self._thread is not None:
            raise RuntimeError("subscribe() must be called before start()")
        self._handlers.setdefault(topic, []).append(handler)

    def publish(self, topic: str, message: Any) -> None:
        if self._pub is None:
            raise RuntimeError("publish() called before start()")
        frames = serialization.encode(message)
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
        poller = zmq.Poller()
        poller.register(self._sub, zmq.POLLIN)
        self._ready.set()
        while not self._stop.is_set():
            events = dict(poller.poll(timeout=100))
            if self._sub in events:
                frames = self._sub.recv_multipart()
                self._dispatch(frames)

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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m pytest tests/unit/test_zeromq_bus.py -v
```
Expected: all PASS (11 loopback cases + handler-isolation + 2 lifecycle cases).

- [ ] **Step 5: Lint and type-check**

Run:
```
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m ruff check src/overwatch/bus/zeromq_bus.py tests/unit/test_zeromq_bus.py
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m mypy src
```
Expected: clean.

- [ ] **Step 6: Commit**

```
git add src/overwatch/bus/zeromq_bus.py tests/unit/test_zeromq_bus.py
git commit -m "feat(bus): working ZeroMqBus over inproc PUB/SUB (#10)"
```

---

## Task 6: Device-marked tcp transport check

Validates the `tcp` path (the on-device transport check). `device`-marked so it is skipped in host CI and run on the Jetson.

**Files:**
- Create: `tests/device/test_zeromq_tcp.py`

- [ ] **Step 1: Confirm the device test dir exists; create if missing**

Run:
```
New-Item -ItemType Directory -Force tests\device | Out-Null; Test-Path tests\device
```
Expected: `True`.

- [ ] **Step 2: Write the device test**

Create `tests/device/test_zeromq_tcp.py`:
```python
"""On-device transport check: ZeroMqBus over a real tcp endpoint.

Marked ``device`` so host CI skips it (``-m "not device..."``); run on the Jetson
to confirm the chosen transport round-trips over tcp, not just inproc.
"""

import threading

import pytest

from overwatch.bus import schemas, topics
from overwatch.bus.zeromq_bus import ZeroMqBus

pytestmark = pytest.mark.device


def test_tcp_round_trip():
    received = []
    done = threading.Event()

    def handler(msg):
        received.append(msg)
        done.set()

    bus = ZeroMqBus(endpoint="tcp://127.0.0.1:5599")
    bus.subscribe(topics.FUSION_COUNT, handler)
    bus.start()
    try:
        bus.publish(
            topics.FUSION_COUNT,
            schemas.ZoneCount(zone_id="z1", timestamp=1.0, count=5),
        )
        assert done.wait(timeout=3.0), "tcp message not delivered"
    finally:
        bus.close()

    assert received[0].count == 5
```

- [ ] **Step 3: Verify it is collected but skipped on the host**

Run:
```
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m pytest tests/device/test_zeromq_tcp.py -m "not device and not gpu and not zed" -v
```
Expected: `1 deselected` (skipped on host — correct).

- [ ] **Step 4: Optionally run it directly on the host to prove the tcp path works**

Run:
```
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m pytest tests/device/test_zeromq_tcp.py -m device -v
```
Expected: PASS (tcp loopback works on the host too; on-device run is the real check).

- [ ] **Step 5: Commit**

```
git add tests/device/test_zeromq_tcp.py
git commit -m "test(bus): device-marked tcp transport check (#10)"
```

---

## Task 7: Dev bus-tap script

**Files:**
- Create: `scripts/dev/bus_tap.py`

- [ ] **Step 1: Write the tap**

Create `scripts/dev/bus_tap.py`:
```python
"""Dev bus-tap: subscribe to every topic and print decoded, typed messages.

Recovers the live-inspectability a single Redis broker would give for free
(ADR-0001). Host/dev tooling only — not part of the shipped package, never runs
on the Jetson. Run with a real host interpreter:

    <real-python>\\python.exe scripts\\dev\\bus_tap.py

Because V1 is single-process inproc, a separate tap process does NOT see another
process's messages (inproc is per-context). This is therefore most useful inside
a demo/test that publishes on the same ZeroMqBus instance, or as a template for a
future tcp tap. Kept import-light and 3.8-compatible.
"""

from __future__ import annotations

import dataclasses
import time
from typing import Any, Callable, List

from overwatch.bus import topics as topics_mod
from overwatch.bus.zeromq_bus import ZeroMqBus


def _all_topics() -> List[str]:
    return [getattr(topics_mod, name) for name in topics_mod.__all__]


def _make_printer(topic: str) -> Callable[[Any], None]:
    def handler(message: Any) -> None:
        if dataclasses.is_dataclass(message) and not isinstance(message, type):
            fields = {
                f.name: getattr(message, f.name)
                for f in dataclasses.fields(message)
            }
            print("[{}] {}: {}".format(topic, type(message).__name__, fields))
        else:
            print("[{}] {!r}".format(topic, message))

    return handler


def main() -> None:
    bus = ZeroMqBus()
    all_topics = _all_topics()
    for topic in all_topics:
        bus.subscribe(topic, _make_printer(topic))
    bus.start()
    print("bus-tap listening on {} topics. Ctrl-C to stop.".format(len(all_topics)))
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        bus.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-check it imports and starts/stops cleanly**

Run:
```
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -c "import sys; sys.argv=['bus_tap']; import runpy; import threading; import scripts.dev.bus_tap as t; b=t.ZeroMqBus(); [b.subscribe(x, lambda m: None) for x in t._all_topics()]; b.start(); b.close(); print('bus_tap OK')"
```
Expected: `bus_tap OK` (no exceptions). If `scripts` is not importable as a package, instead run: `& "<py>" -c "import sys; sys.path.insert(0,'scripts/dev'); import bus_tap as t; b=t.ZeroMqBus(); b.start(); b.close(); print('bus_tap OK')"`.

- [ ] **Step 3: Lint**

Run:
```
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m ruff check scripts/dev/bus_tap.py
```
Expected: clean.

- [ ] **Step 4: Commit**

```
git add scripts/dev/bus_tap.py
git commit -m "feat(dev): bus-tap that prints decoded typed messages (#10)"
```

---

## Task 8: CHANGELOG + full verification

**Files:**
- Modify: `CHANGELOG.md:9-23` (Unreleased → Added)

- [ ] **Step 1: Add a CHANGELOG entry**

Under `## [Unreleased]` → `### Added`, append:
```
- Bus message (de)serialization (#10): a typed codec (`bus/serialization.py`,
  JSON header + raw numpy frames) and a working `ZeroMqBus` (inproc PUB/SUB) so
  every `schemas.*` dataclass round-trips over the ZeroMQ ephemeral tier
  (ADR-0001). `pyzmq` added as a host-runnable dependency; dev `bus_tap.py`
  prints decoded typed messages.
```

- [ ] **Step 2: Run the full host test suite**

Run:
```
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m pytest -m "not device and not gpu and not zed" -v
```
Expected: all PASS (existing config/imports tests + new serialization + zeromq tests; device tcp test deselected).

- [ ] **Step 3: Full lint + type-check**

Run:
```
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m ruff check src tests scripts
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m mypy src
```
Expected: `All checks passed!` / no errors.

- [ ] **Step 4: Confirm the contract surface is unchanged**

Run:
```
git diff --stat master -- src/overwatch/bus/schemas.py src/overwatch/bus/topics.py
```
Expected: **no output** (schemas.py / topics.py untouched — the contract held).

- [ ] **Step 5: Commit**

```
git add CHANGELOG.md
git commit -m "docs: changelog for bus (de)serialization (#10)"
```

---

## Done-when verification (issue #10)

- [ ] All bus schemas round-trip over the concrete `ZeroMqBus` — Task 5 inproc loopback (parametrized over every schema) passes.
- [ ] Frame/depth payload strategy (by-value per ADR-0001) implemented — codec sends arrays as raw buffers in the message; Task 3 covers `Frame`/`DepthFrame`/`Identity` arrays.
- [ ] Host round-trip unit tests pass — Tasks 3 + 5, confirmed by the Task 8 full-suite run.
- [ ] On-device transport check defined — Task 6 `device`-marked tcp test, ready to run on the Jetson.

## Notes for the implementer

- **Slow joiner:** `start()` sleeps `_SLOW_JOINER_SETTLE_S` (0.1s) after the SUB subscription so the first `publish()` is delivered. This is inherent to ZeroMQ PUB/SUB, not a workaround — don't remove it or the first message can be silently dropped.
- **Read-only decoded arrays:** `np.frombuffer` returns a read-only array; decoded `Frame.image`/`depth`/`embedding` are not writable. V1 consumers treat them as read-only. If a consumer must mutate, it copies — do not add a blanket `.copy()` to the codec (it would defeat the zero-copy receive).
- **`detail` dicts** must be JSON-serializable and must not use the reserved sentinel keys (`__ndarray__`/`__type__`/`__tuple__`). This is the producer's responsibility (documented in the codec module).
- **Sibling test import** (`from _schema_equal import ...`) relies on pytest's default `prepend` import mode adding `tests/unit` to `sys.path`. There is intentionally no `tests/unit/__init__.py`.
