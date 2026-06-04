"""Startup-precondition health-check / watchdog (#55).

The #38 supervisor restarts a *running* stage that crashes; nothing verifies the
**startup preconditions** before the pipeline comes up, or gives a single
"is the system healthy" signal. This module is that check: for the validated
``AppConfig`` it probes —

- **source(s) reachable** — an RTSP camera (TCP connect to its host:port) now, or
  a ZED present (``pyzed`` device list) when a ``zed`` source is configured (#54);
- **engine / nvinfer configs present** — the detector/tracker configs and the
  ReID engine file exist and are readable;
- **bus up** — the configured transport endpoint is bindable (ZeroMQ);
- **EventStore writable** — the SQLite durable tier opens + creates its table.

Each probe returns a :class:`CheckResult` (never raises); :func:`run_health_check`
aggregates them into a :class:`HealthReport` with an overall pass/fail and
per-check detail. Runnable on demand and at boot via ``python -m overwatch.healthcheck``
and ``scripts/target/55_healthcheck.sh`` (wired into the #43 deploy smoke-check).

**Host vs target.** The orchestration and each probe's logic are host-testable by
injecting stub probes (see ``tests/unit/test_healthcheck.py``); the *real* probes
for the ZED (``pyzed``) and a live RTSP camera, plus deep TRT-engine deserialize,
are target-only and verified on the Jetson. Stdlib-only; Python 3.8-compatible.
"""

from __future__ import annotations

import logging
import os
import socket
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, List, Optional
from urllib.parse import urlparse

if TYPE_CHECKING:  # avoid import cost / cycles at runtime; types only
    from overwatch.config.schema import AppConfig, BusConfig, StoreConfig

_LOG = logging.getLogger(__name__)

_DEFAULT_RTSP_PORT = 554
_DEFAULT_TIMEOUT_S = 2.0

# Injectable probe signatures (defaults below; stubbed in host tests).
Connector = Callable[[str, int, float], None]  # raises on failure
Binder = Callable[[str], None]                 # raises on failure
ZedProbe = Callable[[], int]                   # device count; may raise
ExistsFn = Callable[[str], bool]
StoreOpener = Callable[["StoreConfig"], None]  # raises on failure


@dataclass
class CheckResult:
    """Outcome of one precondition probe."""

    name: str
    ok: bool
    detail: str = ""


@dataclass
class HealthReport:
    """Aggregate of all precondition probes."""

    results: "List[CheckResult]" = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results)

    def format(self) -> str:
        """Human-readable, one line per check plus an aggregate verdict."""
        lines = []
        for r in self.results:
            marker = "OK  " if r.ok else "FAIL"
            line = "[{}] {}".format(marker, r.name)
            if r.detail:
                line += ": " + r.detail
            lines.append(line)
        lines.append("== health-check {} ==".format("PASSED" if self.ok else "FAILED"))
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Default (real) probes — target-touching ones import lazily so this module
# imports cleanly on the host.
# --------------------------------------------------------------------------- #
def _tcp_connect(host: str, port: int, timeout: float) -> None:
    with socket.create_connection((host, port), timeout=timeout):
        pass


def _zed_device_count() -> int:  # pragma: no cover - target-only (pyzed)
    try:
        import pyzed.sl as sl  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("pyzed unavailable (ZED is target-only): {}".format(exc))
    return len(sl.Camera.get_device_list())


def _zmq_bind(endpoint: str) -> None:  # pragma: no cover - exercised via injected stub
    import zmq

    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.PUB)
    try:
        sock.bind(endpoint)
    finally:
        sock.close(0)


def _sqlite_open(store_cfg: "StoreConfig") -> None:
    # Opening the store creates its table if absent — proof the path is writable.
    from overwatch.output.sqlite_store import SqliteEventStore

    SqliteEventStore(store_cfg.path or ":memory:")


# --------------------------------------------------------------------------- #
# Individual checks — each returns a CheckResult and never raises.
# --------------------------------------------------------------------------- #
def check_source(
    src_cfg: object,
    *,
    connect: "Connector" = _tcp_connect,
    zed_probe: "ZedProbe" = _zed_device_count,
    timeout: float = _DEFAULT_TIMEOUT_S,
) -> CheckResult:
    """Probe one configured capture source (RTSP reachability or ZED presence)."""
    source_id = getattr(src_cfg, "source_id", "?")
    name = "source:{}".format(source_id)
    kind = getattr(src_cfg, "type", None)
    try:
        if kind == "rtsp":
            url = getattr(src_cfg, "url", "")
            parsed = urlparse(url)
            host = parsed.hostname
            if not host:
                return CheckResult(name, False, "no host in url: {!r}".format(url))
            port = parsed.port or _DEFAULT_RTSP_PORT
            connect(host, port, timeout)
            return CheckResult(name, True, "rtsp reachable {}:{}".format(host, port))
        if kind == "zed":
            n = zed_probe()
            if n >= 1:
                return CheckResult(name, True, "zed devices: {}".format(n))
            return CheckResult(name, False, "no ZED device detected")
        return CheckResult(name, False, "unknown source type: {!r}".format(kind))
    except Exception as exc:  # noqa: BLE001 - any probe failure is a failed check
        return CheckResult(name, False, str(exc))


