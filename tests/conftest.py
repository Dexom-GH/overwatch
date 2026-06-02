"""Pytest configuration and markers.

Markers separate host-runnable tests from target-only ones so CI/host runs can
``-m "not device and not gpu and not zed"`` and skip what needs the Jetson.

- ``device``: requires the Jetson target (any on-device dependency).
- ``gpu``: requires CUDA / TensorRT.
- ``zed``: requires the ZED SDK / a connected ZED camera (pyzed).
"""

import pytest


def pytest_configure(config: "pytest.Config") -> None:
    config.addinivalue_line("markers", "device: requires the Jetson target device")
    config.addinivalue_line("markers", "gpu: requires CUDA / TensorRT")
    config.addinivalue_line("markers", "zed: requires the ZED SDK / camera (pyzed)")
