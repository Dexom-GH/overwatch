#!/usr/bin/env bash
# TARGET (Jetson) -- verify the flashed base before installing anything.
# Checks JetPack/L4T and the CUDA/TensorRT/cuDNN/DeepStream line against the pins
# in docs/SOFTWARE_STACK.md. Run this FIRST. Exits non-zero on any mismatch so the
# env-verification-sweep fails loudly.
#
# NOTE: the dpkg grep patterns below are host-authored against the documented
# pins and have NOT been run on a device. Confirm the package names on the first
# real device run and adjust if the JetPack image labels them differently.
set -euo pipefail

echo "== Overwatch: verify JetPack base =="
fail=0

# L4T / JetPack revision (informational; exact string varies by image)
if [ -f /etc/nv_tegra_release ]; then
    echo "[L4T] $(cat /etc/nv_tegra_release)"   # expect L4T 35.6.4 (JetPack 5.1.6)
else
    echo "[FAIL] /etc/nv_tegra_release not found -- is this a Jetson?"
    fail=1
fi

# CUDA 11.4
if command -v nvcc >/dev/null 2>&1; then
    cuda_ver="$(nvcc --version | sed -n 's/.*release \([0-9.]*\).*/\1/p')"
    if [ "$cuda_ver" = "11.4" ]; then
        echo "[OK]   CUDA: $cuda_ver"
    else
        echo "[FAIL] CUDA: $cuda_ver (expected 11.4)"
        fail=1
    fi
else
    echo "[FAIL] nvcc not on PATH (CUDA 11.4 expected)"
    fail=1
fi

# Python 3.8
py_ver="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
if [ "$py_ver" = "3.8" ]; then
    echo "[OK]   Python: $py_ver"
else
    echo "[FAIL] Python: $py_ver (expected 3.8)"
    fail=1
fi

# dpkg-based package pins. $1 label, $2 grep -E pattern, $3 expected version substring.
check_pkg() {
    local label="$1" pat="$2" want="$3" line
    line="$(dpkg -l 2>/dev/null | grep -iE "$pat" | head -1 || true)"
    if [ -z "$line" ]; then
        echo "[FAIL] $label: not found (expected $want)"
        fail=1
    elif echo "$line" | grep -q "$want"; then
        echo "[OK]   $label: $(echo "$line" | awk '{print $3}')"
    else
        echo "[FAIL] $label: $(echo "$line" | awk '{print $3}') (expected $want)"
        fail=1
    fi
}

check_pkg "TensorRT" 'tensorrt|libnvinfer' '8.5'
check_pkg "cuDNN"    'libcudnn'            '8.6'

if [ -d /opt/nvidia/deepstream ] || dpkg -l 2>/dev/null | grep -qiE 'deepstream'; then
    echo "[OK]   DeepStream: present"
else
    echo "[FAIL] DeepStream: not found"
    fail=1
fi

if [ "$fail" -ne 0 ]; then
    echo "== base check FAILED -- fix the mismatches above before provisioning =="
    exit 1
fi
echo "== base check passed =="
