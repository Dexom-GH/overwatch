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

---

## As actually provisioned (2026-06-03, validated on-device)

The plan above (`scripts/target/00–30`, per-user `--user` installs) was the
*intended* flow. The real provisioning of the Xavier NX (JetPack 5.1.x /
L4T 35.6.4 / Python 3.8.10 / CUDA 11.4 / TensorRT 8.5.2 / DeepStream 6.3)
diverged substantially. This section records **what was actually done and
verified**, including the gotchas. Where it conflicts with the steps above,
**this section is the ground truth** for #3.

> **FUTURE / productionization (read this first):** the manual, two-account,
> shared-venv setup below is a **dev-phase one-off**. After the development
> phase it should be replaced by a **single installable artifact for a fresh
> edge device** — a `.deb`, container image, or one-shot installer bundling the
> pinned wheels + ZED SDK + pyds + the overwatch package — so a fresh device is
> provisioned with **no manual steps**. Tracked as a follow-up issue.

### Two-account / shared-venv model (the big structural deviation)

The device has **two users** who collaborate through a shared group:

- **`agent`** — the `jetson-agent` SSH alias. Sandboxed: **no sudo**, cannot
  read the other user's home.
- **`farm-edge`** — has **sudo**, owns the repo and runs the privileged steps.

They share a group **`farmproject`** and a **group-writable dir
`/srv/farmproject`** (setgid; both users set `umask 002`). Everything Overwatch
needs lives under `/srv/farmproject` so both accounts can read/write it.

A **shared virtualenv** lives at **`/srv/farmproject/venv`**:

```
sudo apt install -y python3-venv python3-pip          # for ensurepip
python3 -m venv --system-site-packages /srv/farmproject/venv
sudo chgrp -R farmproject /srv/farmproject/venv
sudo chmod -R g+rwX       /srv/farmproject/venv
sudo find /srv/farmproject/venv -type d -exec chmod g+s {} +
```

**`--system-site-packages` is essential** — it lets the venv see the
system-installed **TensorRT / pyds / pyzed** (which are not pip wheels). The
`chgrp`/`chmod`/setgid dance is what lets *both* accounts install into and
import from the same venv.

All `pip install` steps below run **inside this venv with `umask 002`** so new
files stay group-writable.

### Build order + package install (into the shared venv)

**1. PyTorch** — NVIDIA Jetson wheel:

```
https://developer.download.nvidia.com/compute/redist/jp/v512/pytorch/torch-2.1.0a0+41361538.nv23.06-cp38-cp38-linux_aarch64.whl
```

Install with **`--no-deps`**. *Why:* a later `pip install timm` otherwise pulls
a **CPU PyPI torch** and clobbers the CUDA build — the nv `2.1.0a0` is a
pre-release that fails timm's `torch>=` pin, so pip "resolves" it by replacing
torch. The wheel needs **`libopenblas.so.0`** at runtime →
`sudo apt install libopenblas0`. (No-sudo shim used here:
`apt-get download libopenblas0-pthread`, `dpkg-deb -x` into
`/srv/farmproject/syslibs`, then add that dir to the venv `activate`'s
`LD_LIBRARY_PATH`.)

**2. torchvision 0.16.2 + timm + onnx** — torchvision is the cp38 Jetson wheel
matching torch 2.1 (**not** on NVIDIA's redist; sourced from the Ultralytics
assets release). Install all `--no-deps` for timm. timm **hard-requires** a
matching torchvision.

**3. ZED SDK** — download the `.run` matching the device L4T. We used
`ZED_SDK_Tegra_L4T35.4_v4.2.5.zstd.run`; the **L4T 35.4 build ran fine on the
L4T 35.6 device**. Gotchas:

- Needs **`sudo apt install zstd`** (the `.run` is zstd-compressed).
- **Run it as the NORMAL user (`farm-edge`), NOT with `sudo`** — it self-elevates.
- Prompt answers: **No** CUDA (JetPack provides 11.4) · **No** samples ·
  **No** static libs · **Yes** AI module (gates NEURAL depth — keep the
  option) · **Yes** Python API · **No** to "run ZED Diagnostic to download all
  AI models" (GPU-heavy optimization — defer).
- Installs to **`/usr/local/zed`** (group **`zed`**, mode 0770) → add agent:
  **`sudo usermod -aG zed agent`** (a new login picks it up).
- Install pyzed **into the shared venv** (the installer's own pyzed lands in
  farm-edge's `~/.local`, invisible to the venv):
  ```
  source /srv/farmproject/venv/bin/activate
  python /usr/local/zed/get_python_api.py
  ```

**4. pyds (DeepStream Python bindings)** — official prebuilt wheel matching
DS 6.3, `pip install`ed into the venv:

```
https://github.com/NVIDIA-AI-IOT/deepstream_python_apps/releases/download/v1.1.8/pyds-1.1.8-py3-none-linux_aarch64.whl
```

(`gi` / PyGObject 3.36 is already present system-wide.)

**5. overwatch** — clone to a **shared** path and install editable into the venv:

```
git clone https://github.com/Dexom-GH/overwatch.git /srv/farmproject/overwatch
pip install -e /srv/farmproject/overwatch
```

Editable-from-shared is deliberate: installing editable from farm-edge's home
would be unreadable to `agent`, breaking `import overwatch` for the sandboxed
account.

### Verification (the #3 exit criterion)

As **`agent`**, `source /srv/farmproject/venv/bin/activate`, then **13/13**
imports succeed:

- Core deps: `numpy`, `torch` (**cuda True**), `tensorrt` (**8.5.2**),
  `pyzed.sl`, `pyds`, `gi`, `overwatch`.
- The previously import-guarded target-only modules:
  `overwatch.capture.zed_source`, `overwatch.inference.reid.megadescriptor`,
  `overwatch.inference.deepstream.pipeline`, `overwatch.app`.
