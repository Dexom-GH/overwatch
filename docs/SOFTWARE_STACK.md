# Software Stack

The **authoritative version-pin table** and build-order rules for the Jetson
target. When a session needs to know "what version of X" or "what installs
first," this file is the single source of truth. CLAUDE.md only flags the
gotchas and points here.

## Version pin table (Jetson target)

| Component | Pinned version | Notes |
|---|---|---|
| JetPack | **5.1.6** | Latest 5.1.x for Xavier NX. **JetPack 6 is Orin-only** — do not attempt. |
| L4T | 35.6.4 | Ships with JetPack 5.1.6. |
| OS | Ubuntu 20.04 | |
| Python | **3.8** | System Python on this L4T. All target code must be 3.8-compatible. |
| CUDA | 11.4 | From the 5.1.x line. |
| cuDNN | 8.6 | |
| TensorRT | **8.5** | MegaDescriptor → TRT conversion targets this. See [DECISIONS/0003](DECISIONS/0003-ondemand-reid-trigger.md) and the `trt-model-conversion` skill. |
| DeepStream | installed | Streaming-analytics path (GStreamer: decode → nvinfer → nvtracker). |
| PyTorch | ~2.1 (NVIDIA Jetson wheel for 5.1.x) | **Install AFTER the ZED SDK** (see build order). Not pip-installable from PyPI — use the NVIDIA wheel. |
| ZED SDK | matching JetPack 5.1.x | Provides `pyzed` (Python 3.8 wheel). **Install FIRST.** |
| `pyzed` | Python 3.8 wheel from the ZED SDK | **Not installable on the Windows dev host.** Target-only. |

### Pinned install artifacts

- **PyTorch (Jetson wheel):** `torch-2.1.0a0+41361538.nv23.06-cp38-cp38-linux_aarch64.whl`
  + `torchvision-0.16.2+c6f3977-cp38-cp38-linux_aarch64.whl` (JetPack 5.1.x / cp38).
  Source: NVIDIA "PyTorch for Jetson"; mirrored on the Ultralytics assets GitHub
  release. Installed by `scripts/target/20_install_pytorch.sh`.
- **ZED SDK:** a 4.x build matching L4T 35.6 (`ZED_SDK_Tegra_L4T35.x_vY.Y.Y.zstd.run`)
  from the Stereolabs release page; silent install via `scripts/target/10_install_zed_sdk.sh`.
- **pip:** not preinstalled on this L4T's system python3; bootstrap with the py3.8
  `get-pip.py` (`--user`). See `docs/runbooks/jetson-provisioning.md`.

### Provisioned versions (verified on-device 2026-06-03)

What actually landed on the Xavier NX when #3 was provisioned (into a shared
venv — see "As actually provisioned" in `docs/runbooks/jetson-provisioning.md`):

| Component | Version |
|---|---|
| Python | 3.8.10 |
| CUDA | 11.4 |
| torch | `2.1.0a0+41361538.nv23.06` |
| torchvision | 0.16.2 |
| numpy | 1.24.4 |
| tensorrt | 8.5.2.2 |
| pyzed | 4.2 (ZED SDK 4.2.5) |
| pyds | 1.1.8 (DeepStream 6.3) |
| gi (PyGObject) | 3.36.0 |

**Deviation worth flagging:** the ZED SDK installer used was the **L4T 35.4**
build run on an **L4T 35.6** device — it worked.

## Models (detector + ReID)

The two inference models, their provenance, and licensing. Licensing is cleared
under the **non-commercial / AGPL / public** posture in
[DECISIONS/0007](DECISIONS/0007-licensing-posture.md) — under a *commercial*
posture both picks below would be blockers.

