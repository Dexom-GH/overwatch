#!/usr/bin/env bash
# TARGET (Jetson) — verify the fully layered environment. Imports each key dep
# and prints versions. Consumed by the env-verification-sweep workflow.
set -euo pipefail

echo "== Overwatch: verify full environment =="

python3 - <<'PY'
mods = [
    ("pyzed.sl", "pyzed"),
    ("torch", "torch"),
    ("tensorrt", "tensorrt"),
    ("pyds", "pyds (DeepStream bindings)"),   # #3 exit criterion: DeepStream imports
]
import importlib
ok = True
for import_name, label in mods:
    try:
        m = importlib.import_module(import_name)
        ver = getattr(m, "__version__", "unknown")
        print(f"[OK]   {label}: {ver}")
    except Exception as e:  # noqa
        ok = False
        print(f"[FAIL] {label}: {e}")

try:
    import torch
    print(f"[INFO] torch.cuda.is_available() = {torch.cuda.is_available()}")
except Exception:
    pass

# The package itself must import on-device (#3 exit criterion) with all its
# target-only deps present (no import guards tripping).
try:
    import overwatch
    print(f"[OK]   import overwatch: {getattr(overwatch, '__version__', 'unknown')}")
except Exception as e:  # noqa
    ok = False
    print(f"[FAIL] import overwatch: {e}")

raise SystemExit(0 if ok else 1)
PY

# TODO: also assert versions match docs/SOFTWARE_STACK.md pins (not just import).
echo "== env verify done =="
