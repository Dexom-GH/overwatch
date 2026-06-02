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

# TODO:
#   1. Fetch the NVIDIA Jetson torch wheel for JetPack 5.1.x (~2.1) + matching
#      torchvision.
#   2. pip install the wheel(s).
#   3. Verify: python3 -c "import torch; print(torch.__version__, torch.cuda.is_available())"

echo "== PyTorch install (skeleton — steps TODO) =="
