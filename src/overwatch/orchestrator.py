"""Pipeline orchestration spine — lifecycle + supervised restart (#38).

Turns the individual pipeline stages into a running system. Per the 2026-06-03
grooming decision the supervision model is a **single process with internal
supervision** (one systemd unit, see #43) — *not* a unit per stage. This module
is that supervisor:

- :class:`Stage` — the uniform lifecycle every stage implements: a blocking
  ``run(stop)`` that returns when ``stop`` is set or its work is exhausted, and
  raises to signal a crash.
- :class:`RestartPolicy` — a pure, bounded restart rule (N restarts within a
  trailing window, with backoff).
- :class:`Supervisor` — brings the bus up first, starts each stage on its own
  thread in the configured order, detects a crashed stage and restarts it under
  the policy, surfaces a stage that exhausts its budget (without silently
  halting the rest), and shuts everything down in reverse order.

The supervisor is transport- and stage-agnostic (stdlib ``threading`` only), so
it is host-unit-tested with stub stages + a fake bus. The live wiring of the
real target-only stages (ZED / DeepStream / TensorRT) lives in ``app.main``.

Target code — kept Python 3.8-compatible.
"""

from __future__ import annotations

import enum
import logging
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Sequence

if TYPE_CHECKING:
    from overwatch.bus.base import MessageBus

_LOG = logging.getLogger(__name__)


class Stage(ABC):
    """A long-lived pipeline stage with a uniform, supervisable lifecycle.

    A stage's whole life is its :meth:`run` method: it blocks, doing work, until
    the supervisor sets the ``stop`` event (clean shutdown) or its work is
    naturally exhausted (e.g. a finite replay source). Raising out of ``run`` is
    how a stage signals a crash — the supervisor catches it and applies the
    :class:`RestartPolicy`. Stages communicate only over the bus (see
    ``bus-stage-conventions``); the supervisor never inspects their I/O.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable, unique stage name (used for ordering, logging, state)."""
        raise NotImplementedError

    @abstractmethod
    def run(self, stop: "threading.Event") -> None:
        """Run until ``stop`` is set or work is exhausted; raise to signal a crash."""
        raise NotImplementedError


class StageState(enum.Enum):
    """Lifecycle state the supervisor tracks per stage."""

    PENDING = "pending"      # not yet started
    RUNNING = "running"      # run() executing (or between supervised restarts)
    COMPLETED = "completed"  # run() returned on its own (finite work done)
    FAILED = "failed"        # crashed and exhausted its restart budget
    STOPPED = "stopped"      # ended cleanly in response to shutdown


@dataclass
class RestartPolicy:
    """Bounded restart rule: at most ``max_restarts`` within ``window_seconds``.

    Failures older than the window are ignored, so a long-lived stage that
    crashes only occasionally keeps being restarted; a stage thrashing inside the
    window is given up on. ``backoff_seconds`` is the pause before each restart.
    """

    max_restarts: int = 3
    window_seconds: float = 60.0
    backoff_seconds: float = 0.5

    def allows_restart(self, failure_times: "List[float]", now: float) -> bool:
        """Whether a stage may restart, given its failure timestamps and ``now``.

        ``failure_times`` includes the failure that just occurred. Returns True
        while the count of failures within the trailing window is within budget.
        """
        recent = [t for t in failure_times if now - t < self.window_seconds]
        return len(recent) <= self.max_restarts


# (event, stage_name) — stage_name is None for bus/pipeline-level events.
EventHook = Callable[[str, Optional[str]], None]


