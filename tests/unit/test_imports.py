"""Host-safe import smoke tests.

These prove the import-guard convention holds: the whole package (including
target-only submodules) imports on the Windows host without pulling in pyzed /
DeepStream / TensorRT. If one of these fails on the host, a target-only import
leaked to module top level.
"""

import importlib

import pytest

HOST_SAFE_MODULES = [
    "overwatch",
    "overwatch.bus",
    "overwatch.bus.base",
    "overwatch.bus.topics",
    "overwatch.bus.schemas",
    "overwatch.bus.serialization",
    "overwatch.bus.redis_bus",
    "overwatch.bus.zeromq_bus",
    "overwatch.capture",
    "overwatch.capture.base",
    "overwatch.capture.zed_source",          # guarded import
    "overwatch.inference",
    "overwatch.inference.detection",
    "overwatch.inference.tracking",
    "overwatch.inference.pose",
    "overwatch.inference.deepstream.pipeline",   # guarded import
    "overwatch.inference.deepstream.probes",
    "overwatch.inference.reid.megadescriptor",   # guarded import
    "overwatch.inference.reid.gallery",
    "overwatch.fusion",
    "overwatch.fusion.depth_fusion",
    "overwatch.fusion.zone_counting",
    "overwatch.fusion.health",
    "overwatch.fusion.events",
    "overwatch.output",
    "overwatch.output.slack",
    "overwatch.output.store",
    "overwatch.config",
    "overwatch.config.loader",
    "overwatch.app",
]


@pytest.mark.parametrize("module_name", HOST_SAFE_MODULES)
def test_module_imports_on_host(module_name: str) -> None:
    importlib.import_module(module_name)


def test_schema_dataclasses_construct() -> None:
    """The contract dataclasses construct with simple values (no heavy deps)."""
    from overwatch.bus import schemas

    alert = schemas.Alert(
        timestamp=0.0, severity="info", title="t", message="m"
    )
    assert alert.severity == "info"
    count = schemas.ZoneCount(zone_id="z1", timestamp=0.0, count=3)
    assert count.count == 3
