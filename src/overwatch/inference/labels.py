"""``animals.yaml`` -> ``labels.txt`` single source of truth (#57).

``configs/animals.yaml`` is the authoritative ``class_id`` <-> name map for the
custom-fine-tuned YOLOv8 detector (we own the label map). The DeepStream
``labels.txt`` is **generated from it**, one name per line in ``class_id`` order —
never hand-maintained — and the nvinfer ``num-detected-classes`` must equal the
class count. This module owns that generation + a drift guard so the two can
never silently diverge (the guard runs as a host unit test; on-device
confirmation of the running engine's label map folds into #49).

Regenerate after editing ``animals.yaml``::

    python -m overwatch.inference.labels --write    # rewrite labels.txt
    python -m overwatch.inference.labels --check     # exit 1 if out of sync

Host-safe (pure ``yaml`` parsing). Python 3.8-compatible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# Repo layout: this file is src/overwatch/inference/labels.py.
_INFERENCE_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _INFERENCE_DIR.parents[2]
ANIMALS_YAML = _REPO_ROOT / "configs" / "animals.yaml"
LABELS_TXT = _INFERENCE_DIR / "deepstream" / "configs" / "labels.txt"
NVINFER_CONFIG = _INFERENCE_DIR / "deepstream" / "configs" / "nvinfer_detector.txt"

_NUM_CLASSES_RE = re.compile(r"^\s*num-detected-classes\s*=\s*(\d+)", re.MULTILINE)


@dataclass(frozen=True)
class AnimalClass:
    """One detector class. ``class_id`` is canonical and positional in labels.txt."""

    class_id: int
    name: str
    tier: int                          # data-availability tier (1/2/3); see animals.yaml
    reid_difficulty: Optional[str] = None


def parse_animal_classes(data: "Dict[str, Any]") -> "List[AnimalClass]":
    """Validate a parsed ``animals.yaml`` mapping into class-id-ordered classes.

    Enforces the invariants ``labels.txt`` / nvinfer rely on: a non-empty class
    set, a ``name`` and ``tier`` per entry, and ``class_id`` values forming a
    unique, contiguous ``0..N-1`` range (so list index == class_id, which is how
    nvinfer maps a detection to a label). Raises ``ValueError`` otherwise.
    """
    animals = data.get("animals") if isinstance(data, dict) else None
    if not isinstance(animals, list) or not animals:
        raise ValueError("animals.yaml: 'animals' must be a non-empty list")

    classes: List[AnimalClass] = []
    for i, entry in enumerate(animals):
        if not isinstance(entry, dict):
            raise ValueError("animals[{}] must be a mapping".format(i))
        if "class_id" not in entry or not isinstance(entry["class_id"], int):
            raise ValueError("animals[{}] missing integer 'class_id'".format(i))
        if not entry.get("name"):
            raise ValueError("animals[{}] missing 'name'".format(i))
        if "tier" not in entry or not isinstance(entry["tier"], int):
            raise ValueError(
                "animals[{}] ({}) missing integer 'tier'".format(i, entry.get("name"))
            )
        classes.append(
            AnimalClass(
                class_id=entry["class_id"],
                name=str(entry["name"]),
                tier=entry["tier"],
                reid_difficulty=entry.get("reid_difficulty"),
            )
        )

    ids = [c.class_id for c in classes]
    if len(set(ids)) != len(ids):
        raise ValueError("animals.yaml: duplicate class_id values: {}".format(ids))
    if sorted(ids) != list(range(len(ids))):
        raise ValueError(
            "animals.yaml: class_id must be a contiguous 0..N-1 range, got {}".format(
                sorted(ids)
            )
        )
    return sorted(classes, key=lambda c: c.class_id)


def load_animal_classes(path: "Optional[Path]" = None) -> "List[AnimalClass]":
    """Load + validate ``animals.yaml`` (defaults to the packaged config)."""
    target = Path(path) if path is not None else ANIMALS_YAML
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    return parse_animal_classes(data)


# The V1 farm detector (#77) is trained on tier 1-2 only (sheep/goat/poultry);
# tier-3 (rabbit, guinea_pig) are demoted to V2 (#90) but keep canonical ids here.
V1_DETECTOR_MAX_TIER = 2


def detector_classes(
    classes: "Optional[List[AnimalClass]]" = None,
) -> "List[AnimalClass]":
    """Classes the V1 detector ships (#77): tier <= ``V1_DETECTOR_MAX_TIER``.

    ``labels.txt`` and the nvinfer ``num-detected-classes`` describe THIS subset,
    not the full canonical map in ``animals.yaml`` (which reserves V2 ids). Asserts
    the subset's ``class_id`` values are contiguous ``0..M-1`` — nvinfer maps a
    detection to a label positionally, so a tier-3 class at a low id would break it.
    """
    src = classes if classes is not None else load_animal_classes()
    det = sorted(
        (c for c in src if c.tier <= V1_DETECTOR_MAX_TIER), key=lambda c: c.class_id
    )
    ids = [c.class_id for c in det]
    if ids != list(range(len(ids))):
        raise ValueError(
            "V1 detector class_ids must be a contiguous 0..M-1 range, got {}".format(ids)
        )
    return det


def render_labels(classes: "List[AnimalClass]") -> str:
    """Render the labels.txt body: one name per line, class-id order, LF, trailing \\n."""
    ordered = sorted(classes, key=lambda c: c.class_id)
    return "".join(c.name + "\n" for c in ordered)


def read_labels_file(path: "Optional[Path]" = None) -> "List[str]":
    """Read committed labels.txt into logical names (line-ending / blank-line agnostic)."""
    target = Path(path) if path is not None else LABELS_TXT
    text = target.read_text(encoding="utf-8")
    return [line.strip() for line in text.splitlines() if line.strip()]


def read_nvinfer_num_detected_classes(path: "Optional[Path]" = None) -> "Optional[int]":
    """Parse ``num-detected-classes`` out of the nvinfer config (None if absent)."""
    target = Path(path) if path is not None else NVINFER_CONFIG
    match = _NUM_CLASSES_RE.search(target.read_text(encoding="utf-8"))
    return int(match.group(1)) if match else None


def labels_out_of_sync() -> bool:
    """True if committed labels.txt diverges from the V1 detector classes (#77)."""
    return read_labels_file() != [c.name for c in detector_classes()]


def write_labels(classes: "List[AnimalClass]", path: "Optional[Path]" = None) -> None:
    """Write labels.txt from ``classes`` with LF endings (repo convention)."""
    target = Path(path) if path is not None else LABELS_TXT
    # newline="" + explicit \n in render keeps LF on Windows too (matches .gitattributes).
    target.write_text(render_labels(classes), encoding="utf-8", newline="")


def _main(argv: "Optional[List[str]]" = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="animals.yaml -> labels.txt (#57)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--write", action="store_true", help="regenerate labels.txt")
    group.add_argument("--check", action="store_true", help="exit 1 if out of sync")
    args = parser.parse_args(argv)

    classes = detector_classes()  # V1 detector subset (#77): tier 1-2
    if args.write:
        write_labels(classes)
        print("wrote {} ({} V1 detector classes)".format(LABELS_TXT, len(classes)))
        return 0
    if labels_out_of_sync():
        print("labels.txt is OUT OF SYNC with animals.yaml; run --write")
        return 1
    print("labels.txt is in sync ({} classes)".format(len(classes)))
    return 0


__all__ = [
    "AnimalClass",
    "parse_animal_classes",
    "load_animal_classes",
    "detector_classes",
    "V1_DETECTOR_MAX_TIER",
    "render_labels",
    "read_labels_file",
    "read_nvinfer_num_detected_classes",
    "labels_out_of_sync",
    "write_labels",
    "ANIMALS_YAML",
    "LABELS_TXT",
    "NVINFER_CONFIG",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
