"""Host tests for the startup-precondition health-check (#55).

The full check runs on the Jetson (real RTSP reachability / pyzed / TRT), but the
orchestration and each probe's *logic* are host-testable by injecting stub probes.
We verify: report aggregation + formatting, RTSP url->host:port parsing and
reach/unreachable handling, ZED absent/unavailable handling, engine/config file
presence, bus bindability, and SQLite store writability.
"""

import os

from overwatch.config.schema import (
    BusConfig,
    RtspSourceConfig,
    StoreConfig,
    ZedSourceConfig,
)
from overwatch.healthcheck import (
    CheckResult,
    HealthReport,
    check_bus,
    check_engine_files,
    check_source,
    check_store,
    run_health_check,
)


def _ok_connect(host, port, timeout):  # stub: reachable
    _ok_connect.calls.append((host, port, timeout))


_ok_connect.calls = []


def _refused_connect(host, port, timeout):  # stub: unreachable
    raise OSError("connection refused")


class TestHealthReport:
    def test_ok_true_when_all_pass(self):
        rep = HealthReport([CheckResult("a", True), CheckResult("b", True)])
        assert rep.ok is True

    def test_ok_false_when_any_fails(self):
        rep = HealthReport([CheckResult("a", True), CheckResult("b", False, "boom")])
        assert rep.ok is False

    def test_format_lists_each_check_with_marker_and_aggregate(self):
        rep = HealthReport([CheckResult("bus", True), CheckResult("store", False, "ro")])
        text = rep.format()
        assert "bus" in text and "store" in text
        assert "OK" in text and "FAIL" in text
        assert "ro" in text  # failing detail is surfaced


class TestCheckSourceRtsp:
    def _rtsp(self, url="rtsp://cam.local:8554/stream"):
        return RtspSourceConfig(type="rtsp", source_id="cam-1", url=url, fps=10)

    def test_reachable_rtsp_is_ok_and_parses_host_port(self):
        _ok_connect.calls = []
        res = check_source(self._rtsp(), connect=_ok_connect)
        assert res.ok is True
        assert _ok_connect.calls[0][0] == "cam.local"
        assert _ok_connect.calls[0][1] == 8554

    def test_rtsp_defaults_to_port_554_when_absent(self):
        _ok_connect.calls = []
        check_source(self._rtsp("rtsp://cam.local/stream"), connect=_ok_connect)
        assert _ok_connect.calls[0][1] == 554

    def test_unreachable_rtsp_is_failure_with_detail(self):
        res = check_source(self._rtsp(), connect=_refused_connect)
        assert res.ok is False
        assert "refused" in res.detail.lower()


class TestCheckSourceZed:
    def _zed(self):
        return ZedSourceConfig(type="zed", source_id="zed-0", fps=15)

    def test_zed_present_is_ok(self):
        res = check_source(self._zed(), zed_probe=lambda: 1)
        assert res.ok is True

    def test_zed_absent_is_failure(self):
        res = check_source(self._zed(), zed_probe=lambda: 0)
        assert res.ok is False

    def test_zed_probe_unavailable_is_failure_not_crash(self):
        def _boom():
            raise RuntimeError("pyzed unavailable (target-only)")

        res = check_source(self._zed(), zed_probe=_boom)
        assert res.ok is False
        assert "pyzed" in res.detail.lower()


class TestCheckEngineFiles:
    def test_all_present_is_ok(self, tmp_path):
        a = tmp_path / "nvinfer.txt"
        b = tmp_path / "reid.engine"
        a.write_text("x")
        b.write_text("y")
        res = check_engine_files([str(a), str(b)])
        assert res.ok is True

    def test_missing_file_is_failure_naming_it(self, tmp_path):
        present = tmp_path / "nvinfer.txt"
        present.write_text("x")
        missing = str(tmp_path / "absent.engine")
        res = check_engine_files([str(present), missing])
        assert res.ok is False
        assert "absent.engine" in res.detail


class TestCheckBus:
    def test_bindable_endpoint_is_ok(self):
        cfg = BusConfig(transport="zeromq", endpoint="tcp://127.0.0.1:5599")
        res = check_bus(cfg, bind=lambda endpoint: None)
        assert res.ok is True

    def test_unbindable_endpoint_is_failure(self):
        cfg = BusConfig(transport="zeromq", endpoint="tcp://127.0.0.1:5599")

        def _boom(endpoint):
            raise OSError("address in use")

        res = check_bus(cfg, bind=_boom)
        assert res.ok is False
        assert "address in use" in res.detail.lower()


class TestCheckStore:
    def test_sqlite_writable_path_is_ok(self, tmp_path):
        cfg = StoreConfig(backend="sqlite", path=str(tmp_path / "events.db"))
        res = check_store(cfg)
        assert res.ok is True
        assert os.path.exists(str(tmp_path / "events.db"))

    def test_sqlite_unwritable_is_failure(self):
        cfg = StoreConfig(backend="sqlite", path="/nonexistent-dir-xyz/events.db")
        res = check_store(cfg)
        assert res.ok is False


class TestRunHealthCheck:
    def _cfg(self):
        from overwatch.config.schema import validate_config

        return validate_config(
            {
                "bus": {"transport": "zeromq", "endpoint": "tcp://127.0.0.1:5599"},
                "capture": {
                    "sources": [
                        {"type": "rtsp", "source_id": "cam-1",
                         "url": "rtsp://cam/stream", "fps": 10}
                    ]
                },
                "inference": {
                    "detector_config": "d.txt", "tracker_config": "t.txt",
                    "reid": {"engine": "r.engine", "refresh_seconds": 5,
                             "min_crop_confidence": 0.5},
                },
                "fusion": {
                    "health": {"immobility_seconds": 60, "lameness_score_threshold": 0.5},
                    "events": {},
                },
                "output": {
                    "slack": {"webhook_env": "W", "min_severity": "warning"},
                    "store": {"backend": "sqlite", "path": ":memory:"},
                },
            }
        )

    def test_all_pass_with_stub_probes(self, tmp_path):
        rep = run_health_check(
            self._cfg(),
            connect=_ok_connect,
            bind=lambda e: None,
            exists=lambda p: True,
        )
        assert rep.ok is True
        names = [r.name for r in rep.results]
        assert any("source" in n for n in names)
        assert any("bus" in n for n in names)
        assert any("store" in n for n in names)
        assert any("engine" in n for n in names)

    def test_one_failing_probe_makes_report_fail(self):
        rep = run_health_check(
            self._cfg(),
            connect=_refused_connect,  # camera unreachable
            bind=lambda e: None,
            exists=lambda p: True,
        )
        assert rep.ok is False
        failed = [r.name for r in rep.results if not r.ok]
        assert any("source" in n for n in failed)
