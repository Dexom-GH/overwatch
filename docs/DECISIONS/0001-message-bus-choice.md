# ADR 0001 — Message bus: Redis vs ZeroMQ

- **Status:** Accepted — V1 planning default (Option C, Hybrid); final accept
  **gated on the on-device benchmark**. Reversible to Option A behind the
  `MessageBus` ABC.
- **Date:** 2026-06-02
- **Deciders:** project owner
- **Resolved by:** issue #4 (adr-fanout: 3 advocates + 3 judge lenses)

## Context

Pipeline stages (capture → inference → fusion → output) are decoupled through a
message bus. We need a concrete transport. The decision was deliberately left
open at scaffold time: pick the concrete impl after on-device behavior is
understood. Until then, code targets the transport-agnostic `MessageBus` ABC in
`src/overwatch/bus/base.py`, and `redis_bus.py` / `zeromq_bus.py` exist as
parallel stubs — **neither is privileged**.

Constraints: single edge device (Jetson Xavier NX, 16 GB shared RAM); real-time
video-derived messages; some messages (events/alerts) benefit from persistence,
most (frames/tracks) are high-rate and ephemeral.

## Options considered

### Option A — Redis (pub/sub + streams)
- Pros: mature tooling; built-in persistence / Redis Streams for the event log;
  easy to inspect; one broker serves bus + cache + event store; Slack/dashboard
  can read history.
- Cons: a broker process consuming RAM on a constrained device; pub/sub copy
  overhead at high frame rates; another service to supervise.

### Option B — ZeroMQ
- Pros: brokerless, lower latency, lean on the edge; good for high-rate
  ephemeral frame/track traffic.
- Cons: no built-in persistence (event store must be separate); more wiring/
  topology management in our code; harder to inspect live.

### Option C — Hybrid (chosen)
Split the contract **by durability** — which `bus/topics.py` and `bus/schemas.py`
already do cleanly:
- **Ephemeral tier → ZeroMQ** (`bus/zeromq_bus.py`): `capture.frame`,
  `capture.depth`, `infer.detection`, `infer.track`, `infer.pose`,
  `fusion.depth_bbox`. These carry numpy image/depth arrays at frame rate;
  drop-on-overload (HWM / `ZMQ_CONFLATE`) is the *correct* real-time behavior.
- **Durable tier → a real EventStore** (`output/store.py`): `fusion.count`,
  `fusion.health`, `fusion.event`, `output.alert`. Backed by **SQLite** for V1.
- Pros: answers *both* binding constraints at once — removes the resident-broker
  RAM + per-subscriber frame-copy tax (the pure-Redis OOM/latency hazard on a
  16 GB shared CPU/GPU box already committed to ZED + DeepStream + a ~28M-param
  FP16 TRT engine), and removes the "nowhere durable to land an Event/Alert" gap
  (the pure-ZeroMQ welfare-miss hazard). Decouples two independently reversible
  bets (hot-path transport vs persistence backend). Matches the project's
  accepted "cheap continuous + selective expensive" pattern (ADR-0002, ADR-0003).
- Cons: we own ZeroMQ topology (binders/connectors, slow-joiner, reconnection)
  and lose a single introspectable broker — bounded behind one file
  (`zeromq_bus.py`) plus a `scripts/dev/` bus-tap subscriber over the typed
  dataclasses.

## Decision

ADOPT the **hybrid (Option C)** as the V1 planning default, with final acceptance
**gated on an on-device benchmark**.

**Why hybrid over the alternatives.** It is the only option that answers both
binding constraints at once (16 GB shared-RAM broker/copy hazard *and*
durable-alert correctness). Two of three review lenses (correctness/risk,
long-term maintainability) rank it first; the delivery-speed lens ranks it second
behind Redis. Decisive tie-breakers: (1) the EventStore must be built *regardless*
of bus choice, so the hybrid does not add a subsystem — it assigns durability to
the component that already owns it; (2) it decouples two independently-reversible
bets instead of fusing them, which a tight V1 timeline favors; (3) it matches the
already-accepted hybrid patterns in ADR-0002 / ADR-0003; (4) no change to
`schemas.py` / `topics.py` — the most-reviewed contract surface is untouched.

**V1 implementation shape (lean):** implement `MessageBus` in `zeromq_bus.py`
(PUB/SUB sockets, per-lane HWM / `CONFLATE` on the frame lanes only, serialize the
`schemas.*` dataclasses); back `EventStore` with SQLite (host-testable behind a
`device`/unit marker); add a `scripts/dev/` bus-tap subscriber that prints decoded
`schemas.*` messages to recover the live-inspectability Redis would have given for
free. → follow-on slice #10 (bus (de)serialization for ZeroMQ).

**Keep the decision reversible.** `bus/base.py` stays transport-agnostic and
`redis_bus.py` remains a live stub. The on-device benchmark (the
`model-convert-benchmark` / `env-verification-sweep` sweeps ADR-0003 already
requires) measures RSS, p99 stage-handoff latency, and dropped-frame behavior
under DeepStream + ZED load. If Redis's pub/sub copy cost and a tuned,
MAXLEN-trimmed Streams broker prove negligible on this box, fall back to **Option A
(Redis)** — Redis Streams (`XADD`/`XRANGE`) map almost 1:1 onto the `EventStore`
ABC — with zero stage-code changes because everything targets the ABC.

## Consequences

- `bus/base.py` stays genuinely transport-agnostic; `redis_bus.py` and
  `zeromq_bus.py` both remain valid implementations of the same ABC. The hybrid
  does **not** privilege a transport in stage code — stages publish/subscribe by
  topic only.
- **`bus/topics.py` and `bus/schemas.py` are unchanged.** The V1 contract holds;
  the durability split is a routing/wiring concern, not a schema change.
- `requirements.target.txt` gains **`pyzmq`** (SQLite is stdlib, so effectively
  just `pyzmq`). No broker package is provisioned or health-checked in
  `scripts/target/`.
- `output/store.py` `EventStore` becomes the **system of record** for Event/Alert
  (at-least-once writes); `fusion/events.py` and `output/` consumers of
  `fusion.event` / `output.alert` call `store.record(...)`; Slack and the
  dashboard query the store, not the bus, for history. Welfare-critical alerts
  never depend on fire-and-forget PUB/SUB for durability.
- ZeroMQ frame lanes get explicit `ZMQ_SNDHWM` / `ZMQ_RCVHWM` (and `ZMQ_CONFLATE`
  where stale-frame drop is desired) so backpressure/loss is **deliberate, on the
  ephemeral tier only**.
- The `bus-stage-conventions` skill keeps describing pub/sub in transport-neutral
  terms; add a note that high-rate ephemeral vs durable topics wire to different
  transports.

## Revisit if

Revisit (and likely flip to Redis) if the on-device benchmark shows (a) a tuned,
MAXLEN-trimmed Redis broker's resident RSS is negligible against the inference
budget, **and** (b) pub/sub frame-copy cost does not raise p99 stage-handoff
latency or drop frames on the count-dedup / lameness paths. Also revisit if V1
adds a frame/depth consumer in a *separate process* (making zero-copy handoff
harder), or if headless-device debugging proves the ZeroMQ bus-tap insufficient
versus `redis-cli MONITOR`. If frames/depth ever need to cross a process boundary,
prefer shared-memory / GStreamer handles + metadata over either bus before
re-opening this.
