#!/usr/bin/env python3
"""Export MegaDescriptor-T-224 (Swin-Tiny) -> ONNX for TensorRT 8.5 (#7).

Tries the real (NC-licensed) HF weights; falls back to the bare Swin-T
architecture (random weights) if the gated download balks -- the Swin->TRT
conversion friction is architecture-bound, so the finding holds either way.

IMPORTANT: default opset is 16, NOT 17. Opset 17 emits a fused
``LayerNormalization`` op that the TensorRT 8.5 ONNX parser cannot import
(native support is TRT 8.6+); opset 16 decomposes it into primitives TRT 8.5
parses. Saves a fixed 1x3x224x224 reference input + FP32 output for FP16 compare.

Usage: python export_onnx.py <out.onnx> [opset=16]
"""
import sys
import time

import numpy as np
import torch
import timm

OUT_ONNX = sys.argv[1] if len(sys.argv) > 1 else "megadescriptor_t224.onnx"
OPSET = int(sys.argv[2]) if len(sys.argv) > 2 else 16


def load_model():
    try:
        m = timm.create_model(
            "hf-hub:BVRA/MegaDescriptor-T-224", pretrained=True, num_classes=0
        )
        return m.eval(), "hf-hub:BVRA/MegaDescriptor-T-224 (pretrained, NC license)"
    except Exception as exc:  # noqa: BLE001
        print(
            "[warn] HF MegaDescriptor load failed: {}\n"
            "[warn] falling back to timm swin_tiny arch (random weights); "
            "Swin->TRT friction is architecture-bound, so still representative.".format(exc),
            flush=True,
        )
        m = timm.create_model(
            "swin_tiny_patch4_window7_224", pretrained=False, num_classes=0
        )
        return m.eval(), "timm swin_tiny_patch4_window7_224 (random weights, fallback)"


def main():
    model, src = load_model()
    print("[model]", src)
    x = torch.randn(1, 3, 224, 224)
    with torch.no_grad():
        y = model(x)
    print("[fp32] output shape {} embed_dim {}".format(tuple(y.shape), y.shape[-1]))

    t0 = time.time()
    torch.onnx.export(
        model, x, OUT_ONNX,
        input_names=["input"], output_names=["embedding"],
        opset_version=OPSET, do_constant_folding=True,
    )
    print("[onnx] exported {} (opset {}) in {:.1f}s".format(OUT_ONNX, OPSET, time.time() - t0))

    import onnx
    onnx.checker.check_model(onnx.load(OUT_ONNX))
    print("[onnx] checker OK")

    np.save("ref_input.npy", x.numpy())
    np.save("ref_output_fp32.npy", y.numpy())
    x.numpy().astype(np.float32).tofile("ref_input.bin")
    print("[saved] ref_input.npy ref_output_fp32.npy ref_input.bin")


if __name__ == "__main__":
    main()
