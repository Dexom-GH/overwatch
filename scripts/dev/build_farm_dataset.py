#!/usr/bin/env python3
"""Build the V1 3-class farm detection dataset (#77) by combining dedicated
Roboflow Universe datasets for sheep, goat, and poultry.

Downloads each source (pinned workspace/project/version), reports its license
(ADR-0007: non-commercial OK), then remaps every source's classes onto our
canonical ids (sheep=0, goat=1, poultry=2 — animals.yaml tier 1-2) and merges
into ``datasets/farm/`` with an Ultralytics ``data.yaml``. Idempotent: re-download
is skipped if the raw dir already exists. Data is gitignored; this script is the
reproducible provenance record (DoD: "dataset snapshot/version used").

Needs ROBOFLOW_API_KEY (Private key) in .env. Run from the repo root:
    python scripts/dev/build_farm_dataset.py
"""
from __future__ import annotations

import random
import shutil
from pathlib import Path

import yaml

RAW = Path("datasets/raw")
OUT = Path("datasets/farm")
TARGETS = ["sheep", "goat", "poultry"]  # canonical id order (animals.yaml tier 1-2)
TARGET_ID = {name: i for i, name in enumerate(TARGETS)}

# Pinned sources. name_map: source class name (lowercased) -> our target (or absent = drop).
SOURCES = [
    {"name": "sheep", "ws": "mtandjl", "project": "sheep_detection-chjlw", "version": 5},
    {"name": "goat", "ws": "brookside-research", "project": "goat-looker", "version": 6},
    {"name": "poultry", "ws": "charan-7q0md", "project": "chickens-a4wpd", "version": 63},
]
# Uniform class-name -> target map applied to ALL sources (handles strays/typos).
NAME_MAP = {
    "sheep": "sheep", "shee[": "sheep",
    "goat": "goat", "goats": "goat",
    "chicken": "poultry", "poultry": "poultry",
    "1woc": "poultry", "2woc": "poultry", "3woc": "poultry", "4woc": "poultry",
}
# Sources expose inconsistent splits (sheep/poultry are train-only; goat has
# valid/test), so we pool every image and re-split per-source 85/15 train/val —
# guaranteeing each class is represented in validation (else per-class mAP is
# unmeasurable). #77.
SRC_SPLITS = ("train", "valid", "val", "test")
VAL_FRAC = 0.15
SEED = 0


def _load_key() -> str:
    for line in Path(".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("ROBOFLOW_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("ROBOFLOW_API_KEY not in .env")


def download() -> None:
    from roboflow import Roboflow

    rf = Roboflow(api_key=_load_key())
    RAW.mkdir(parents=True, exist_ok=True)
    for s in SOURCES:
        dest = RAW / s["name"]
        if dest.exists():
            print("[skip] {} already downloaded".format(dest))
            continue
        print("[dl] {ws}/{project} v{version} -> {d}".format(d=dest, **s))
        proj = rf.workspace(s["ws"]).project(s["project"])
        proj.version(s["version"]).download("yolov8", location=str(dest))


def report_licenses() -> None:
    print("\n=== LICENSES (verify ADR-0007: non-commercial OK) ===")
    for s in SOURCES:
        # Roboflow records the license in data.yaml's roboflow block (and
        # README.dataset.txt), NOT README.roboflow.txt.
        lic = "UNKNOWN"
        data_yaml = RAW / s["name"] / "data.yaml"
        if data_yaml.exists():
            cfg = yaml.safe_load(data_yaml.read_text(encoding="utf-8")) or {}
            lic = (cfg.get("roboflow") or {}).get("license", "UNKNOWN")
        print("  {:8s} {}/{} v{}: {}".format(s["name"], s["ws"], s["project"], s["version"], lic))
    print()


def _src_names(src_dir: Path) -> "list":
    cfg = yaml.safe_load((src_dir / "data.yaml").read_text(encoding="utf-8"))
    names = cfg.get("names")
    if isinstance(names, dict):
        names = [names[k] for k in sorted(names)]
    return list(names or [])


def _collect(src: "dict") -> "list":
    """All target-bearing (image, remapped-label-lines, dest-stem) from a source."""
    src_dir = RAW / src["name"]
    names = _src_names(src_dir)
    remap = {}
    for old_id, nm in enumerate(names):
        tgt = NAME_MAP.get(str(nm).strip().lower())
        remap[old_id] = TARGET_ID[tgt] if tgt else None
    print("[collect] {}: names={} remap={}".format(src["name"], names, remap))
    items = []
    for split in SRC_SPLITS:
        lbl_dir = src_dir / split / "labels"
        img_dir = src_dir / split / "images"
        if not lbl_dir.exists():
            continue
        for lbl in lbl_dir.glob("*.txt"):
            kept = []
            for line in lbl.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if not parts:
                    continue
                new_id = remap.get(int(parts[0]))
                if new_id is None:
                    continue
                kept.append(" ".join([str(new_id)] + parts[1:]))
            if not kept:
                continue  # no target boxes -> drop image
            img = next((p for p in img_dir.glob(lbl.stem + ".*")), None)
            if img is None:
                continue
            items.append((img, kept, "{}_{}".format(src["name"], lbl.stem)))
    return items


def merge() -> "tuple":
    if OUT.exists():
        shutil.rmtree(OUT)
    for split in ("train", "val"):
        (OUT / split / "images").mkdir(parents=True, exist_ok=True)
        (OUT / split / "labels").mkdir(parents=True, exist_ok=True)

    counts = {t: 0 for t in TARGETS}
    val_counts = {t: 0 for t in TARGETS}
    for s in SOURCES:
        items = _collect(s)
        random.Random(SEED).shuffle(items)  # deterministic
        n_val = int(len(items) * VAL_FRAC)
        for i, (img, kept, stem) in enumerate(items):
            split = "val" if i < n_val else "train"
            (OUT / split / "labels" / (stem + ".txt")).write_text(
                "\n".join(kept) + "\n", encoding="utf-8"
            )
            shutil.copyfile(img, OUT / split / "images" / (stem + img.suffix))
            for ln in kept:
                cid = int(ln.split()[0])
                counts[TARGETS[cid]] += 1
                if split == "val":
                    val_counts[TARGETS[cid]] += 1
        print("[merge] {}: {} images ({} -> val)".format(s["name"], len(items), n_val))

    (OUT / "data.yaml").write_text(
        yaml.safe_dump({
            "path": str(OUT.resolve()),
            "train": "train/images",
            "val": "val/images",
            "nc": len(TARGETS),
            "names": TARGETS,
        }, sort_keys=False),
        encoding="utf-8",
    )
    return counts, val_counts


def main() -> int:
    download()
    report_licenses()
    counts, val_counts = merge()
    print("=== merged box counts (total / val) per class ===")
    for t in TARGETS:
        print("  {:8s} {:6d}  (val {})".format(t, counts[t], val_counts[t]))
    print("\n[done] {} (data.yaml names={})".format(OUT, TARGETS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
