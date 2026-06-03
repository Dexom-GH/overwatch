# ADR 0006 — Multi-camera capture + stereo/mono capability split

- **Status:** Accepted
- **Date:** 2026-06-02
- **Deciders:** Product Owner (approved forward-port)

## Context

PO-approved **V2→V1 forward-port (2026-06-02)**: add **3-4 IP/RTSP non-stereo
cameras** alongside the **1 ZED 2i** → **4-5 total streams**, with mixed
overlapping + disjoint coverage. IP cameras were previously deferred to V2 (see
`HARDWARE.md`, `ROADMAP_V1_V2.md`); this ADR records their forward-port and the
firm capability split it implies.

The decision is **firm** — this ADR's job is to record it, not to deliberate.

## Options considered

### Option A — Single ZED only (status quo, V2 defers IP cameras)
- Pros: simplest; depth everywhere.
- Cons: single FOV; cannot cover a multi-pen / multi-angle farm in V1.

### Option B — ZED + 3-4 mono RTSP, single batched pipeline (chosen)
- Pros: wide coverage now; reuses one DeepStream pipeline; depth where it matters
  (ZED), 2D elsewhere; honest capability split.
- Cons: mono feeds lack depth features; cross-camera de-dup is a new problem.

## Decision

Adopt **Option B**. Specifically:

### (1) Per-source config
`CaptureConfig.source` becomes a **list of typed sources** (`zed | rtsp`), each
with a `source_id`. `rtsp` sources carry `url` / `fps`. **RTSP URLs/credentials
resolve from env (secrets), not YAML** — mirroring `SlackConfig.webhook_env` /
`StoreConfig.url_env`. (Implemented under #30.)

### (2) DeepStream multi-stream
RTSP enters via `uridecodebin` into `nvstreammux`, batched with the ZED RGB
through one detect+track pipeline; `source_id` is preserved through tracker
metadata end-to-end. (Implemented under #31 / #32.)

### (3) Per-feature stereo-only vs mono-capable matrix (CANONICAL reference)

| Feature | ZED (stereo) | RTSP (mono) |
|---|---|---|
| 2D zone counting | yes | yes |
| Depth-based count de-dup | yes | **NO** |
| Body-size ID cue | yes | **NO** |
| Lameness scoring | yes | **NO** |
| Immobility detection | yes | yes (2D) |
| Fence-crossing | yes | yes (2D image-plane) |
| On-demand ReID embedding | yes | yes |
| Cross-camera association | (own item) | (own item) |

This table is the **single canonical home** for the capability split.
`HARDWARE.md` and `ROADMAP_V1_V2.md` link here rather than duplicating it.

### (4) Cross-camera de-dup / hand-off
Handled as its **own item** — the spike in **#34**. This is where ReID embeddings
gain a **genuine V1 use without a gallery** (association by embedding similarity).

## Consequences

- **Config (#30):** `CaptureConfig` gains a typed source list; RTSP secrets via env.
- **Capture (#31):** RTSP `CaptureSource` publishes `capture.frame` with `depth=None`.
  Its OpenCV decode (GStreamer/NVDEC on-device) **deliberately coexists** with the
  DeepStream `uridecodebin` decode in #32 — the same RGB-to-bus + RGB-to-DeepStream
  hybrid ADR-0002 accepts for the ZED. The resulting per-stream **double NVDEC
  decode** is a benchmark exit criterion in #8; if the Xavier NX can't sustain it,
  the fallback is to make #32 the single decode point and tap its buffers.
- **Inference (#32):** `nvstreammux` batches ZED + N RTSP; `source_id` preserved.
- **Fusion/Output (#33):** mono 2D zone counting + Slack (no depth de-dup).
- **Cross-camera (#34):** de-dup/hand-off spike; V1 use of ReID embeddings sans gallery.
- **Throughput (#8):** Xavier NX must sustain 4-5 streams of decode + nvinfer/nvtracker
  plus on-demand ReID dispatch — now in that benchmark's scope.
- **Edits to existing slices:** #12 (per-camera zones; mono = image-plane), #16
  (stays ZED-only/single-source — the first milestone), #19 (mono 2D immobility),
  #20 (mono 2D image-plane fence), #22 (lameness stays ZED-only).
- **Docs:** `ROADMAP_V1_V2.md` and `HARDWARE.md` updated to reflect the
  forward-port and link this matrix.
- Sequence **AFTER** the ZED capture spine (#14).
- Revisit if Xavier NX cannot sustain the multi-stream load (#8 outcome) or if the
  cross-camera method (#34) is no-go.
