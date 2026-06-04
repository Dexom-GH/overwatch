#!/usr/bin/env bash
# TARGET (Jetson) — bounded post-deploy smoke-check (#43). Confirms the deployed
# package + declared deps import at the checked-out ref, the config loads, and the
# pipeline/fusion/output modules import (catching drift like a missing python-dotenv).
#
# BOUNDED on purpose: it does NOT start the live pipeline. The full runtime check
# (pipeline reaches PLAYING + a test Slack alert is delivered) needs the supervised
# app (#38) + a model/source + SLACK_WEBHOOK — that is #81.
#
#   Env: OVERWATCH_VENV  venv dir (default: /srv/farmproject/venv)
set -euo pipefail

PY="${OVERWATCH_VENV:-/srv/farmproject/venv}/bin/python"
echo "== Overwatch bounded smoke-check =="

"$PY" - <<'PY'
import importlib
ok = True

# 1. Package + declared runtime deps import at the deployed ref.
for m in ["overwatch", "dotenv", "pydantic", "zmq", "yaml", "numpy"]:
    try:
        importlib.import_module(m)
        print("[OK]   import {}".format(m))
    except Exception as e:  # noqa: BLE001
        ok = False
        print("[FAIL] import {}: {}".format(m, e))

# 2. Config loads + validates (does not require secrets).
try:
    from overwatch.config.loader import load_config
    cfg = load_config()
    print("[OK]   config loads (bus={})".format(cfg.bus.transport))
except Exception as e:  # noqa: BLE001
    ok = False
    print("[FAIL] config load: {}".format(e))

# 3. The stage/pipeline/fusion/output modules import (target-only deps are
#    import-guarded, so this passes on a correctly provisioned device).
for m in [
    "overwatch.app",
    "overwatch.inference.deepstream.pipeline",
    "overwatch.inference.deepstream.probes",
    "overwatch.fusion.mono_alerts",
    "overwatch.output.slack",
]:
    try:
        importlib.import_module(m)
        print("[OK]   import {}".format(m))
    except Exception as e:  # noqa: BLE001
        ok = False
        print("[FAIL] import {}: {}".format(m, e))

raise SystemExit(0 if ok else 1)
PY

echo "== bounded smoke-check passed =="
