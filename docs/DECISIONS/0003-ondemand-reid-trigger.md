# ADR 0003 — On-demand ReID trigger

- **Status:** Proposed
- **Date:** 2026-06-02
- **Deciders:** project owner

## Context

Individual ID uses MegaDescriptor-T-224 (Swin-Tiny, FP16 TensorRT). Running it
per frame per object is too expensive on a ~21 TOPS device under the continuous
DeepStream load. So ReID must run **on-demand** — only when a track actually
needs identity — and sit **outside DeepStream's per-frame happy path**.

The question: how and when is the embedding fired?

## Options considered

### Option A — Probe-driven trigger (proposed default)
A GStreamer **probe** on the pipeline (see `inference/deepstream/probes.py`)
watches tracks. When a track meets a trigger condition (new track, identity
stale/low-confidence, periodic refresh, or a quality gate on the crop), the
probe dispatches the crop to the MegaDescriptor TRT engine and attaches the
resulting embedding to the track.
- Pros: keeps the continuous pipeline hardware-accelerated; ReID cost is paid
  only when needed; trigger policy is tunable in one place.
- Cons: probe code runs in the streaming thread — the embedding call must be
  dispatched off-thread / batched to avoid stalling the pipeline.

### Option B — Out-of-band consumer
DeepStream publishes track crops to the bus; a separate ReID worker consumes and
fires embeddings asynchronously, publishing identities back.
- Pros: full isolation from the streaming thread; scales independently.
- Cons: more moving parts; added latency and bus traffic for crops.

## Decision

OPEN — leaning Option A (probe-driven) as the V1 default, with the embedding
call dispatched off the streaming thread. Confirm after on-device benchmarks
(see the `model-convert-benchmark` workflow) show the dispatch doesn't stall the
pipeline.

## Consequences

- `inference/deepstream/probes.py` owns the trigger policy and dispatch.
- `inference/reid/megadescriptor.py` must be callable off-thread / batchable.
- Trigger conditions (new/stale/periodic/quality) are config-driven.
- Note: V1 has **no gallery** to match embeddings against — the trigger produces
  embeddings that are stored/logged but not yet matched. Matching is V2
  ([ROADMAP_V1_V2.md](../ROADMAP_V1_V2.md)).

## Conversion findings (#7 — 2026-06-02)

An on-device spike (Xavier NX, TRT 8.5.2) validated the Swin→TRT conversion this
trigger depends on. Two findings refine the "FP16 TensorRT" assumption in the
Context above:

- The engine builds and runs: MegaDescriptor-T-224 → 768-dim embedding,
  ~16.7 ms (FP16) / ~40.6 ms (FP32) per crop on Xavier NX (measured at
  `MODE_10W_4CORE`, DVFS unpinned — a conservative lower bound). On-demand
  dispatch (off-thread) easily tolerates the FP32 figure.
- **FP16 is not safe out-of-the-box.** Pure-FP16 cosine vs FP32 is ~0.13 (Swin
  overflows FP16); the FP32 engine is exact (1.00). **V1 should ship the FP32
  engine**; recovering FP16 speed needs mixed precision (FP32 fallback on
  overflow-prone layers) — tracked with the ReID slice (#17).
- Export must use **ONNX opset 16** (opset 17's fused `LayerNormalization` is
  unparseable by TRT 8.5). See the `trt-model-conversion` skill.

This does not change the trigger decision (probe vs out-of-band) — still pending
the dispatch-stall benchmark — but it updates the engine assumptions feeding it.
