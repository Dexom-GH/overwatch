# ADR 0008 — Dashboard streaming surface (live operator feed)

- **Status:**
  - **Client architecture — Accepted** (decided by the project owner; see Decision).
  - **Transport / overlay-draw / bus path — Proposed (OPEN)** — deferred to the
    S1 spike (#119), which is perf-driven.
- **Date:** 2026-06-07
- **Deciders:** project owner

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

This **fully supersedes the #18 dashboard surface**:

> **#18 read-only static-HTML event table with `<meta refresh>` (no JS, no build)
> → SPA operator console served as a static build artifact, backed by a live
> data/stream API.**

It also supersedes the narrow **"vanilla-JS only / no SPA / no build step"**
amendment previously recorded for #18 — that amendment is **dropped entirely**.

### Transport / overlay-draw location / bus path — OPEN

**Deferred to the S1 spike (#119)**, which measures fps / CPU / GPU delta against
the #84 fakesink baseline (reusing the #8 / #50 sweep tooling) and, from those
numbers, picks:

- the **transport** (simplest one meeting the ~1–3 s bar on Xavier NX),
- the **overlay-draw location** (burned-in vs client-canvas), and
- the **bus path** (whether `infer.frame_annotated` is added — flagged for
  contract review).

**ADR-0001 note:** live frames live on the **ephemeral ZeroMQ tier only**, never
the durable SQLite EventStore. Whether a new `infer.frame_annotated` topic/schema
is added is part of the S1 bus-path decision and goes to contract review.

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

## Consequences

- A **scaffolding chore** stands up the SPA toolchain + CI build job and shifts
  the backend to serving `dist/` + JSON data endpoints + the stream/push
  channels. It is the foundational step that the dashboard slices (S2 feed #120,
  S3 console shell / alerts + info panel #121, S4 overlays #122) build on.
- The **deploy path** (`scripts/target/deploy.sh`) must ship the CI-built `dist/`
  to the Jetson alongside the Python package; no Node is provisioned on-device.
- Transport / overlay / bus consequences are **filled in once S1 (#119) decides** —
  they shape the dashboard server's stream endpoint, the DeepStream pipeline tap
  point (a tee/branch, **not** the inference branch), and possibly a new bus
  topic/schema if client-canvas is chosen. The overlay-draw choice gates whether
  the S4 client-canvas slice (#122) proceeds in V1 or converts to a `v2-fwd`
  candidate.