class Supervisor:
    """Bring up, supervise, and tear down the pipeline stages in one process.

    Startup is deterministic: the bus is started first, then each stage on its
    own daemon thread in the given order (``start`` waits for each stage to enter
    ``run`` before launching the next). A stage that crashes is restarted under
    ``policy``; one that returns on its own is marked COMPLETED; one that exhausts
    its restart budget is marked FAILED and surfaced — the *other* stages keep
    running, so a single crash never silently halts the pipeline. Shutdown stops
    stages in reverse order (LIFO) and closes the bus last.

    ``clock`` / ``sleep`` are injected so the supervision logic is host-testable
    without real time; ``on_event`` is an observability hook (defaults to logging).
    """

    def __init__(
        self,
        stages: "Sequence[Stage]",
        *,
        bus: "Optional[MessageBus]" = None,
        policy: "Optional[RestartPolicy]" = None,
        on_event: "Optional[EventHook]" = None,
        clock: "Callable[[], float]" = time.monotonic,
        sleep: "Callable[[float], None]" = time.sleep,
        ready_timeout: float = 10.0,
    ) -> None:
        self._stages = list(stages)
        self._bus = bus
        self._policy = policy if policy is not None else RestartPolicy()
        self._on_event = on_event if on_event is not None else self._log_event
        self._clock = clock
        self._sleep = sleep
        self._ready_timeout = ready_timeout

        self._threads: Dict[str, threading.Thread] = {}
        self._stops: Dict[str, threading.Event] = {}
        self._ready: Dict[str, threading.Event] = {}
        self._settled: Dict[str, threading.Event] = {}
        self._states: Dict[str, StageState] = {}
        for stage in self._stages:
            self._stops[stage.name] = threading.Event()
            self._ready[stage.name] = threading.Event()
            self._settled[stage.name] = threading.Event()
            self._states[stage.name] = StageState.PENDING

    # -- introspection -------------------------------------------------------
    def state(self, name: str) -> StageState:
        """Current lifecycle state of stage ``name``."""
        return self._states[name]

    def wait_settled(self, name: str, timeout: "Optional[float]" = None) -> bool:
        """Block until stage ``name`` reaches a terminal state (COMPLETED/FAILED/STOPPED)."""
        return self._settled[name].wait(timeout)

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        """Start the bus, then each stage in order, waiting for each to come up."""
        if self._bus is not None:
            self._bus.start()
            self._on_event("bus_start", None)
        for stage in self._stages:
            thread = threading.Thread(
                target=self._worker, args=(stage,), name="stage:" + stage.name, daemon=True
            )
            self._threads[stage.name] = thread
            thread.start()
            if not self._ready[stage.name].wait(self._ready_timeout):
                raise RuntimeError("stage {} did not start".format(stage.name))

    def shutdown(self, timeout: float = 5.0) -> None:
        """Stop stages in reverse startup order, then close the bus."""
        for stage in reversed(self._stages):
            name = stage.name
            self._stops[name].set()
            thread = self._threads.get(name)
            if thread is not None:
                thread.join(timeout)
        if self._bus is not None:
            self._bus.close()
            self._on_event("bus_close", None)

    # -- internals -----------------------------------------------------------
    def _worker(self, stage: "Stage") -> None:
        name = stage.name
        stop = self._stops[name]
        failures: List[float] = []
        while True:
            self._states[name] = StageState.RUNNING
            self._on_event("run_start", name)
            self._ready[name].set()
            try:
                stage.run(stop)
            except Exception:  # noqa: BLE001 — a crash is any exception out of run()
                if stop.is_set():
                    # Crashed while we were tearing it down; treat as a clean stop.
                    self._finish(name, StageState.STOPPED, "stopped")
                    return
                now = self._clock()
                failures.append(now)
                self._on_event("crash", name)
                _LOG.warning("stage %s crashed", name, exc_info=True)
                if self._policy.allows_restart(failures, now):
                    self._on_event("restart", name)
                    self._sleep(self._policy.backoff_seconds)
                    continue
                _LOG.error("stage %s exhausted its restart budget; giving up", name)
                self._finish(name, StageState.FAILED, "failed")
                return
            else:
                if stop.is_set():
                    self._finish(name, StageState.STOPPED, "stopped")
                else:
                    self._finish(name, StageState.COMPLETED, "completed")
                return

    def _finish(self, name: str, state: StageState, event: str) -> None:
        self._states[name] = state
        self._on_event(event, name)
        self._settled[name].set()

    @staticmethod
    def _log_event(event: str, name: "Optional[str]") -> None:
        _LOG.info("orchestrator event: %s%s", event, "" if name is None else " [" + name + "]")


__all__ = ["Stage", "StageState", "RestartPolicy", "Supervisor", "EventHook"]
