# Architecture

Overwatch is a **modular pipeline over a message bus**. Each stage is an
independent process/module that communicates only through bus topics — never by
direct import of another stage's internals. The bus message schemas
(`src/overwatch/bus/schemas.py`) and topic names (`src/overwatch/bus/topics.py`)
are **the contract**; they are the most important and most reviewed surface in
the repo.

## Pipeline overview

```
                          ┌─────────────────────── message bus ───────────────────────┐
                          │  hybrid: ZeroMQ ephemeral + SQLite EventStore durable      │
                          │  (ADR-0001; final-accept benchmark-gated)                  │
                          └────────────────────────────────────────────────────────────┘
   ┌──────────┐      ┌────────────────┐      ┌──────────────────┐      ┌──────────────┐
   │ capture  │ ───▶ │   inference    │ ───▶ │     fusion       │ ───▶ │   output     │
   │ ZED RGB  │      │ DeepStream     │      │ depth fusion,    │      │ Slack alerts │
   │ + depth  │      │ detect+track,  │      │ zone counts,     │      │ event store, │
   │          │      │ on-demand ReID,│      │ health (immob.,  │      │ operator     │
   │          │      │ pose           │      │ lameness),       │      │ dashboard    │
   │          │      │                │      │ fence-crossing   │      │              │
   └──────────┘      └────────────────┘      └──────────────────┘      └──────────────┘
```

## Stages

### capture (`src/overwatch/capture/`)
Produces synchronized RGB + depth from the ZED 2i via `pyzed`. Publishes frames
(and depth) to the bus. **Target-only** (`pyzed`). `CaptureSource` ABC in
`base.py`; `zed_source.py` is the V1 implementation. The interface does not
assume a single source — IP cameras are deferred but the seam stays open.

### inference (`src/overwatch/inference/`)
The continuous load runs as a **hardware-accelerated DeepStream/GStreamer
pipeline**: `decode → nvinfer (detection) → nvtracker (tracking)`. On top of
that:
- **On-demand ReID** (`reid/megadescriptor.py`): the tracker fires a
  MegaDescriptor-T-224 (Swin-Tiny, ~28M params) embedding **only when a track
  needs identity** — not per frame. FP16 TensorRT. Implemented via a GStreamer
  **probe** (see `deepstream/probes.py` and
  [DECISIONS/0003](DECISIONS/0003-ondemand-reid-trigger.md)).
  V1 produces embeddings but has **no gallery to match against** — enrollment is
  V2 (`reid/gallery.py` is a stub).
- **pose** (`pose.py`): pose estimation feeding health signals (lameness).

### fusion (`src/overwatch/fusion/`)
The logic layer. This is where **ZED depth is fused into the otherwise
2D-bbox-centric DeepStream metadata** (`depth_fusion.py`) — the hybrid
integration seam (see [DECISIONS/0002](DECISIONS/0002-zed-deepstream-integration.md)).
Depth-aware logic:
- `zone_counting.py` — counts with depth-based de-duplication.
- `health.py` — immobility detection, lameness scoring (depth + pose).
- `events.py` — fence-crossing and other rules → `Alert` messages.

### output (`src/overwatch/output/`)
- `slack.py` — real-time Slack alerts.
- `store.py` — time-series / event store interface (logging + dashboard backing).
- `dashboard/` — on-site operator screen (interface stub in V1).

## Why depth is the differentiator

A plain 2D detector double-counts overlapping animals, can't use body size for
ID, and can't measure gait. ZED depth gives:
- **Counting de-duplication** — separate animals at different ranges that overlap in 2D.
- **Body-size ID signal** — a coarse but RFID-free identity cue.
- **Lameness scoring** — gait/posture asymmetry from depth + pose over time.

Because DeepStream metadata has no native per-object depth, preserving depth as
a first-class signal is a deliberate design choice, handled in `fusion/`.

## Message bus

Stages are decoupled through the bus. ADR-0001 is **accepted (hybrid)**: a
**ZeroMQ** ephemeral tier for high-rate frame/track lanes plus a **SQLite
EventStore** durable tier for Events/Alerts, the V1 default — with a **pending
final-accept benchmark gate** (on-device RSS / p99 latency / frame-drop) before
the hybrid is locked. See
[DECISIONS/0001-message-bus-choice.md](DECISIONS/0001-message-bus-choice.md).
`bus/base.py` defines the transport-agnostic `MessageBus` ABC. To add a stage,
follow the `bus-stage-conventions` skill.

## Topic wiring state (keep current — this is how we avoid silent gaps)

A topic existing in `topics.py` does **not** mean the live pipeline produces or
consumes it. This table is the source of truth for what is actually wired vs.
deliberately deferred — update it whenever a producer/consumer lands. A V1-active
topic with a missing producer or consumer is a **bug**, not a feature; a deferred
one must name its gating issue so it can't masquerade as done.

| Topic | Producer | Consumer | State |
|---|---|---|---|
| `infer.track` | `InferenceStage` (DeepStream) | `FusionStage` | **wired (V1)** |
| `output.alert` | `FusionStage` | `OutputStage` (Slack) + `StoreStage` (durable) | **wired (V1)** |
| `fusion.count` | `FusionStage` (on change) | `StoreStage` | **wired (V1)** — published when a zone's count changes |
| `fusion.health` | `FusionStage` | `StoreStage` | **wired (V1)** |
| `fusion.event` | `FusionStage` | `StoreStage` | **wired (V1)** — also reaches the store via `Alert.source_event` |
| `capture.frame` / `capture.depth` | `CaptureStage` | — | deferred: ZED→DeepStream hybrid seam (ADR-0002, #6) + depth fusion (#9), gated on the ZED cable (#54) |
| `fusion.depth_bbox` | — | — | deferred: depth fusion (#9) |
| `infer.identity` | — | — | deferred: on-demand ReID (#17, ADR-0003) |
| `infer.pose` | — | — | deferred: V2 |
| `infer.detection` | — | — | unused: DeepStream emits tracks, not raw detections |

Note: the durable EventStore is written by `StoreStage` and bounded by
`RetentionStage`; the read-only operator dashboard (`output/dashboard/`, #18) is
served by the supervised `DashboardStage` (#110) — all three are sqlite-backend
stages wired in `app._build_stages` (the dashboard is gated by
`output.dashboard.enabled`). A standalone `server.serve(cfg)` entry remains for
running the dashboard as its own process.
