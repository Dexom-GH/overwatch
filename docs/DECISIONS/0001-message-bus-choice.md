# ADR 0001 — Message bus: Redis vs ZeroMQ

- **Status:** Proposed (OPEN)
- **Date:** 2026-06-02
- **Deciders:** project owner

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

## Decision

OPEN — no option chosen yet. Revisit once on-device message rates and the event
store design are concrete. A likely outcome is a hybrid (ZeroMQ for high-rate
ephemeral, a persistent store for events) — if so, record it here and update
`bus/`.

## Consequences

- `bus/base.py` must stay genuinely transport-agnostic until this closes.
- `requirements.target.txt` gains either `redis` or `pyzmq` (or both) when decided.
- The `bus-stage-conventions` skill describes pub/sub in transport-neutral terms.
