---
name: deepstream-pipeline
description: Use when building, modifying, or probing the Overwatch DeepStream/GStreamer pipeline on the Jetson — the decode->nvinfer->nvtracker graph, nvinfer/nvtracker config wiring, the probe-callback pattern for firing on-demand ReID, and feeding ZED depth into the hybrid fusion seam.
---

# DeepStream pipeline & probes

Build and extend the continuous-load GStreamer pipeline that does detection +
tracking, and hook in the work that sits outside the per-frame path (on-demand
ReID, depth alignment). DeepStream is target-only. Read ADR-0002 (ZED hybrid)
and ADR-0003 (on-demand ReID) first.

## Pipeline shape

```
source(ZED RGB) -> nvstreammux -> nvinfer(detector) -> nvtracker -> sink
                                       |                    |
                                  (engine from           (track IDs)
                                   trt-model-conversion)
```

Code: `src/overwatch/inference/deepstream/pipeline.py` (graph) +
`probes.py` (callbacks). Element configs: `inference/deepstream/configs/*.txt`.

Per ADR-0002 **hybrid**: ZED **RGB** feeds this pipeline; ZED **depth** is *not*
an element here — it goes to the bus and is fused in `fusion/depth_fusion.py`.
The custom-source alternative (depth as native metadata) is the open escalation
path — if you build it, mark `# V2->V1:` and update ADR-0002.

## Configuring nvinfer / nvtracker

- `nvinfer` config (`.txt`): points at the TensorRT engine (`models/...engine`),
  net input/preproc params, and the class label file. Engine comes from the
  `trt-model-conversion` skill.
- `nvtracker` config: tracker library + tracker config (e.g. NvDCF). Tune for
  the herd's motion once on-device.
- Keep configs committed; keep engines out of git (rebuilt on device).

## Probe pattern (the important part)

Probes attach to a pad and inspect/modify buffers + metadata as they flow. We
use them for two things:

1. **On-demand ReID trigger (ADR-0003)** — probe on the tracker src pad
   (`probes.on_tracker_src_pad`):
   - iterate batch/frame/object metadata (`pyds`),
   - ask the trigger policy which tracks `needs_identity` (new / stale / periodic
     / crop-quality),
   - **dispatch the crop to the MegaDescriptor engine OFF the streaming thread**
     (queue/threadpool) — never block the probe; a slow embedding call stalls
     the whole pipeline,
   - attach the embedding to the track / publish on `topics.INFER_IDENTITY`.
2. **Depth alignment tap** — probe reads the current `frame_id` so
   `fusion/depth_fusion.py` can pick the matching ZED depth frame for the bboxes.

```python
def on_tracker_src_pad(pad, info, user_data):
    # batch_meta = pyds.gst_buffer_get_nvds_batch_meta(...)
    # for frame_meta in ...:  for obj_meta in ...:
    #     if tracker.needs_identity(track): enqueue_for_reid(crop, track)
    return Gst.PadProbeReturn.OK
```

## Common pitfalls

- **Blocking in a probe** → pipeline stall. Keep probes O(metadata); offload real
  compute.
- **`pyds` memory handling** — follow DeepStream's cast/ownership idioms when
  reading metadata; mistakes segfault.
- **Engine/config mismatch** — nvinfer net params must match the converted
  engine's input shape/precision.

## Verify

- Pipeline reaches PLAYING and processes frames on-device.
- A probe-triggered ReID produces an embedding without dropping pipeline FPS
  (measure with the `model-convert-benchmark` / a pipeline FPS check).
- `frame_id` from the depth tap aligns depth to bboxes in `depth_fusion`.
