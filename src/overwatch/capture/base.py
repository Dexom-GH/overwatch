"""Capture source interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator, Optional, Tuple

from overwatch.bus.schemas import DepthFrame, Frame


class CaptureSource(ABC):
    """A sensor producing time-aligned RGB (and optionally depth) frames.

    Implementations yield ``(Frame, Optional[DepthFrame])`` pairs. The depth
    component is ``None`` for depth-less sources (e.g. the deferred IP cameras);
    the ZED source provides both, aligned by ``frame_id``.
    """

    @abstractmethod
    def open(self) -> None:
        """Acquire the device / stream."""
        raise NotImplementedError

    @abstractmethod
    def frames(self) -> Iterator[Tuple[Frame, Optional[DepthFrame]]]:
        """Yield ``(rgb, depth_or_none)`` pairs until the source is exhausted."""
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        """Release the device / stream."""
        raise NotImplementedError


__all__ = ["CaptureSource"]
