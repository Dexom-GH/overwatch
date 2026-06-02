# models/

On-device model artifacts. **Contents are gitignored** — engines are large and
device/version-specific. Only this README is committed.

## What lives here

- `megadescriptor_t224_fp16.engine` — MegaDescriptor-T-224 Swin-Tiny, FP16
  TensorRT 8.5 engine. Produced by `scripts/target/40_convert_megadescriptor.sh`
  (see the `trt-model-conversion` skill).
- Detector / tracker engines + label files referenced by the DeepStream
  `nvinfer` / `nvtracker` configs.
- Intermediate `*.onnx` exports (also gitignored).

## Why not in git

TensorRT engines are built **on the target** against its exact TRT/CUDA/driver
versions and do not transfer. Rebuild them on device rather than committing them.
Keep the *conversion procedure* (skill + script) in git; keep the *output* out.
