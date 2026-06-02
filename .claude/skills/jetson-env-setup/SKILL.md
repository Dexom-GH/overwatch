---
name: jetson-env-setup
description: Use when provisioning, verifying, or troubleshooting the Jetson Xavier NX software stack for Overwatch — the correct build order (ZED SDK before PyTorch), version-pin checks, and failure triage. For on-device setup work, not host dev.
---

# Jetson environment setup

Provision and verify the Overwatch target (Jetson Xavier NX, JetPack 5.1.6).
This is a **procedure**; the authoritative version pins live in
`docs/SOFTWARE_STACK.md` — this skill references them, it does not duplicate them.

## Golden rule

**Install the ZED SDK BEFORE PyTorch.** The ZED SDK installs cleanly against a
stock JetPack environment; installing the Jetson torch wheel first perturbs the
CUDA/Python environment and complicates the ZED SDK build and `pyzed` wheel
selection. The ordered scripts enforce this with guards — don't bypass them.

## Ordered procedure (run on the device)

```
scripts/target/00_verify_jetpack.sh      # confirm L4T 35.6.4 / CUDA 11.4 / TRT 8.5 BEFORE installing
scripts/target/10_install_zed_sdk.sh     # ZED SDK FIRST  (guards: aborts if torch already present)
scripts/target/20_install_pytorch.sh     # NVIDIA Jetson torch wheel ~2.1  (guards: aborts if pyzed missing)
scripts/target/30_verify_env.sh          # import pyzed / torch / tensorrt, print versions
scripts/target/40_convert_megadescriptor.sh   # then convert models (see trt-model-conversion skill)
```

Each step is a skeleton with the real install commands marked `TODO` — fill them
in against the pins in `docs/SOFTWARE_STACK.md`.

## Key facts to verify (against docs/SOFTWARE_STACK.md)

- JetPack **5.1.6** / L4T **35.6.4**, Ubuntu 20.04, **Python 3.8**.
- CUDA **11.4**, cuDNN **8.6**, TensorRT **8.5**, DeepStream installed.
- PyTorch from NVIDIA's **Jetson wheel** (~2.1) — *not* `pip install torch` from
  PyPI. `pyzed` from the ZED SDK installer — *not* PyPI.
- **JetPack 6 is Orin-only.** Do not attempt it on Xavier NX.

## Failure triage

| Symptom | Likely cause / fix |
|---|---|
| `import pyzed` fails after install | ZED SDK didn't build the py3.8 wheel; rerun installer; confirm Python is 3.8. |
| `torch.cuda.is_available()` is False | Wrong wheel (CPU/PyPI) — use the NVIDIA Jetson wheel for 5.1.x. |
| ZED SDK install messy / CUDA conflicts | torch was installed first — order violated. Reflash or remove torch, reinstall ZED SDK first. |
| `trtexec`/TRT mismatch | TensorRT version drift from the 8.5 pin; check `dpkg -l | grep tensorrt`. |

## Verify (done = green)

`scripts/target/30_verify_env.sh` exits 0 and reports `pyzed`, `torch`
(`cuda.is_available() == True`), and `tensorrt` at the pinned versions. The
`env-verification-sweep` workflow automates this check.
