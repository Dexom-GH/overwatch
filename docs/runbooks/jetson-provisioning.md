# Runbook: Jetson env provisioning (issue #3)

Bring `jetson-agent` (Xavier NX, JetPack 5.1.6) from the NVIDIA base to a full
Overwatch target env in the load-bearing order **ZED SDK → PyTorch → verify**.
The scripts live in `scripts/target/`. Some steps need `sudo` (a human runs
those); the rest are non-root and can run over SSH.

## Verified starting state (2026-06-02)
- Present: CUDA 11.4, cuDNN 8.6, TensorRT 8.5.2, DeepStream 6.3 core, Python 3.8.10, numpy 1.17.4.
- Missing: ZED SDK (`pyzed`), PyTorch, `pyds` (DeepStream Python bindings), `pip`.
- Internet egress works (PyPI / GitHub reachable). `sudo` requires a password.

## 0. Get the scripts onto the device
From a machine with the repo + egress (e.g. the dev host), copy the target scripts:
```
scp -r scripts/target jetson-agent:~/overwatch-target
```
(Or `git clone` the repo on the device.) Run the steps below from that directory.

## 1. Verify the base (no root)
```
bash ~/overwatch-target/00_verify_jetpack.sh
```
Expect CUDA 11.4 / Python 3.8 / TensorRT 8.5 / DeepStream OK. Fix any FAIL first.

## 2. Bootstrap pip (no root)
The system python3 has no pip. Needed by both the ZED Python API and the torch install.
```
wget -qO /tmp/get-pip.py https://bootstrap.pypa.io/pip/3.8/get-pip.py
python3 /tmp/get-pip.py --user
python3 -m pip --version
```
(If bootstrap.pypa.io is blocked: `sudo apt-get update && sudo apt-get install -y python3-pip`.)

## 3. Install the ZED SDK — **FIRST**, needs sudo + EULA (human runs this)
1. On a browser, download the installer matching **L4T 35.6 / JetPack 5.1.x** (a 4.x SDK)
   from https://www.stereolabs.com/developers/release/ — file like
   `ZED_SDK_Tegra_L4T35.x_vY.Y.Y.zstd.run`.
2. Copy it next to the scripts (or note its path):
   `scp ZED_SDK_Tegra_L4T35.x_vY.Y.Y.zstd.run jetson-agent:~/overwatch-target/`
3. Run:
   ```
   bash ~/overwatch-target/10_install_zed_sdk.sh
   ```
   It runs the installer with `sudo ... -- silent skip_cuda skip_hub skip_tools` and
   verifies `import pyzed.sl`. You may be prompted for the sudo password and to accept
   the Stereolabs EULA.

## 4. Install PyTorch — **AFTER** ZED SDK (no root)
```
bash ~/overwatch-target/20_install_pytorch.sh
```
Installs the pinned Jetson torch 2.1 + torchvision wheels (cp38) into the user site and
prints `torch.cuda.is_available()` (must be `True`).

## 5. Verify the full env (no root)
```
bash ~/overwatch-target/30_verify_env.sh
```
Reports `pyzed`, `torch` (cuda True), `tensorrt`, `pyds`, and `import overwatch`.

> NOTE: `pyds` (DeepStream Python bindings) and `import overwatch` (needs the repo
> installed: `python3 -m pip install --user -e .` from the repo root) are required for
> #3's FULL exit criteria and for #6 (ZED→DeepStream), but are NOT needed for #7
> (Swin→TRT, which needs only torch + TensorRT). Install `pyds` from the DeepStream
> 6.3 Python bindings if pursuing #6.

## Who runs what
- **Human (sudo):** step 3 (ZED SDK) — and step 2's apt fallback if used.
- **Agent over SSH (no root):** steps 1, 2 (get-pip), 4 (torch), 5 (verify).