| Role | Model | Arch | Weights/code license | DeepStream path |
|---|---|---|---|---|
| **Detector** (`nvinfer`) | **Ultralytics YOLOv8** | YOLOv8 | **AGPL-3.0** (code + weights) — OK under ADR-0007 (#5) | ONNX → TRT 8.5 engine; bbox parser via `marcoslucianops/DeepStream-Yolo`. |
| **ReID** (on-demand) | **`BVRA/MegaDescriptor-T-224`** | Swin-Tiny @ 224 | **`cc-by-nc-4.0`** (non-commercial) — OK under ADR-0007 (#27) | Swin → TRT FP16 (8.5); see the `trt-model-conversion` skill + ADR-0003. |

### Detector — V1 choice (#5)

- **Class set is the 5 V1 animals** — `configs/animals.yaml` is the **single
  source of truth** for `class_id` ↔ name. The detector's label map
  (`src/overwatch/inference/deepstream/configs/labels.txt`) is generated to match
  it, in `class_id` order.
- **Custom fine-tune is required regardless of model.** COCO does **not** cover
  goat / rabbit / guinea pig, so the shipped detector is fine-tuned on our own
  data (tier-3 species data-gated in #35). This is why the **training-code**
  license (AGPL-3.0, fine) matters as much as the pretrained-weights license.
- **Why YOLOv8 over the permissive alternatives** (YOLOX/PP-YOLOE+ Apache-2.0,
  TAO-custom): once AGPL is acceptable under ADR-0007, Ultralytics is the most
  mature option with the best-maintained DeepStream `nvinfer` bbox parser and the
  simplest custom-class fine-tune. `YOLO11` is a drop-in upgrade path on the same
  parser if desired.
- **nvinfer / nvtracker wiring:** `src/overwatch/inference/deepstream/configs/`
  (`nvinfer_detector.txt`, `labels.txt`, `nvtracker.txt`), referenced from
  `configs/default.yaml` (`inference.detector_config` / `tracker_config`). The
  TRT engine lives under `models/` (gitignored; built on device).
- **NOT yet validated on device:** on-device sanity inference (engine loads in
  `nvinfer` and produces plausible detections) is target-only and deferred to the
  Jetson — see #5's remaining exit criterion.

## Build order (this order is load-bearing)

```
1. Verify JetPack/L4T/CUDA/TRT       scripts/target/00_verify_jetpack.sh
2. Install ZED SDK  (FIRST)          scripts/target/10_install_zed_sdk.sh
3. Install PyTorch  (AFTER ZED)      scripts/target/20_install_pytorch.sh
4. Verify the full env               scripts/target/30_verify_env.sh
5. Convert MegaDescriptor → TRT      scripts/target/40_convert_megadescriptor.sh
```

**Why ZED SDK before PyTorch:** the ZED SDK installer and its CUDA/dependency
expectations are cleanest against a stock JetPack environment. Installing the
Jetson PyTorch wheel first has historically perturbed the CUDA/Python
environment in ways that complicate the ZED SDK build and `pyzed` wheel
selection. Installing ZED SDK first, then layering the PyTorch wheel, is the
reliable order. The provisioning procedure is encoded in the `jetson-env-setup`
skill.

## Host vs target

- **Dev host:** Windows 11. Used for editing, unit tests of host-runnable
  logic, linting. **Never `pip install pyzed` or the Jetson torch wheel here** —
  they will not resolve.
- **Target:** Jetson / Ubuntu 20.04 / Python 3.8. Runs the real pipeline.
  Provisioned exclusively by `scripts/target/` (bash).

Target-only Python modules (`capture/zed_source.py`, the DeepStream modules,
`inference/reid/megadescriptor.py`) **must guard their imports** so that
`import overwatch` still succeeds on the host. See CLAUDE.md → Coding conventions.

## Dependency files

- `pyproject.toml` — host-installable package metadata + dev extras.
- `requirements.dev.txt` — host dev tooling (ruff, mypy, pytest).
- `requirements.target.txt` — Jetson-only wheels; installed **only** by
  `scripts/target/`, never by host pip.
