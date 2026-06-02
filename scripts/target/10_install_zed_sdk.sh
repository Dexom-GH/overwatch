#!/usr/bin/env bash
# TARGET (Jetson) — install the ZED SDK. RUN BEFORE PyTorch (20_install_pytorch.sh).
# The ZED SDK installs cleanest against a stock JetPack env; installing the
# Jetson torch wheel first perturbs CUDA/Python and complicates pyzed selection.
# See docs/SOFTWARE_STACK.md (build order) and the jetson-env-setup skill.
set -euo pipefail

echo "== Overwatch: install ZED SDK (must precede PyTorch) =="

# Guard: refuse to proceed if a Jetson torch wheel is already installed, to
# preserve build order.
if python3 -c "import torch" >/dev/null 2>&1; then
    echo "ERROR: torch is already installed. ZED SDK must be installed BEFORE"
    echo "       PyTorch (docs/SOFTWARE_STACK.md). Reflash or remove torch first."
    exit 1
fi

# The ZED SDK .run installer for THIS device's L4T. Download the build matching
# `cat /etc/nv_tegra_release` (L4T 35.6 / JetPack 5.1.x — a 4.x SDK) from
# https://www.stereolabs.com/developers/release/ . Then either set ZED_SDK_RUN to
# its path, or drop the .run beside this script (scripts/target/).
# NOTE: not yet run on a device — confirm installer flags on first real run.
ZED_SDK_RUN="${ZED_SDK_RUN:-}"
if [ -z "$ZED_SDK_RUN" ]; then
    ZED_SDK_RUN="$(ls -1 "$(dirname "$0")"/ZED_SDK_Tegra_*.run 2>/dev/null | head -1 || true)"
fi
if [ -z "$ZED_SDK_RUN" ] || [ ! -f "$ZED_SDK_RUN" ]; then
    echo "ERROR: ZED SDK installer not found."
    echo "  Download the .run matching this device's L4T from"
    echo "  https://www.stereolabs.com/developers/release/ and either:"
    echo "    - export ZED_SDK_RUN=/path/to/ZED_SDK_Tegra_L4T35.x_vY.Y.Y.zstd.run, or"
    echo "    - place it beside this script (scripts/target/)."
    exit 1
fi

# pyzed install (inside the .run) uses python3 -m pip; bootstrap pip first if absent.
if ! python3 -m pip --version >/dev/null 2>&1; then
    echo "WARN: pip not found. The ZED SDK Python API step needs pip; bootstrap it first:"
    echo "      wget -qO /tmp/get-pip.py https://bootstrap.pypa.io/pip/3.8/get-pip.py && python3 /tmp/get-pip.py --user"
fi

echo "[zed] installer: $ZED_SDK_RUN"
chmod +x "$ZED_SDK_RUN"

# Silent/unattended. Keep Python (pyzed). skip_cuda: CUDA 11.4 already provided by
# JetPack. skip_hub/skip_tools: lean install. Needs root (writes /usr/local/zed).
sudo "$ZED_SDK_RUN" -- silent skip_cuda skip_hub skip_tools

# Verify pyzed imports for python3 (3.8).
python3 -c "import pyzed.sl as sl; print('[OK] pyzed import OK')" \
    || { echo "ERROR: pyzed import failed after install (expected a Python 3.8 wheel). See the jetson-env-setup skill triage."; exit 1; }

echo "== ZED SDK install complete =="
