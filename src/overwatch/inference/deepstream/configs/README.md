# DeepStream element configs

`nvinfer` and `nvtracker` are configured by `.txt` files referenced from
`configs/default.yaml` (`inference.detector_config` / `inference.tracker_config`)
and loaded when the pipeline is built (`pipeline.py`). This directory is the
**canonical home** for them:

- `nvinfer_detector.txt` — V1 detector (**Ultralytics YOLOv8**, fine-tuned on the
  5 V1 animal classes). Engine path, net params, parser, class file.
- `labels.txt` — the class label map, **generated from `configs/animals.yaml`**
  (the class-id source of truth), one name per line in `class_id` order.
- `nvtracker.txt` — tracker library + config (NvDCF default for V1).

These reference TensorRT engines and the DeepStream-Yolo custom parser under
`models/` (gitignored; produced/installed on device — see the
`trt-model-conversion` and `deepstream-pipeline` skills). Keep the configs
committed; keep the engines/parser `.so` out of git.

**Status:** authored as on-device **stubs** (#5). Net params, paths, and tracker
tuning are finalized on the Jetson; on-device sanity inference is the remaining
#5 exit criterion (deferred to target — see `docs/SOFTWARE_STACK.md` "Models").
