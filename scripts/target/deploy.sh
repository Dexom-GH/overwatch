#!/usr/bin/env bash
# TARGET (Jetson) — deploy a released version of Overwatch to the device.
# GATED / MANUAL: this is run by hand on (or against) the Jetson, never by CI —
# GitHub runners can't reach the device. It requires an explicit version arg and
# a typed confirmation, so it can't fire by accident. See docs/RELEASING.md.
#
#   Usage:  bash scripts/target/deploy.sh <version>      # e.g. 2026.6.0
set -euo pipefail

VERSION="${1:-}"
if [ -z "$VERSION" ]; then
  echo "usage: bash scripts/target/deploy.sh <version>   (e.g. 2026.6.0)"
  exit 2
fi

echo "== Overwatch deploy to Jetson: v$VERSION =="
echo "This will update the on-device install and rebuild engines. Type the version to confirm:"
read -r CONFIRM
if [ "$CONFIRM" != "$VERSION" ]; then
  echo "confirmation mismatch — aborting."
  exit 1
fi

# TODO (fill in when V1 is shippable — kept a skeleton on purpose):
#   1. Verify the env is provisioned & matches pins: bash scripts/target/30_verify_env.sh
#   2. Fetch the release: git fetch --tags && git checkout "v$VERSION"
#   3. Install/refresh the package on device: pip install -e . (target deps via
#      requirements.target.txt are already installed by the provisioning scripts).
#   4. (Re)build TensorRT engines for THIS device: bash scripts/target/40_convert_megadescriptor.sh
#   5. Restart the Overwatch service / pipeline (systemd unit, TBD).
#   6. Smoke-check: pipeline reaches PLAYING, a Slack alert can be emitted.

echo "== deploy skeleton — steps are TODO (see docs/RELEASING.md); nothing changed =="
