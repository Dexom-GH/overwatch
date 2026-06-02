#!/usr/bin/env bash
# TARGET (Jetson) — install PyTorch from NVIDIA's Jetson wheel (~torch 2.1 for
# JetPack 5.1.x). RUN AFTER the ZED SDK (10_install_zed_sdk.sh). NOT a PyPI install.
# See docs/SOFTWARE_STACK.md and the jetson-env-setup skill.
set -euo pipefail

echo "== Overwatch: install PyTorch (Jetson wheel; after ZED SDK) =="

# Guard: ZED SDK / pyzed must already be present (enforce build order).
if ! python3 -c "import pyzed.sl" >/dev/null 2>&1; then
    echo "ERROR: pyzed not found. Install the ZED SDK FIRST"
    echo "       (scripts/target/10_install_zed_sdk.sh) — build order matters."
    exit 1
fi

# Pinned NVIDIA Jetson wheels for JetPack 5.1.x / Python 3.8 (cp38), torch ~2.1.
# Canonical source: NVIDIA "PyTorch for Jetson"
# (forums.developer.nvidia.com/t/pytorch-for-jetson/72048). Defaults below use the
# Ultralytics GitHub mirror (confirmed reachable). Override via env if needed.
TORCH_WHL_URL="${TORCH_WHL_URL:-https://github.com/ultralytics/assets/releases/download/v0.0.0/torch-2.1.0a0+41361538.nv23.06-cp38-cp38-linux_aarch64.whl}"
TORCHVISION_WHL_URL="${TORCHVISION_WHL_URL:-https://github.com/ultralytics/assets/releases/download/v0.0.0/torchvision-0.16.2+c6f3977-cp38-cp38-linux_aarch64.whl}"

# Bootstrap pip if absent (this L4T's system python3 ships without pip/ensurepip).
# --user keeps it non-root. If wget to bootstrap.pypa.io is blocked, instead run:
#   sudo apt-get update && sudo apt-get install -y python3-pip
if ! python3 -m pip --version >/dev/null 2>&1; then
    echo "[pip] bootstrapping pip (--user) ..."
    tmp_getpip="$(mktemp)"
    wget -qO "$tmp_getpip" https://bootstrap.pypa.io/pip/3.8/get-pip.py
    python3 "$tmp_getpip" --user
    rm -f "$tmp_getpip"
fi

# torch 2.1 (nv) is built against numpy 1.x; pin <2 to avoid the numpy 2.0 ABI break.
echo "[torch] installing numpy<2, torch, torchvision (user site) ..."
python3 -m pip install --user --upgrade "numpy<2"
python3 -m pip install --user "$TORCH_WHL_URL" "$TORCHVISION_WHL_URL"

# Verify CUDA is visible (False usually means a wrong/CPU wheel).
python3 -c "import torch, torchvision; print('[OK] torch', torch.__version__, '| cuda', torch.cuda.is_available(), '| torchvision', torchvision.__version__)"

echo "== PyTorch install complete =="
