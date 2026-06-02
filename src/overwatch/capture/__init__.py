"""Capture stage — produces synchronized RGB + depth from the sensor.

V1 sensor is the ZED 2i (``zed_source.py``, target-only). The ``CaptureSource``
ABC keeps the interface multi-source-capable so the deferred IP cameras can be
added without reshaping the stage.

Importing this package must NOT pull in ``pyzed`` — ``zed_source`` guards that
import so ``import overwatch`` works on the Windows host. Import ``ZedSource``
explicitly (on the target) when you need it.
"""

from overwatch.capture.base import CaptureSource

__all__ = ["CaptureSource"]
