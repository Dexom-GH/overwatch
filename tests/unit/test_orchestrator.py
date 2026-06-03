"""Host tests for the pipeline orchestration spine (#38).

The orchestrator turns the individual stages into a running system: it brings
them up in a deterministic order (bus first), supervises them, restarts a stage
that crashes under a bounded policy, and shuts everything down cleanly. All of
that logic is transport- and stage-agnostic, so it is exercised here with stub
stages + a fake bus. Only the live wiring (real ZED/DeepStream/TensorRT stages)
in ``app.main`` is target-only.
"""

import threading

import pytest

from overwatch.orchestrator import RestartPolicy, Stage, StageState, Supervisor


class _StubStage(Stage):
    """Controllable stage: crash ``crashes`` times, then either finish (``finite``)
    or block until stopped (a healthy long-running stage)."""

    def __init__(self, name, *, crashes=0, finite=False):
        self._name = name
        self._crashes_remaining = crashes
        self._finite = finite
        self._lock = threading.Lock()
        self.run_starts = 0
        self.healthy = threading.Event()  # set once it reaches the blocking run

    @property
    def name(self):
        return self._name

    def run(self, stop):
        with self._lock:
            self.run_starts += 1
        if self._crashes_remaining > 0:
            self._crashes_remaining -= 1
            raise RuntimeError("boom:" + self._name)
        if self._finite:
            return
        self.healthy.set()
        stop.wait()


class _FakeBus:
    """Records start/close against a shared event log (duck-typed MessageBus)."""

    def __init__(self, log):
        self._log = log

    def start(self):
        self._log.append(("bus_start", None))

    def close(self):
        self._log.append(("bus_close", None))


def _no_backoff():
    # Instant restarts in tests; real backoff is exercised via RestartPolicy.
    return RestartPolicy(max_restarts=3, window_seconds=60.0, backoff_seconds=0.0)


class TestStageContract:
    def test_stage_is_abstract(self):
        with pytest.raises(TypeError):
            Stage()  # missing name/run


class TestSupervisorStartup:
    def test_starts_bus_first_then_stages_in_order(self):
        log = []
        bus = _FakeBus(log)
        stages = [_StubStage("capture"), _StubStage("inference"), _StubStage("fusion")]
        sup = Supervisor(stages, bus=bus, on_event=lambda ev, name: log.append((ev, name)))
        sup.start()
        try:
            assert log[0] == ("bus_start", None)
            run_starts = [name for ev, name in log if ev == "run_start"]
            assert run_starts == ["capture", "inference", "fusion"]
        finally:
            sup.shutdown(timeout=2.0)


class TestSupervisorSupervision:
    def test_restarts_a_crashed_stage_until_healthy(self):
        stage = _StubStage("flaky", crashes=2)
        sup = Supervisor([stage], policy=_no_backoff())
        sup.start()
        try:
            assert stage.healthy.wait(2.0), "stage never recovered to a healthy run"
            assert stage.run_starts == 3  # 2 crashes + 1 healthy run
            assert sup.state("flaky") == StageState.RUNNING
        finally:
            sup.shutdown(timeout=2.0)

    def test_completed_stage_is_not_restarted(self):
        stage = _StubStage("oneshot", finite=True)
        sup = Supervisor([stage], policy=_no_backoff())
        sup.start()
        try:
            assert sup.wait_settled("oneshot", 2.0)
            assert sup.state("oneshot") == StageState.COMPLETED
            assert stage.run_starts == 1
        finally:
            sup.shutdown(timeout=2.0)

    def test_exhausted_budget_fails_stage_but_siblings_keep_running(self):
        bad = _StubStage("bad", crashes=99)
        good = _StubStage("good")
        events = []
        sup = Supervisor(
            [bad, good],
            policy=RestartPolicy(max_restarts=2, window_seconds=60.0, backoff_seconds=0.0),
            on_event=lambda ev, name: events.append((ev, name)),
        )
        sup.start()
        try:
            assert sup.wait_settled("bad", 2.0)
            assert sup.state("bad") == StageState.FAILED
            assert bad.run_starts == 3  # initial + 2 restarts, then gives up
            # The failure is surfaced, not silently swallowed.
            assert ("failed", "bad") in events
            # A crash in one stage does not halt the others.
            assert good.healthy.wait(2.0)
            assert sup.state("good") == StageState.RUNNING
        finally:
            sup.shutdown(timeout=2.0)


class TestSupervisorShutdown:
    def test_shutdown_stops_stages_in_reverse_order_then_closes_bus(self):
        log = []
        bus = _FakeBus(log)
        stages = [_StubStage("capture"), _StubStage("inference"), _StubStage("output")]
        sup = Supervisor(stages, bus=bus, on_event=lambda ev, name: log.append((ev, name)))
        sup.start()
        for s in stages:
            assert s.healthy.wait(2.0)
        sup.shutdown(timeout=2.0)

        stopped = [name for ev, name in log if ev == "stopped"]
        assert stopped == ["output", "inference", "capture"]  # reverse of startup
        assert log[-1] == ("bus_close", None)  # bus closed after all stages stop
        for s in stages:
            assert sup.state(s.name) == StageState.STOPPED


class TestRestartPolicy:
    def test_allows_restarts_up_to_max_within_window(self):
        policy = RestartPolicy(max_restarts=3, window_seconds=60.0)
        # 1st, 2nd, 3rd failure -> each still within budget.
        assert policy.allows_restart([10.0], now=10.0) is True
        assert policy.allows_restart([10.0, 11.0], now=11.0) is True
        assert policy.allows_restart([10.0, 11.0, 12.0], now=12.0) is True

    def test_gives_up_once_max_exceeded_within_window(self):
        policy = RestartPolicy(max_restarts=3, window_seconds=60.0)
        # 4th failure within the window -> budget exhausted, give up.
        assert policy.allows_restart([10.0, 11.0, 12.0, 13.0], now=13.0) is False

    def test_prunes_failures_outside_window(self):
        policy = RestartPolicy(max_restarts=3, window_seconds=60.0)
        # Three old failures fall outside the 60s window; only the fresh one
        # counts, so a long-lived stage that crashes rarely keeps restarting.
        failures = [1.0, 2.0, 3.0, 200.0]
        assert policy.allows_restart(failures, now=200.0) is True
