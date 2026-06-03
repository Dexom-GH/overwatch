# capture stage

Produces synchronized RGB + depth from the sensor and publishes to the bus.

- `base.py` — `CaptureSource` ABC (multi-source-capable; depth optional).
- `zed_source.py` — V1 ZED 2i source. **Target-only** (`pyzed`); import-guarded.

## The ZED ↔ DeepStream seam (ADR-0002)

DeepStream expects standard GStreamer sources; the ZED delivers RGB+depth via
`pyzed`. V1 uses the **hybrid** approach (decided in
[../../../docs/DECISIONS/0002-zed-deepstream-integration.md](../../../docs/DECISIONS/0002-zed-deepstream-integration.md)):

- **RGB** → fed to the DeepStream pipeline (`inference/deepstream/`) for
  detection + tracking.
- **Depth** → published on `topics.CAPTURE_DEPTH` and fused into 2D detections
  in `fusion/depth_fusion.py`, keyed by `frame_id`.

The custom-GStreamer-source alternative (depth as first-class pipeline metadata)
is kept open as the likely V2 evolution. If you build it, mark it `# V2→V1:` and
update ADR-0002.

## Record / replay harness (#11)

`recording.py` lets the depth/fusion/counting/health stack iterate **offline,
without a live camera** — high leverage while on-device/livestock time is scarce.

- `FrameRecorder` — writes `(Frame, DepthFrame?)` pairs to an `.owrec` file.
  Records *any* source's frames, so recording a live ZED is just feeding it
  `ZedSource` output (the only target-only path).
- `ReplaySource` — a **`CaptureSource`** that replays a clip; a drop-in for
  `ZedSource`, so fusion/counting/health consume a replay identically to live.
- `replay_to_bus(path, bus)` — republishes a clip on `capture.frame` /
  `capture.depth` in order, for on-device full-pipeline replay.

**Format (`.owrec`).** A recording is `Frame`/`DepthFrame` serialized with the
**same `bus.serialization` codec used on the wire**, so it round-trips
byte-for-byte and adds *no new schema or topic*. The container only frames those
bytes:

```
b"OWREC\x01"                      # magic + format version
repeated record:
    topic   : uint16 length + utf-8     # "capture.frame" | "capture.depth"
    nframes : uint32                     # serialization frame count
    frame*  : uint32 length + bytes
```

`capture.depth` records group with the immediately preceding `capture.frame` of
the same `frame_id` on replay (depth stays optional, alignment stays skew-free).
`recording.py` is host-safe (numpy + the bus codec only); synthetic-clip
record/replay is unit-tested off-device, and round-trips on-device under the
Jetson's Python 3.8. Recording a *real* ZED clip is deferred until a camera is
connected (the ZED needs USB 3.0).
