"""Identity gallery — enrollment + matching. **V2 stub.**

V1 produces embeddings (``megadescriptor.py``) but has **no gallery to match
against** — manual enrollment is deferred to V2 (see docs/ROADMAP_V1_V2.md). This
interface is stubbed now so forward-porting matching into V1 is a small change,
not a new design.

If you pull this forward, mark the change ``# V2->V1:`` and move the item in the
roadmap.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, List, Optional, Tuple


class Gallery(ABC):
    """Stores enrolled individual embeddings and matches new ones. V2."""

    @abstractmethod
    def enroll(self, individual_id: str, embedding: "Any") -> None:
        """V2: add a known individual's embedding to the gallery."""
        raise NotImplementedError("Gallery.enroll — V2 (no gallery in V1)")

    @abstractmethod
    def match(self, embedding: "Any") -> Optional[Tuple[str, float]]:
        """V2: return ``(individual_id, score)`` for the best match, or None."""
        raise NotImplementedError("Gallery.match — V2 (no gallery in V1)")

    @abstractmethod
    def individuals(self) -> List[str]:
        """V2: list enrolled individual ids."""
        raise NotImplementedError("Gallery.individuals — V2 (no gallery in V1)")


__all__ = ["Gallery"]
