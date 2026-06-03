# ADR 0002 — ZED ↔ DeepStream integration

- **Status:** Accepted (hybrid for V1) — custom-source path kept open. Spike #6
  confirms the hybrid on *design + cost*; on-device *accuracy* sign-off pending
  (see findings below).
- **Date:** 2026-06-02 (spike #6 findings appended 2026-06-03)
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

## Spike #6 findings (2026-06-03)

Spike #6 de-risked the hybrid seam. The **design and cost** are confirmed; the
**accuracy** half is blocked on hardware and is recorded as the remaining
on-device sign-off (issue #6 stays open).

**What was confirmed (host + on-device, no camera needed):**

1. **Temporal sync strategy — settled.** ZED RGB and depth come from the *same*
   `grab()` and share a `frame_id` (`capture/zed_source.py`). Fusion therefore
   joins strictly on `frame_id` — **zero temporal skew by construction**, no
   nearest-timestamp matching. `DepthFusion.fuse` raises on a `frame_id`
   mismatch so a wiring bug fails loudly rather than silently aligning the wrong
   depth. *Implication for #15:* the capture `frame_id` must survive the
   DeepStream leg so the tracker-pad probe can stamp each `Track` with it (carry
   it on buffer PTS / a frame-index→frame_id stash at the appsrc feed point).
2. **Spatial sampling — implemented + host-tested.** Per-object depth is the
   **median of valid pixels in the central `inner_fraction` (default 0.6) of the
   bbox**, rejecting the ZED's holes (`0` / `NaN` / `±inf`) and out-of-window
   depths. Sampling the box *core* (not corners) avoids background bleed; a unit
   test demonstrates a 2 m animal in a box whose edges see 10 m background reads
   2 m at `inner_fraction=0.6` vs 10 m at `1.0`. A coarse, *relative* body-size
   cue (`apparent_px · depth_m`) is emitted as `DepthBBox.size_estimate`
   (metric calibration deferred to #12). **No bus-contract change** — `DepthBBox`
   already existed.
3. **Per-frame alignment cost — measured on the actual Xavier NX** (aarch64,
   Python 3.8, numpy 1.24.4; single CPU thread, off the GPU/DeepStream path):

   | boxes | ms/frame | µs/box |
   |------:|---------:|-------:|
   | 1     | 0.72     | 716    |
   | 5     | 3.40     | 681    |
   | 10    | 6.29     | 629    |
   | 20    | 10.9     | 546    |

   Against the V1 budget (15 FPS → 66.7 ms/frame), a realistic 10–20-animal
   frame costs **~9–16% of the budget** on one CPU thread. **Alignment cost is
   not a threat to the hybrid.** (Host x86 ran ~13× faster; the device numbers
   are the ones that matter — reproduce with `scripts/dev/bench_depth_fusion.py`.)
   *Scaling watch:* cost is ~linear in box count; the multi-cam forward-port
   (#32/#34) multiplies boxes (e.g. ~100 boxes ≈ ~55 ms, nearing budget). If hit,
   vectorize the per-box median or move fusion off the critical thread before
   escalating to Option B.

**What is NOT yet confirmed — remaining on-device exit criteria for #6:**

- **Spatial alignment _error_ on a real scene** (depth-pixel ↔ DeepStream-RGB-bbox
  registration) — needs a live ZED + a calibrated target. Ready-to-run sign-off:
  `tests/device/test_depth_fusion_device.py` (`-m zed`).
- **A running DeepStream pipeline with a probe reading bbox metadata** — gated on
  the YOLOv8 detector engine, which is **not built on-device yet** (issue **#49**).

**Blocker uncovered (operational, worth recording):** the ZED 2i on the bench is
cabled to a **USB 2.0** path — `lsusb -t` shows it enumerated at `480M` behind
cascaded USB-2 hubs on the 480M root hub, while the Jetson's USB-3.1 controller
(`10000M`, Bus 02) carries nothing. The ZED SDK **requires USB 3.0** and reports
`CAMERA NOT DETECTED` / an empty `get_device_list()` on a 480M link even though
the camera enumerates as a UVC device. **Fix: move the ZED to the Jetson's
USB-3.x port with a USB-3 cable and no intermediate USB-2 hub.** This gates *all*
live ZED capture (#14 too), not just this spike.

**Verdict:** hybrid (Option A) is **confirmed on design + cost**; no drift toward
Option B. Final accuracy accept pends the two on-device items above.
