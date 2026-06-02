#!/usr/bin/env python3
"""Compare a TRT engine's output vs the torch FP32 reference (#7).

Reads a trtexec --exportOutput JSON dump and ref_output_fp32.npy; reports the
embedding dimensionality and cosine similarity. FP32 engine should give ~1.00;
pure-FP16 MegaDescriptor gives ~0.13 (Swin FP16 overflow) -- see the
trt-model-conversion skill.
"""
import json
import sys

import numpy as np

trt_json = sys.argv[1] if len(sys.argv) > 1 else "trt_out.json"

ref = np.load("ref_output_fp32.npy").astype(np.float64).flatten()

with open(trt_json) as f:
    data = json.load(f)

entry = None
for e in data:
    if "embedding" in e.get("name", ""):
        entry = e
        break
if entry is None:
    entry = data[-1]

vals = np.array(entry["values"], dtype=np.float64)
print("[trt] output name:", entry.get("name"), "dims:", entry.get("dimensions"), "n:", vals.size)
print("[dim] embedding dim:", vals.size, "(expected 768 for Swin-T)")

if vals.size == ref.size:
    cos = float(ref @ vals / (np.linalg.norm(ref) * np.linalg.norm(vals)))
    print("[cosine] FP32(torch) vs TRT:", round(cos, 5))
else:
    print("[warn] size mismatch: ref", ref.size, "trt", vals.size, "- skipping cosine")
