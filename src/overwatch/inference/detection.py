"""Detection interface (model-agnostic).

In V1 detection runs inside DeepStream's ``nvinfer`` element (see
``deepstream/``). This ABC exists so detection can also be driven outside
DeepStream (tests, host-side experiments, the deferred IP-camera path) against
the same contract.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, List

from overwatch.bus.schemas import Detection, Frame


class Detector(ABC):
    """Turns a frame into a list of 2D detections."""

    @abstractmethod
    def detect(self, frame: Frame) -> List[Detection]:
        """Run detection on ``frame`` and return zero or more detections."""
        raise NotImplementedError

    @abstractmethod
    def load(self, model_path: "Any") -> None:
        """Load weights / TensorRT engine."""
        raise NotImplementedError


__all__ = ["Detector"]
