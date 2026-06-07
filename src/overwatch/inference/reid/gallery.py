"""Identity gallery — enrollment + matching.

V1 produces embeddings (``megadescriptor.py``); on its own it has nothing to
match against. ``Gallery`` is the enrollment/matching interface, with a minimal
concrete implementation (:class:`CosineGallery`) **forward-ported from V2** so the
on-demand ReID embedding can actually identify an animal in the V1 demo (see #21).

# V2->V1: minimal manual gallery + cosine-NN match pulled forward (#137/#21) so the
# on-demand ReID embedding (#17) can actually identify an animal in the V1 demo,
# not just log an unmatched vector. Full enrollment/cross-camera re-ID stays V2.
# Roadmap: docs/ROADMAP_V1_V2.md (gallery row moved into V1 scope, 2026-06-07).

This module is **host-safe** (numpy only — no TensorRT / image libraries), so the
store, the cosine match, and the threshold are built and unit-tested off-device.
Generating the real embeddings that get enrolled/matched is target-only
(``megadescriptor.py``); the on-device match e2e is #21.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Tuple, Union

import numpy as np

if TYPE_CHECKING:  # tooling only
    from os import PathLike

    PathT = Union[str, PathLike]

# Provisional cosine-similarity acceptance threshold. The real operating point is
# tuned and recorded on-device in #21 (real MegaDescriptor embeddings); this
# host-side default just makes the match logic usable and testable.
DEFAULT_MATCH_THRESHOLD = 0.5

_MAGIC = "OWGAL"  # stored in the .npz to fail loud on a foreign/garbage file


class Gallery(ABC):
    """Stores enrolled individual embeddings and matches new ones."""

    @abstractmethod
    def enroll(self, individual_id: str, embedding: "np.ndarray") -> None:
        """Add a known individual's embedding to the gallery."""
        raise NotImplementedError

    @abstractmethod
    def match(self, embedding: "np.ndarray") -> Optional[Tuple[str, float]]:
        """Return ``(individual_id, score)`` for the best match, or ``None``."""
        raise NotImplementedError

    @abstractmethod
    def individuals(self) -> List[str]:
        """List enrolled individual ids."""
        raise NotImplementedError


class CosineGallery(Gallery):
    """Minimal manual gallery: cosine nearest-neighbour over enrolled embeddings.

    Embeddings are L2-normalized on enrollment, so a match is the maximum dot
    product against the query (also normalized). A query matches the nearest
    enrolled embedding **iff** its cosine similarity is ``>= threshold``; ties are
    broken in favour of the **first-enrolled** entry. Deliberately minimal — no
    automatic enrollment, no cross-camera association (#34 stays V2), no learned
    metric, no temporal smoothing.
    """

    def __init__(self, threshold: float = DEFAULT_MATCH_THRESHOLD) -> None:
        self._threshold = float(threshold)
        self._ids: List[str] = []
        self._embeddings: List[np.ndarray] = []  # parallel to _ids; L2-normalized
        self._dim: Optional[int] = None

    # -- normalization / validation ------------------------------------------
    def _normalize(self, embedding: "np.ndarray") -> "np.ndarray":
        vec = np.asarray(embedding, dtype=np.float32)
        if vec.ndim != 1:
            raise ValueError(f"embedding must be 1-D, got shape {vec.shape}")
        if self._dim is not None and vec.shape[0] != self._dim:
            raise ValueError(
                f"embedding dim {vec.shape[0]} != gallery dim {self._dim}"
            )
        norm = float(np.linalg.norm(vec))
        if not np.isfinite(norm) or norm == 0.0:
            raise ValueError("embedding has zero or non-finite norm; cannot normalize")
        return vec / norm

    # -- Gallery interface ----------------------------------------------------
    def enroll(self, individual_id: str, embedding: "np.ndarray") -> None:
        normalized = self._normalize(embedding)
        if self._dim is None:
            self._dim = int(normalized.shape[0])
        self._ids.append(str(individual_id))
        self._embeddings.append(normalized)

    def match(self, embedding: "np.ndarray") -> Optional[Tuple[str, float]]:
        if not self._embeddings:
            return None
        query = self._normalize(embedding)
        best_id: Optional[str] = None
        best_score = -np.inf
        for ind_id, enrolled in zip(self._ids, self._embeddings):
            score = float(np.dot(query, enrolled))
            if score > best_score:  # strict: first-enrolled wins on a tie
                best_score = score
                best_id = ind_id
        if best_id is not None and best_score >= self._threshold:
            return best_id, best_score
        return None

    def individuals(self) -> List[str]:
        seen = set()
        ordered: List[str] = []
        for ind_id in self._ids:
            if ind_id not in seen:
                seen.add(ind_id)
                ordered.append(ind_id)
        return ordered

    # -- persistence (flat .npz under models/gallery/, gitignored) -----------
    def save(self, path: "PathT") -> None:
        """Persist to a flat ``.npz`` (``embeddings`` (N,D) + ``labels`` (N,))."""
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        if self._embeddings:
            embeddings = np.stack(self._embeddings).astype(np.float32)
        else:
            embeddings = np.empty((0, 0), dtype=np.float32)
        labels = np.array(self._ids, dtype=np.str_)
        np.savez(
            out,
            magic=np.array(_MAGIC),
            threshold=np.array(self._threshold, dtype=np.float64),
            embeddings=embeddings,
            labels=labels,
        )

    @classmethod
    def load(cls, path: "PathT", threshold: Optional[float] = None) -> "CosineGallery":
        """Load a gallery saved by :meth:`save`.

        ``threshold`` overrides the persisted operating point when given.
        """
        with np.load(Path(path), allow_pickle=False) as data:
            if str(data["magic"]) != _MAGIC:
                raise ValueError(f"not an Overwatch gallery file: {path}")
            persisted_threshold = float(data["threshold"])
            embeddings = data["embeddings"]
            labels = [str(x) for x in data["labels"].tolist()]
        gallery = cls(threshold=threshold if threshold is not None else persisted_threshold)
        # Stored embeddings are already normalized; re-enroll to restore state
        # (enroll re-normalizes, which is idempotent for unit vectors).
        for ind_id, vec in zip(labels, list(embeddings)):
            gallery.enroll(ind_id, vec)
        return gallery


__all__ = ["Gallery", "CosineGallery", "DEFAULT_MATCH_THRESHOLD"]
