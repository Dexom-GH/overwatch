"""Host tests for pipeline observability — metrics + structured logging (#13).

The on-device DoD bars across slices (FPS, per-stage latency, ReID dispatch
count/latency, dropped frames; see #8) are only trustworthy if we can measure
them. This module is the host-testable plumbing: thread-safe metric primitives
(the #38 supervisor runs stages on threads), a registry, and structured logging.
Real numbers come from the on-device run; the plumbing is verified here.
"""

import json
import logging
import threading
from io import StringIO

from overwatch import observability as obs


class TestCounter:
    def test_inc_defaults_to_one(self):
        c = obs.Counter()
        c.inc()
        c.inc()
        assert c.value == 2

    def test_inc_by_n(self):
        c = obs.Counter()
        c.inc(5)
        assert c.value == 5

    def test_thread_safe_under_concurrent_increments(self):
        c = obs.Counter()

        def worker():
            for _ in range(1000):
                c.inc()

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert c.value == 8000


class TestGauge:
    def test_set_and_read(self):
        g = obs.Gauge()
        g.set(3.5)
        assert g.value == 3.5


class TestLatencyStat:
    def test_records_count_total_avg_max(self):
        s = obs.LatencyStat()
        for d in (0.010, 0.020, 0.030):
            s.record(d)
        assert s.count == 3
        assert abs(s.total_s - 0.060) < 1e-9
        assert abs(s.avg_s - 0.020) < 1e-9
        assert abs(s.max_s - 0.030) < 1e-9

    def test_avg_is_zero_when_empty(self):
        assert obs.LatencyStat().avg_s == 0.0

    def test_time_context_manager_records_elapsed(self):
        clock = iter([100.0, 100.25])  # start, end -> 0.25s
        s = obs.LatencyStat()
        with s.time(clock=lambda: next(clock)):
            pass
        assert s.count == 1
        assert abs(s.max_s - 0.25) < 1e-9


class TestRateMeter:
    def test_estimates_rate(self):
        m = obs.RateMeter(window=10)
        for t in range(5):  # ticks 1s apart -> 1/s
            m.tick(float(t))
        assert abs(m.rate() - 1.0) < 1e-6

    def test_zero_until_two_samples(self):
        m = obs.RateMeter()
        assert m.rate() == 0.0
        m.tick(0.0)
        assert m.rate() == 0.0


class TestRegistry:
    def test_get_or_create_returns_same_instance(self):
        r = obs.MetricsRegistry()
        assert r.counter("dropped_frames") is r.counter("dropped_frames")
        assert r.gauge("fps") is r.gauge("fps")
        assert r.latency("stage_latency") is r.latency("stage_latency")

    def test_snapshot_reports_values(self):
        r = obs.MetricsRegistry()
        r.counter("dropped_frames").inc(2)
        r.gauge("fps").set(15.0)
        r.latency("reid_latency").record(0.05)
        snap = r.snapshot()
        assert snap["dropped_frames"] == 2
        assert snap["fps"] == 15.0
        assert snap["reid_latency"]["count"] == 1


class TestStructuredLogging:
    def test_log_event_emits_json_with_fields(self):
        stream = StringIO()
        logger = logging.getLogger("overwatch.test.obs1")
        logger.handlers = []
        logger.propagate = False
        handler = logging.StreamHandler(stream)
        handler.setFormatter(obs.StructuredFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        obs.log_event(logger, logging.INFO, "frame", stage="capture", fps=15.0)

        record = json.loads(stream.getvalue().strip())
        assert record["msg"] == "frame"
        assert record["stage"] == "capture"
        assert record["fps"] == 15.0
        assert record["level"] == "INFO"

    def test_log_metrics_emits_registry_snapshot(self):
        stream = StringIO()
        logger = logging.getLogger("overwatch.test.obs2")
        logger.handlers = []
        logger.propagate = False
        handler = logging.StreamHandler(stream)
        handler.setFormatter(obs.StructuredFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        reg = obs.MetricsRegistry()
        reg.counter("dropped_frames").inc(3)
        obs.log_metrics(logger, reg)

        record = json.loads(stream.getvalue().strip())
        assert record["dropped_frames"] == 3
