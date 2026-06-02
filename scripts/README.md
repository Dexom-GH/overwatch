# scripts/

Split by where they run — this mirrors the host/target distinction that runs
through the whole repo (see CLAUDE.md, docs/SOFTWARE_STACK.md).

## `target/` — Jetson / Ubuntu / bash (LF line endings, enforced by .gitattributes)

Provisioning, run **in order** on the device:

| Script | Purpose |
|---|---|
| `00_verify_jetpack.sh` | Verify the flashed base (L4T 35.6.4, CUDA 11.4, TRT 8.5) before installing. |
| `10_install_zed_sdk.sh` | Install the ZED SDK — **FIRST** (guards against torch-before-ZED). |
| `20_install_pytorch.sh` | Install the NVIDIA Jetson PyTorch wheel — **AFTER** ZED (guards on pyzed). |
| `30_verify_env.sh` | Import pyzed/torch/tensorrt and report versions (used by the env-verification-sweep workflow). |
| `40_convert_megadescriptor.sh` | Swin → ONNX → TensorRT FP16 engine into `models/`. |

The build-order rationale lives in docs/SOFTWARE_STACK.md; the step-by-step
procedure is the `jetson-env-setup` skill.

## `dev/` — Windows host / PowerShell

| Script | Purpose |
|---|---|
| `lint.ps1` | `ruff check` + `mypy` over `src`/`tests`. |
| `format.ps1` | `ruff format` + `ruff check --fix`. |