def check_engine_files(paths: "List[str]", *, exists: "ExistsFn" = os.path.exists) -> CheckResult:
    """Verify the detector/tracker configs + ReID engine files are present."""
    missing = [p for p in paths if not exists(p)]
    if missing:
        return CheckResult("engines", False, "missing: {}".format(", ".join(missing)))
    return CheckResult("engines", True, "{} file(s) present".format(len(paths)))


def check_bus(bus_cfg: "BusConfig", *, bind: "Binder" = _zmq_bind) -> CheckResult:
    """Verify the bus transport endpoint is reachable/bindable (ZeroMQ)."""
    try:
        if bus_cfg.transport == "zeromq":
            endpoint = bus_cfg.endpoint or ""
            bind(endpoint)
            return CheckResult("bus", True, "zeromq endpoint bindable: {}".format(endpoint))
        # redis transport: URL is a secret resolved on-device; full connect is
        # target-only. Confirm the env var that supplies it is named + present.
        env = bus_cfg.url_env or ""
        if env and os.environ.get(env):
            return CheckResult("bus", True, "redis url present (${}); connect verified on device".format(env))
        return CheckResult("bus", False, "redis url env {!r} unset".format(env))
    except Exception as exc:  # noqa: BLE001
        return CheckResult("bus", False, str(exc))


def check_store(store_cfg: "StoreConfig", *, open_store: "StoreOpener" = _sqlite_open) -> CheckResult:
    """Verify the EventStore durable tier is writable (SQLite)."""
    try:
        if store_cfg.backend == "sqlite":
            open_store(store_cfg)
            return CheckResult("store", True, "sqlite writable: {}".format(store_cfg.path))
        env = store_cfg.url_env or ""
        if env and os.environ.get(env):
            return CheckResult("store", True, "redis url present (${}); verified on device".format(env))
        return CheckResult("store", False, "redis url env {!r} unset".format(env))
    except Exception as exc:  # noqa: BLE001
        return CheckResult("store", False, str(exc))


def run_health_check(
    cfg: "AppConfig",
    *,
    connect: "Connector" = _tcp_connect,
    zed_probe: "ZedProbe" = _zed_device_count,
    bind: "Binder" = _zmq_bind,
    open_store: "StoreOpener" = _sqlite_open,
    exists: "ExistsFn" = os.path.exists,
    timeout: float = _DEFAULT_TIMEOUT_S,
) -> HealthReport:
    """Run every startup-precondition probe and aggregate into a report."""
    results: "List[CheckResult]" = []
    for src in cfg.capture.sources:
        results.append(check_source(src, connect=connect, zed_probe=zed_probe, timeout=timeout))
    engine_paths = [
        cfg.inference.detector_config,
        cfg.inference.tracker_config,
        cfg.inference.reid.engine,
    ]
    results.append(check_engine_files(engine_paths, exists=exists))
    results.append(check_bus(cfg.bus, bind=bind))
    results.append(check_store(cfg.output.store, open_store=open_store))
    return HealthReport(results)


def main(argv: "Optional[List[str]]" = None) -> int:
    """CLI entrypoint: load config, run the health-check, print the report.

    Exit 0 if all preconditions pass, else 1 — usable at boot (systemd
    ``ExecStartPre``) and on demand. See ``scripts/target/55_healthcheck.sh``.
    """
    import argparse

    from overwatch.config.loader import load_config
    from overwatch.observability import configure_logging, log_event

    ap = argparse.ArgumentParser(description="Overwatch startup-precondition health-check (#55)")
    ap.add_argument("--config", default=None, help="config path (default: loader default)")
    ap.add_argument("--plain", action="store_true", help="plain text output (not JSON logs)")
    args = ap.parse_args(argv)

    configure_logging(structured=not args.plain)
    cfg = load_config(args.config) if args.config else load_config()
    report = run_health_check(cfg)
    print(report.format())
    log_event(
        _LOG,
        logging.INFO if report.ok else logging.ERROR,
        "health-check",
        ok=report.ok,
        checks={r.name: r.ok for r in report.results},
    )
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "CheckResult",
    "HealthReport",
    "check_source",
    "check_engine_files",
    "check_bus",
    "check_store",
    "run_health_check",
    "main",
]
