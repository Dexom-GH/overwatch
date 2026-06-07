"""Host tests for operator-visible pipeline liveness (#136).

The tracker + monitor are pure/host-runnable (injected clock, no real time, no
device) so the degraded/recovered state machine and the throttled Slack signal are
unit-tested against a *simulated* stalled source. The real capture wiring + a live
source loss on the Jetson is the deferred on-device bar (AC6).
"""

from __future__ import annotations

from overwatch.output.liveness import LivenessTracker
from overwatch.output.liveness_monitor import LivenessMonitor


# --- LivenessTracker: per-source up/down + degraded rollup --------------------

def test_registered_source_is_down_until_first_frame() -> None:
    t = LivenessTracker(silence_seconds=30.0)
    t.register("cam-0")
    snap = t.snapshot(now=100.0)
    assert [s.source_id for s in snap.sources] == ["cam-0"]
    s = snap.sources[0]
    assert s.up is False and s.last_frame_age_s is None
    assert snap.degraded is True  # a source with no frames is degraded


def test_marked_source_is_up_within_window() -> None:
    t = LivenessTracker(silence_seconds=30.0)
    t.mark("cam-0", now=100.0)            # mark auto-registers
    snap = t.snapshot(now=105.0)
    s = snap.sources[0]
    assert s.up is True
    assert s.last_frame_age_s == 5.0
    assert snap.degraded is False


def test_source_goes_degraded_past_silence_window() -> None:
    t = LivenessTracker(silence_seconds=30.0)
    t.mark("cam-0", now=100.0)
    snap = t.snapshot(now=140.0)          # 40s since last frame > 30s window
    s = snap.sources[0]
    assert s.up is False
    assert s.last_frame_age_s == 40.0
    assert snap.degraded is True


def test_recovery_clears_degraded() -> None:
    t = LivenessTracker(silence_seconds=30.0)
    t.mark("cam-0", now=100.0)
    assert t.snapshot(now=140.0).degraded is True   # gone silent
    t.mark("cam-0", now=141.0)                       # frames resume
    snap = t.snapshot(now=142.0)
    assert snap.sources[0].up is True
    assert snap.degraded is False


def test_multiple_sources_mixed_state() -> None:
    t = LivenessTracker(silence_seconds=30.0)
    t.mark("cam-0", now=100.0)
    t.mark("cam-1", now=60.0)             # stale
    snap = t.snapshot(now=105.0)
    by_id = {s.source_id: s for s in snap.sources}
    assert by_id["cam-0"].up is True
    assert by_id["cam-1"].up is False
    assert snap.degraded is True          # one source down -> degraded
    # insertion order preserved
    assert [s.source_id for s in snap.sources] == ["cam-0", "cam-1"]


# --- stage restarts (AC3) ----------------------------------------------------

def test_restart_is_reflected_then_ages_out() -> None:
    t = LivenessTracker(silence_seconds=30.0, restart_window_seconds=300.0)
    t.mark("cam-0", now=100.0)
    t.note_restart("inference", now=100.0)
    snap = t.snapshot(now=110.0)          # within restart window
    assert any(r["stage"] == "inference" for r in snap.recent_restarts)
    assert snap.degraded is True          # a recent restart degrades the rollup

    t.mark("cam-0", now=420.0)            # keep the source up
    later = t.snapshot(now=420.0)         # restart now older than the 300s window
    assert later.recent_restarts == []
    assert later.degraded is False


def test_unregistered_mark_auto_registers() -> None:
    t = LivenessTracker(silence_seconds=30.0)
    t.mark("cam-9", now=10.0)             # never register()ed
    assert [s.source_id for s in t.snapshot(now=10.0).sources] == ["cam-9"]


# --- LivenessMonitor: throttled degraded/recovered Slack signal ---------------

def _collect_monitor():
    """A monitor wired to an in-memory sink; returns (tracker, monitor, alerts)."""
    tracker = LivenessTracker(silence_seconds=30.0)
    alerts = []
    monitor = LivenessMonitor(tracker, alerts.append, cooldown_seconds=300.0)
    return tracker, monitor, alerts


def test_no_alert_when_source_comes_up_normally() -> None:
    tracker, monitor, alerts = _collect_monitor()
    tracker.mark("cam-0", now=100.0)
    monitor.check(now=101.0)              # up -> no edge from the optimistic seed
    assert alerts == []


def test_silent_source_fires_one_degraded_alert() -> None:
    tracker, monitor, alerts = _collect_monitor()
    tracker.mark("cam-0", now=100.0)
    monitor.check(now=101.0)              # up, baseline
    monitor.check(now=140.0)             # 40s silent -> degraded edge
    assert len(alerts) == 1
    a = alerts[0]
    assert a.severity == "warning"
    assert a.source_event.kind == "source_degraded"
    assert a.source_event.detail["source_id"] == "cam-0"
    assert "cam-0" in a.message
    # still silent -> throttled, no repeat within cooldown
    monitor.check(now=160.0)
    assert len(alerts) == 1


def test_recovery_fires_recovered_alert() -> None:
    tracker, monitor, alerts = _collect_monitor()
    tracker.mark("cam-0", now=100.0)
    monitor.check(now=101.0)
    monitor.check(now=140.0)             # degraded
    tracker.mark("cam-0", now=150.0)     # frames resume
    monitor.check(now=151.0)             # recovered edge
    kinds = [a.source_event.kind for a in alerts]
    assert kinds == ["source_degraded", "source_recovered"]
    assert alerts[-1].severity == "info"


def test_dead_from_start_source_alerts_after_window() -> None:
    tracker, monitor, alerts = _collect_monitor()
    tracker.register("cam-0")            # registered, never marked
    monitor.check(now=10.0)              # within window since "start" — no alert yet
    monitor.check(now=50.0)              # > silence window, still no frame -> degraded
    assert len(alerts) == 1
    assert alerts[0].source_event.detail["source_id"] == "cam-0"


def test_sources_alert_independently() -> None:
    tracker, monitor, alerts = _collect_monitor()
    tracker.mark("cam-0", now=100.0)
    tracker.mark("cam-1", now=100.0)
    monitor.check(now=101.0)             # both up, baseline
    tracker.mark("cam-1", now=140.0)     # cam-1 keeps producing
    monitor.check(now=140.0)             # cam-0 silent 40s -> degraded; cam-1 up
    assert len(alerts) == 1
    assert alerts[0].source_event.detail["source_id"] == "cam-0"
