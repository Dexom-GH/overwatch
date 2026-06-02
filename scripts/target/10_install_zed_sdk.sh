#!/usr/bin/env bash
# TARGET (Jetson) — install the ZED SDK. RUN BEFORE PyTorch (20_install_pytorch.sh).
# The ZED SDK installs cleanest against a stock JetPack env; installing the
# Jetson torch wheel first perturbs CUDA/Python and complicates pyzed selection.
# See docs/SOFTWARE_STACK.md (build order) and the jetson-env-setup skill.
set -euo pipefail

echo "== Overwatch: install ZED SDK (must precede PyTorch) =="

# TODO:
#   1. Download the ZED SDK installer matching JetPack 5.1.x for Xavier NX.
#   2. Run the installer (it builds/installs the pyzed wheel for Python 3.8).
#   3. Verify: python3 -c "import pyzed.sl as sl; print(sl.Camera)"
#
# Guard: refuse to proceed if a Jetson torch wheel is already installed, to
# preserve build order.
if python3 -c "import torch" >/dev/null 2>&1; then
    echo "ERROR: torch is already installed. ZED SDK must be installed BEFORE"
    echo "       PyTorch (docs/SOFTWARE_STACK.md). Reflash or remove torch first."
    exit 1
fi

echo "== ZED SDK install (skeleton — steps TODO) =="
