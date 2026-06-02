"""Target-only tests (skipped on the host).

These run only on the Jetson, where pyzed / DeepStream / TensorRT exist. Marked
so host/CI runs exclude them with ``-m "not device"``. Real device tests
(ZED open, engine load, pipeline build) are added as stages are implemented.
"""

import pytest


@pytest.mark.device
@pytest.mark.zed
def test_zed_source_opens() -> None:
    pytest.skip("device test placeholder — implement on the Jetson target")


@pytest.mark.device
@pytest.mark.gpu
def test_megadescriptor_engine_loads() -> None:
    pytest.skip("device test placeholder — implement on the Jetson target")
