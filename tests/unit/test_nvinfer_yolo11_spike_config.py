"""Host sanity guard for the throwaway YOLOv11 spike nvinfer config."""
from __future__ import annotations

import re
from pathlib import Path

CONFIG = (
    Path(__file__).resolve().parents[2]
    / "src" / "overwatch" / "inference" / "deepstream" / "configs"
    / "nvinfer_yolo11_spike.txt"
)


def _field(name: str) -> str:
    m = re.search(r"^\s*{}\s*=\s*(.+?)\s*$".format(re.escape(name)), CONFIG.read_text(), re.M)
    assert m, "missing field: {}".format(name)
    return m.group(1)


def test_coco_class_count():
    assert _field("num-detected-classes") == "80"  # stock COCO yolo11n


def test_fp16_network_mode():
    assert _field("network-mode") == "2"  # FP16


def test_reuses_deepstream_yolo_parser():
    assert _field("parse-bbox-func-name") == "NvDsInferParseYolo"
    assert _field("custom-lib-path").endswith("libnvdsinfer_custom_impl_Yolo.so")
