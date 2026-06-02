# DeepStream element configs

`nvinfer` and `nvtracker` are configured by `.txt` files referenced when the
pipeline is built (`pipeline.py`). Place them here, e.g.:

- `nvinfer_detector.txt` — detector engine path, net params, class file.
- `nvtracker.txt` — tracker library + tracker config (e.g. NvDCF).

These reference TensorRT engines under `models/` (gitignored; produced on
device via the `trt-model-conversion` skill). Keep the configs committed; keep
the engines out of git.

Not yet authored — added when the detector/tracker models are chosen and
converted on device.
