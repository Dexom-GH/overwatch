# Bus message (de)serialization over ZeroMQ — Design

- **Issue:** [#10] `[chore] Bus message (de)serialization for the chosen transport` (P0, area:bus, status:ready, v1)
- **Date:** 2026-06-02
- **ADRs:** ADR-0001 (message bus — **Accepted**, hybrid: ZeroMQ ephemeral + SQLite EventStore)
- **Status:** Design approved; pending implementation plan.

## Problem

The bus schemas (`bus/schemas.py`) and topic names (`bus/topics.py`) are the
architecture's contract, but nothing flows between pipeline stages until those
schemas reliably **(de)serialize over the real transport**. ADR-0001 closed on a
hybrid bus: the high-rate **ephemeral tier → ZeroMQ**. This work implements the
serialization layer so every `schemas.*` dataclass round-trips
`serialize → ZeroMQ → deserialize`, and gives the pipeline a first concrete,
working `MessageBus`.

Blocks the capture spine (#14) and every downstream slice that publishes/consumes.

## Scope

**In:**
- A transport-agnostic codec for the `schemas.*` dataclasses (`bus/serialization.py`).
- A working `ZeroMqBus` (`bus/zeromq_bus.py`) that uses the codec over ZeroMQ.
- Host round-trip unit tests (codec alone, and over the real ZeroMQ transport).
- A device-marked cross-process `tcp` round-trip test (the on-device transport check).
- A dev bus-tap script (`scripts/dev/bus_tap.py`) that prints decoded, typed messages.
- A **non-invasive seam** for per-topic ZMQ socket options (so #39 can add
  HWM/`CONFLATE` without reworking the bus).

**Out (separate, already-groomed work — do not absorb):**
- HWM / `CONFLATE` backpressure *policy* — issue **#39**. (We add the seam, not the policy.)
- The SQLite `EventStore` durable tier — issue **#3**.
- Shared-memory / by-reference frame handoff — ADR-0001 "revisit if": only when a
  frame/depth consumer moves to a **separate process**. V1 is single-process, so
  the frame/depth payload strategy is **by-value**, per ADR-0001.
- `redis_bus.py` — stays a live, reversible stub (ADR-0001 keeps Redis as the
  benchmark-gated fallback). Untouched.

**Untouched contract surface:** `bus/schemas.py` and `bus/topics.py` do not
change. This is pure wire-format + transport work; the durability split is a
routing concern, not a schema change (ADR-0001 §Consequences).

## Approach (decisions)

### Wire format: typed codec, JSON header + raw array frames

Chosen over `pickle` and `msgpack`. Rationale:

- **Cross-numpy-version safety.** Host runs numpy 2.x; the Jetson runs an older
  numpy (paired with torch ~2.1). `pickle` of numpy arrays is not guaranteed
  stable across numpy major versions; a fixture encoded on the host could fail to
  decode on-device. Raw `dtype + shape + bytes` is read identically on both ends —
  essential for the record/replay interchange story (#11).
- **Schema-evolution tolerance.** Named fields + an explicit type tag let a
  decoder ignore unknown fields and default missing ones (no flag-day on a schema
  change). `pickle` binds to the exact class definition.
- **Introspectability.** ADR-0001 wants the dev bus-tap to print *decoded typed*
  messages; a JSON header makes that trivial and avoids executing the payload
  (`pickle`'s arbitrary-code-exec footgun on the most-reviewed surface).
- **No extra dependency.** stdlib `json` + `pyzmq` only; `msgpack` avoided.

### Transport test strategy: pyzmq is a host dependency

`pyzmq` ships Windows wheels (unlike `pyzed`/`torch`), so it resolves on the host.
We add it to host dev deps and run a **real in-process ZeroMQ loopback** round-trip
in host CI. `ZeroMqBus` therefore imports `pyzmq` directly (no import-guard — it
resolves on host). The "on-device transport check" becomes a confirmation on real
`tcp`, not the only proof.

## Architecture — three units

### 1. `bus/serialization.py` (NEW) — the codec

Pure Python + numpy + stdlib `json`. Transport-agnostic and **socket-free**, so it
is fully host-testable on its own. numpy is a base dependency (`pyproject`), so the
codec imports it at module top level; this keeps the numpy import out of
`schemas.py` (which stays dependency-light) while the codec legitimately owns it.

Public API:

```python
def encode(message: Any) -> List[bytes]: ...
    # -> [header_json_bytes, *array_buffers]

def decode(frames: List[bytes]) -> Any: ...
    # frames == [header_json_bytes, *array_buffers] -> reconstructed schemas.* dataclass

class SerializationError(Exception): ...
```

- A type **registry** is auto-built from `schemas.__all__` (every `@dataclass`), so
  `decode` maps a type tag back to its class. New schemas register automatically.
- `encode` does **not** prepend the topic — the transport owns topic framing. This
  keeps the codec transport-agnostic.

### 2. Wire format

A ZMQ multipart message:

```
frame[0] = topic            # utf-8; ZMQ PUB/SUB prefix-matches on this
frame[1] = header           # JSON: {"type": "<TypeName>", "tree": <field-tree>}
frame[2..] = raw numpy buffers  # dtype + shape carried in the header
```

The field tree is produced by recursively walking the dataclass instance, using
three **self-describing sentinels** so round-trips are exact (and `decoded ==
original` holds):

- `{"__ndarray__": {"buf": i, "dtype": "<np dtype str>", "shape": [...]}}` — array
  bytes are appended to the buffer list at global index `i` and sent raw
  (near-zero-copy on send, no base64 bloat). Covers `Frame.image`,
  `DepthFrame.depth`, `Identity.embedding`.
- `{"__type__": "<TypeName>", "tree": {...}}` — nested dataclasses recurse
  (`Track.identity → Identity`, `Alert.source_event → Event`).
- `{"__tuple__": [...]}` — restores `bbox` and pose keypoint tuples exactly
  (otherwise JSON would decode them as lists and break equality).

Plain scalars (`str`/`int`/`float`/`bool`/`None`), lists, and JSON-able dicts pass
through as-is.

**Constraints / edge cases:**
- `detail` fields (`Dict[str, Any]` on `HealthSignal`/`Event`/`Alert`) **must be
  JSON-serializable** — a documented producer responsibility. `encode` raises
  `SerializationError` with a clear message if a `detail` value is not JSON-able.
- Special floats (`NaN`/`Inf`) round-trip because both ends use Python `json`
  (symmetric `allow_nan`); documented as Python-to-Python only.
- Unknown type tag, missing buffer index, or buffer/shape mismatch on `decode`
  raise `SerializationError`.

### 3. `bus/zeromq_bus.py` (REWRITE) — minimal correct transport

A single shared instance (app.py injects one bus into all stages), single
`zmq.Context`:

- PUB socket **binds** `inproc://overwatch-bus`; SUB socket **connects** and
  subscribes to the registered topics. `inproc` = in-process, zero-copy, fast —
  the correct transport for single-process V1.
- `publish(topic, msg)` → `pub.send_multipart([topic.encode(), *encode(msg)])`.
- A background **dispatch thread** polls the SUB socket (`zmq.Poller` with a
  timeout so `close()` can stop it via a `threading.Event`), `decode`s each
  message, and calls the handlers registered for that topic. Handler exceptions
  are caught and logged so one bad handler cannot kill the bus.
- **Lifecycle contract:** `subscribe()` must be called **before** `start()`. The
  SUB socket is owned exclusively by the dispatch thread; we do not mutate
  subscriptions cross-thread. This matches the natural "wire, then start" app.py
  flow and the `MessageBus` ABC's `__enter__` semantics.
- **Seam for #39:** the constructor accepts an optional
  `socket_options: Dict[str, Dict[int, int]]` (topic → {zmq option: value}); V1
  passes none. This is where #39 wires per-lane `ZMQ_SNDHWM`/`ZMQ_RCVHWM`/
  `ZMQ_CONFLATE` without touching the bus internals.
- **Future seam (documented, not built):** swap `inproc` → `tcp` + an XPUB/XSUB
  forwarder when a frame/depth consumer moves cross-process (ADR-0001 "revisit if").

`base.py` is unchanged; stages keep depending only on the `MessageBus` ABC.

## Testing

- `tests/unit/test_serialization.py` — codec round-trip for **every** schema
  (parametrized over the registry), with representative instances incl. numpy
  arrays, nested dataclasses, and `detail` dicts. Host-only (numpy present).
- `tests/unit/test_zeromq_bus.py` — **real `inproc` loopback**: build the bus,
  `subscribe`, `start`, `publish` each schema, assert the handler received an equal
  object. Runs on host (pyzmq is a dev dep). Also covers: handler exception does
  not kill the bus; `SerializationError` on a malformed frame.
- A `device`-marked `tcp` cross-process round-trip = the on-device transport check.
- `tests/unit/test_imports.py` — add `overwatch.bus.serialization` to the host
  import smoke list.
- **Equality helper:** dataclasses with ndarray fields break Python `==` (numpy
  ambiguous-truth `ValueError`). Tests use a shared `assert_schema_equal(a, b)`
  helper — scalar fields compared directly, array fields via `np.array_equal`,
  recursing into nested dataclasses. Lives in a small test helper module.

## Files

| Action | Path |
| --- | --- |
| NEW | `src/overwatch/bus/serialization.py` |
| REWRITE | `src/overwatch/bus/zeromq_bus.py` |
| EDIT | `requirements.dev.txt` (+`pyzmq`) |
| EDIT | `requirements.target.txt` (uncomment `pyzmq`) |
| EDIT | `pyproject.toml` (+`pyzmq` as a host-runnable dependency) |
| NEW | `tests/unit/test_serialization.py` |
| NEW | `tests/unit/test_zeromq_bus.py` |
| NEW | `tests/unit/_schema_equal.py` (or a `conftest` fixture) — `assert_schema_equal` |
| EDIT | `tests/unit/test_imports.py` (+`overwatch.bus.serialization`) |
| NEW | `scripts/dev/bus_tap.py` — subscribe-all, print decoded typed messages |
| EDIT | `CHANGELOG.md` (Keep a Changelog entry under Unreleased) |

## Conventions / constraints honored

- **Python 3.8 compatible** target code: `typing.List/Dict/Optional/Tuple`, no
  `X | None`, no match statements, no walrus in shipped code.
- `schemas.py` / `topics.py` (the contract) **unchanged**; no bare topic strings.
- `ruff check src tests` and `mypy src` clean.
- `pytest -m "not device and not gpu and not zed"` passes on the host, including
  the import smoke test, the codec tests, and the inproc loopback test.

## Done when (issue #10)

- [ ] All bus schemas round-trip over the concrete `ZeroMqBus`.
- [ ] Frame/depth payload strategy (by-value per ADR-0001) implemented.
- [ ] Host round-trip unit tests pass (codec + inproc loopback).
- [ ] On-device transport check (device-marked `tcp` round-trip) defined and ready
      to run on the Jetson.

## Setup note

`pyzmq` is not yet installed on the host. Implementation begins by installing it
into the host dev environment (and adding it to the requirements files above)
before the loopback test can run.
