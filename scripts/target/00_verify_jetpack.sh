#!/usr/bin/env bash
# TARGET (Jetson) — verify the flashed base before installing anything.
# Checks JetPack/L4T and the CUDA/TensorRT line against the pins in
# docs/SOFTWARE_STACK.md. Run this FIRST. Skeleton — fill in exact assertions.
set -euo pipefail

echo "== Overwatch: verify JetPack base =="

# L4T / JetPack revision
if [ -f /etc/nv_tegra_release ]; then
    echo "[L4T] $(cat /etc/nv_tegra_release)"   # expect L4T 35.6.4 (JetPack 5.1.6)
else
    echo "WARN: /etc/nv_tegra_release not found — is this a Jetson?"
fi

# CUDA
if command -v nvcc >/dev/null 2>&1; then
    nvcc --version | sed -n 's/.*release \([0-9.]*\).*/[CUDA] \1/p'   # expect 11.4
else
    echo "WARN: nvcc not on PATH (CUDA 11.4 expected)"
fi

# Python (expect 3.8)
python3 --version

# TODO: assert TensorRT 8.5 (dpkg -l | grep tensorrt), cuDNN 8.6, DeepStream present.
# TODO: exit non-zero on any mismatch so the env-verification-sweep workflow fails loudly.

echo "== base check done (skeleton — assertions TODO) =="
