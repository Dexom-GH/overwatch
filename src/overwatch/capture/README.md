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

### Gotcha: the ZED needs USB 3.0

The ZED 2i **must** be on a USB-3.x port (a USB-3 cable, no intermediate USB-2
hub). On a USB-2.0 link the SDK reports `CAMERA NOT DETECTED` with an empty
`get_device_list()` even though the camera still enumerates as a UVC device in
`lsusb`. Check the link speed with `lsusb -t` — the ZED's `Video` interfaces
must show `5000M`/`10000M`, not `480M`. (Spike #6, recorded in ADR-0002.)
