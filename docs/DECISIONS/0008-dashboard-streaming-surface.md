# ADR 0008 — Dashboard streaming surface (live operator feed)

- **Status: Accepted.**
  - **Client architecture** — Accepted (project owner; see Decision).
  - **Transport / overlay-draw / bus path** — Accepted, resolved by the **#119**
    perf spike (2026-06-07) on measured Xavier NX numbers (see Decision +
    Measurements).
- **Date:** 2026-06-07
- **Deciders:** project owner; #119 spike (perf-driven)

## Context

The V1 operator dashboard (#18) shipped as a **read-only static-HTML event table
with `<meta refresh>`** — deliberately no JavaScript, no build step, to keep a
build toolchain (Node / `node_modules`) off the headless Jetson edge device. We
now want both:

1. a **live camera feed with detection context** so the on-site operator can see
   what the system sees, and
2. a **UI that looks good** — a real operator console, not an auto-refreshing
   table.

The second requirement forces the **client-architecture** question (decided
here). The live feed forces three **coupled, perf-driven** questions (deferred to
the S1 spike #119) that bear on the bus contract, the DeepStream pipeline, and
the dashboard server:

1. **Transport** — how live frames reach the browser on a Xavier NX without
   threatening inference throughput. Target bar: **~1–3 s latency is acceptable**
   (sub-second / WebRTC is explicitly *not* required for V1). On-site LAN, **no
   auth** in V1.
2. **Overlay-draw location** — where detection overlays (boxes / IDs / zone
   polygons / fence lines) are drawn:
   - **burned-in** (e.g. `nvdsosd` composites overlays into the video on-device,
     simplest client), vs
   - **client-canvas** (frames stream "clean"; the browser draws overlays from
     live detection metadata, enabling toggles / interactivity but requiring a
     metadata path to the client).
3. **Bus path** — frames are an **ephemeral** signal. Per ADR-0001 they belong on
   the ZeroMQ ephemeral tier only (never the durable SQLite EventStore). Open
   question for **contract review**: does a new `infer.frame_annotated` topic +
   schema get added, or are annotated frames carried out-of-band of the bus
   schemas? Whether client-canvas overlays require a separate metadata topic is
   tied to question 2.

Transport / overlay / bus choices trade transport complexity, on-device cost, and
client interactivity against each other and **must be made on measured Xavier NX
perf numbers**, not by guessing — hence they stay OPEN under #119. The
client-architecture choice is **not** perf-gated (the build runs off-device), so
it is decided now.

## Decision

### Client architecture — ACCEPTED

The operator console is a **single-page application (SPA)** with a **build step**,
**built in CI / on a build host** and **shipped to the Jetson as a static `dist/`
bundle** that the backend serves as static assets.

- A real SPA and a build toolchain are **allowed**. The driver is UI quality
  ("we want a UI that looks good").
- **Invariant preserved (the original #18 concern):** the **Jetson never runs the
  frontend build, never gets Node / `node_modules`, and the inference pipeline is
  never involved in the build.** The build is a **CI / host artifact**;
  on-device startup serves a prebuilt `dist/`.
  - **Explicitly rejected:** building the SPA on-device at startup (that would put
    Node on the Jetson — the very liability #18 avoids).
- The backend **stops emitting HTML** and instead serves: the static SPA `dist/`
  bundle, **JSON data endpoints** (read from the EventStore), the **MJPEG /
  stream endpoint** (transport TBD by S1), and a **live-alert push channel**
  (SSE / WebSocket; exact transport TBD by S1).
- A **lightweight pure-Python web framework (FastAPI / Flask) is permitted** —
  negligible footprint next to the torch / TensorRT / DeepStream stack.
- **Host / target split survives:** backend logic stays host-runnable and
  unit-tested, `import overwatch` is unaffected, and the SPA has its **own
  host-side toolchain** (it never imports the `overwatch` package or touches
  target-only deps).

**Frameworks chosen (implemented in #124):** frontend **React + Vite + TypeScript**;
backend **FastAPI + uvicorn** (pure-Python, pinned `<0.116` / `<0.34` to stay on the
Jetson's Python 3.8). The backend exposes `GET /api/state` + `/api/health` and serves
the prebuilt SPA `dist/`. Transport + overlay-draw for the live feed were **resolved
by #119** (below): **throttled MJPEG** + **burned-in `nvdsosd`**.

This **fully supersedes the #18 dashboard surface**:

> **#18 read-only static-HTML event table with `<meta refresh>` (no JS, no build)
> → SPA operator console served as a static build artifact, backed by a live
> data/stream API.**

It also supersedes the narrow **"vanilla-JS only / no SPA / no build step"**
amendment previously recorded for #18 — that amendment is **dropped entirely**.

### Transport / overlay-draw location / bus path — ACCEPTED (resolved by #119, 2026-06-07)

Resolved on measured Xavier NX numbers (see **Measurements**). The production
source is a live camera at **≤25 fps** (ADR-0006 mono RTSP); the spike measured
**max-throughput headroom** (720p file at `sync=0`) to size the tap's cost.

- **(a) Transport — MJPEG-over-HTTP** (multipart `x-mixed-replace`). It meets the
  ~1–3 s latency bar (in fact near-realtime), the JPEG encode is ~free (clean
  encode cost ≈ 0.7 % fps), and it is served directly by the existing FastAPI
  backend as a streaming response — **no HLS segmenting, no WebRTC**. The served
  stream is **throttled to a few fps** (monitoring does not need 35 fps), keeping
  bandwidth/CPU low.
- **(b) Overlay-draw — burned-in `nvdsosd`** (on-GPU). Both options clear the
  budget at camera rates, so V1 takes the **simplest** path: `nvdsosd` composites
  boxes / labels / track-ids into the frame, `nvjpegenc` encodes, and the SPA shows
  it as a plain MJPEG `<img>` — **no metadata channel, no frame/metadata sync, no
  client overlay engine**. Its ~6 fps cost is immaterial at ≤25 fps. **Consequence:
  the client-canvas slice (S4 / #122) is deferred to V2 (`v2-fwd`)** — its only edge
  (near-free pipeline cost + toggleable overlays) buys V1 nothing once burned-in
  already fits; it matters in V2 for interactivity.
- **(c) Bus path — in-process latest-frame slot; NO new bus topic.** The pipeline
  (`InferenceStage`) and the dashboard (`DashboardStage`) are threads in the **one**
  `app.py` process, so the annotated JPEG passes via a thread-safe latest-frame slot
  — frames **never touch the ZeroMQ bus, and never the SQLite store**. Lighter than
  a bus topic and keeps the contract clean. **Contract-review flag: no
  `bus/schemas.py` / `bus/topics.py` change in V1** (no `infer.frame_annotated`
  topic). Revisit only if V2 needs multi-process / remote / multi-stream.
- **Tap point — a `tee` branch after `nvtracker`, never the inference branch** (the
  feed queue is **leaky**, so it drops under pressure and cannot backpressure
  inference). This is the binding constraint for the S2 feed slice (#120).

**ADR-0001 note:** confirmed — live frames are an **ephemeral** signal kept **off
the bus entirely** (in-process slot), and a fortiori off the durable SQLite
EventStore. No new topic/schema; **nothing for contract review to change in V1**.

## Options considered

### Client architecture (decided)
- **Keep #18's static HTML + `<meta refresh>`** — zero build, but cannot deliver a
  good-looking console; rejected.
- **Vanilla JS over the existing HTML (no build)** — the earlier narrow amendment;
  avoids a build toolchain but caps UI quality and ergonomics; **dropped** in
  favour of a real SPA.
- **SPA built on-device at startup** — would place Node / `node_modules` on the
  Jetson; **rejected** (reintroduces the #18 liability).
- **SPA built in CI, served as a static `dist/` artifact** — **ACCEPTED.** Real
  SPA + good UI, with no build toolchain on the device.

### Transport — candidates to measure (S1)
- **MJPEG over HTTP** — simplest; per-frame JPEG; higher bandwidth.
- **HLS** — segmented; ~few-second latency; more server-side machinery.
- (WebRTC — out of scope for V1; sub-second not required.)

### Overlay-draw location — candidates (S1)
- **Burned-in (`nvdsosd`)** — overlays composited on-device; trivial client; no
  metadata path to the browser; no client-side toggles.
- **Client-canvas** — clean frames + a live metadata path; browser draws boxes /
  IDs / zone polygons / fence lines; enables toggles; needs a metadata transport
  and a contract decision (bus-path question).

## Measurements (#119, 2026-06-07)

On-device: Jetson Xavier NX, JetPack 5.1.x / DeepStream 6.x, stock-YOLOv8n FP16
detector + NvDCF tracker, 720p H.264 sample at `sync=0` (max throughput — so the
fps gap is pure GPU-headroom contention; a live camera caps at ≤25 fps). Harness:
[`scripts/dev/bench_feed_tap.py`](../../scripts/dev/bench_feed_tap.py); GPU% via
`tegrastats`. The feed branch is a **leaky `tee`** off the `nvtracker` src pad, so
it cannot backpressure inference.

| variant | inference fps | feed fps | GPU % |
|---|---|---|---|
| baseline (no feed) | 41.1–41.7 | — | 66 |
| **burned-in `nvdsosd` → `nvjpegenc`** | **35.4** | 35.0 | 61 |
| clean encode `nvjpegenc` (client-canvas) | 40.8 | 40.8 | 64 |

Reading: the JPEG **encode is ~free** (−0.7 %); the cost of burned-in overlays is
the **`nvdsosd` compositing** (−14 % at max throughput). Both clear a ≤25 fps camera
with margin (35 fps ≫ 25 fps), so **neither drops inference frames at production
rates** — burned-in wins on simplicity. (Power rails were not exposed by
`tegrastats` on this unit; the sustained power-mode question is tracked separately
in **#46** — note CPU ran with 2 of 6 cores shed during the sweep.)

## Consequences

- A **scaffolding chore** stands up the SPA toolchain + CI build job and shifts
  the backend to serving `dist/` + JSON data endpoints + the stream/push
  channels. It is the foundational step that the dashboard slices (S2 feed #120,
  S3 console shell / alerts + info panel #121, S4 overlays #122) build on.
- The **deploy path** (`scripts/target/deploy.sh`) must ship the CI-built `dist/`
  to the Jetson alongside the Python package; no Node is provisioned on-device.
- **Transport / overlay / bus consequences (resolved by #119):** the S2 feed slice
  (#120) taps a `tee` after `nvtracker` → `nvdsosd` → `nvjpegenc` → an in-process
  latest-frame slot, served as **throttled MJPEG** by the FastAPI backend and shown
  as an `<img>` in the SPA. **No `bus/schemas.py` / `bus/topics.py` change.** The
  **client-canvas slice (S4 / #122) converts to `v2-fwd`** (burned-in chosen). The
  scaffolding (#124) and console shell (#121) are **done**; #120 is unblocked.
