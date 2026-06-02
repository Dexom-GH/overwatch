# Glossary

Disambiguation for terms a new session (human or agent) will hit in this repo.

| Term | Meaning in this project |
|---|---|
| **ReID** (re-identification) | Recognizing that a detected animal is a specific known individual, from its appearance. Here: produced by MegaDescriptor embeddings, matched against a gallery (gallery is V2). |
| **MegaDescriptor** | MegaDescriptor-T-224 — a Swin-Tiny (~28M param) wildlife re-identification backbone from `WildlifeDatasets/wildlife-tools`. Run as FP16 TensorRT, on-demand. Produces an **embedding** per animal crop. |
| **Embedding** | A fixed-length vector describing an animal's appearance. Two crops of the same individual should have nearby embeddings. V1 generates them; matching needs a gallery (V2). |
| **Gallery** | The enrolled set of known-individual embeddings that new embeddings are matched against. **Not built in V1** (enrollment is V2). `reid/gallery.py` is a stub. |
| **nvinfer** | DeepStream's GStreamer element that runs TensorRT inference (detection) on frames. Configured via a `.txt` config. |
| **nvtracker** | DeepStream's GStreamer element that tracks detections across frames, assigning track IDs. |
| **Probe** | A callback attached to a GStreamer pad that inspects/modifies buffers and metadata as they flow. Used here to fire **on-demand ReID** when a track needs identity, outside the per-frame path. |
| **DeepStream** | NVIDIA's GStreamer-based streaming-analytics SDK. Provides the hardware-accelerated decode → nvinfer → nvtracker pipeline. Its metadata is 2D-bbox-centric (no native per-object depth). |
| **ZED depth** | Per-pixel depth from the ZED 2i stereo camera, delivered via `pyzed`. Fused into 2D detections in `fusion/depth_fusion.py`. The project's core differentiator. |
| **Point cloud** | 3D points from the ZED. Available via `pyzed`; depth map is the primary V1 use. |
| **Immobility** | A health signal: an animal not moving for an abnormal duration. Computed in `fusion/health.py`. |
| **Lameness** | A health signal: gait/posture asymmetry indicating injury. Scored from depth + pose over time in `fusion/health.py`. |
| **Fence-crossing** | An event: an animal leaving a defined zone / crossing a boundary. Rule in `fusion/events.py` → `Alert`. |
| **Message bus** | The Redis-or-ZeroMQ transport decoupling pipeline stages. Transport choice is open ([DECISIONS/0001](DECISIONS/0001-message-bus-choice.md)). |
| **Host / target** | Host = the Windows 11 dev machine. Target = the Jetson Xavier NX device. See [SOFTWARE_STACK.md](SOFTWARE_STACK.md). |
| **JetPack / L4T** | NVIDIA's Jetson software bundle (JetPack 5.1.6) / its Linux-for-Tegra base (L4T 35.6.4). |
| **`# V2→V1:`** | Code marker for functionality pulled forward from V2 into V1. See [ROADMAP_V1_V2.md](ROADMAP_V1_V2.md). |
