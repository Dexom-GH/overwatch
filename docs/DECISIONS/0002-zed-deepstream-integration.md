# ADR 0002 — ZED ↔ DeepStream integration

- **Status:** Accepted (hybrid for V1) — custom-source path kept open
- **Date:** 2026-06-02
- **Deciders:** project owner

## Context

DeepStream expects standard GStreamer sources (RTSP / V4L2 / CSI). The ZED SDK
delivers **synchronized RGB + depth + point cloud** via `pyzed`, not as a
standard source. DeepStream's metadata model is **2D-bbox-centric with no native
per-object depth**. Depth is this project's core differentiator (counting
de-dup, body-size ID, lameness), so it must survive as a first-class signal.

Two ways to reconcile these.

## Options considered

### Option A — Hybrid (CHOSEN for V1)
DeepStream handles detection + tracking on the ZED RGB stream. ZED depth is
pulled in parallel via `pyzed` and **fused into the 2D detections in the logic
layer** (`fusion/depth_fusion.py`), keyed by bbox / track.
- Pros: lower risk; no custom GStreamer element to build/maintain; depth logic
  lives in plain Python where it's easy to iterate; gets V1 moving fastest.
- Cons: requires reliable spatial+temporal alignment of the depth frame to the
  DeepStream bboxes in our code; depth isn't "inside" the DeepStream metadata.

### Option B — Custom GStreamer source element
Build a custom source element that brings ZED RGB (and ideally depth) into the
DeepStream pipeline as first-class buffers/metadata.
- Pros: cleanest long-term; depth flows natively with frames; single pipeline.
- Cons: higher effort/risk; GStreamer element development against DeepStream's
  metadata model; slower path to a working V1.

## Decision

**Use the hybrid (Option A) for V1.** DeepStream does detect+track; ZED depth is
fused in `fusion/depth_fusion.py`. The custom-source path (Option B) is **kept
open** as the likely V2 evolution if alignment overhead or accuracy in the
hybrid proves limiting.

## Consequences

- `capture/` delivers RGB to DeepStream and depth to the bus/fusion layer.
- `fusion/depth_fusion.py` owns the alignment of depth → 2D bboxes; this is the
  hybrid seam and the place to watch for accuracy/latency issues.
- `capture/README.md` documents the seam and points here.
- Revisit (toward Option B) if depth↔bbox alignment cost or error is too high,
  or if the custom-source work is pulled forward (`# V2→V1:`).
