"""Manual gallery enrollment CLI (#137 — the host half of #21).

Builds a :class:`~overwatch.inference.reid.gallery.CosineGallery` from a directory
of reference crops laid out as ``<individual_id>/<image>``::

    crops/
      daisy/   d1.jpg d2.jpg
      rosie/   r1.jpg

The **gallery store, the crop discovery, and the enrollment loop are host-safe**
and unit-tested with a mock embedder — :func:`build_gallery` takes an injected
``embed_crop`` callable, so no TensorRT or image library is needed to test it.

Only the *default* embedder (:func:`default_embed_crop`) is target-only: it loads
the image and runs the real MegaDescriptor TRT engine, both guarded so importing
this module still works on the host. The real on-device match e2e is #21.

Usage (on the Jetson)::

    python -m overwatch.inference.reid.enroll --crops crops/ --out models/gallery/farm.npz
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Callable, List, Optional, Sequence, Tuple

from overwatch.inference.reid.gallery import DEFAULT_MATCH_THRESHOLD, CosineGallery

if TYPE_CHECKING:  # tooling only
    import numpy as np

_LOG = logging.getLogger(__name__)

# Recognized crop image extensions (case-insensitive).
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp"}

# An embedder maps a crop image path to its embedding vector. Injected so the
# enrollment core is testable on the host without a real engine.
EmbedCrop = Callable[[Path], "np.ndarray"]


def discover_crops(crops_dir: "Path") -> List[Tuple[str, Path]]:
    """Return ``(individual_id, image_path)`` pairs under ``crops_dir``.

    Each immediate subdirectory is one individual; its image files (by extension)
    are that individual's reference crops. Non-image files and loose files at the
    top level are ignored. Order is deterministic (sorted by id, then filename).
    """
    root = Path(crops_dir)
    pairs: List[Tuple[str, Path]] = []
    for individual_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for image_path in sorted(individual_dir.iterdir()):
            if image_path.is_file() and image_path.suffix.lower() in _IMAGE_SUFFIXES:
                pairs.append((individual_dir.name, image_path))
    return pairs


def build_gallery(
    crops_dir: "Path",
    embed_crop: EmbedCrop,
    *,
    threshold: float = DEFAULT_MATCH_THRESHOLD,
) -> CosineGallery:
    """Build a :class:`CosineGallery` by embedding every crop under ``crops_dir``.

    ``embed_crop`` is called once per discovered image and returns its embedding;
    inject a mock for host tests, or :func:`default_embed_crop` on the Jetson.
    """
    gallery = CosineGallery(threshold=threshold)
    for individual_id, image_path in discover_crops(Path(crops_dir)):
        embedding = embed_crop(image_path)
        gallery.enroll(individual_id, embedding)
    return gallery


def default_embed_crop(
    engine_path: str = "models/megadescriptor_t224_fp16.engine",
) -> EmbedCrop:
    """Build the real (target-only) embedder: load image + MegaDescriptor TRT.

    Constructing the engine raises on the host (tensorrt unavailable) — this path
    runs only on the Jetson. Image loading is imported lazily so this module stays
    host-importable.
    """
    from overwatch.inference.reid.megadescriptor import MegaDescriptorReID

    reid = MegaDescriptorReID(engine_path=engine_path)
    reid.load()

    def _embed(image_path: Path) -> "np.ndarray":
        import cv2  # type: ignore  # target-only image IO, guarded by lazy import

        image = cv2.imread(str(image_path))
        if image is None:
            raise ValueError(f"could not read crop image: {image_path}")
        return reid.embed(image)

    return _embed


def main(argv: "Optional[Sequence[str]]" = None) -> int:
    parser = argparse.ArgumentParser(description="Enroll reference crops into a ReID gallery.")
    parser.add_argument("--crops", required=True, type=Path, help="dir of <id>/<image> crops")
    parser.add_argument("--out", required=True, type=Path, help="output gallery .npz path")
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_MATCH_THRESHOLD,
        help=f"cosine match threshold (default {DEFAULT_MATCH_THRESHOLD})",
    )
    parser.add_argument(
        "--engine",
        default="models/megadescriptor_t224_fp16.engine",
        help="MegaDescriptor TRT engine path (target-only)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    gallery = build_gallery(
        args.crops, default_embed_crop(args.engine), threshold=args.threshold
    )
    gallery.save(args.out)
    _LOG.info(
        "enrolled %d individual(s) (%d crops) -> %s",
        len(gallery.individuals()),
        len(discover_crops(Path(args.crops))),
        args.out,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())


__all__ = ["discover_crops", "build_gallery", "default_embed_crop", "main", "EmbedCrop"]
