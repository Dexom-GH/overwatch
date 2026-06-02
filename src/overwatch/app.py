"""Application entrypoint — pipeline wiring (placeholder).

Wires the stages together over the message bus: construct the bus (per ADR-0001
once decided), start capture -> inference -> fusion -> output, and run until
stopped. This is a skeleton; the wiring is filled in as stages are implemented.

Run on the TARGET (Jetson) — it pulls in target-only stages (ZED, DeepStream,
TensorRT). On the host, importing ``overwatch.app`` is fine, but ``main()`` will
hit the target-only guards.
"""

from __future__ import annotations


def main() -> None:
    """Construct the bus + stages and run the pipeline.

    TODO:
      1. load_config()
      2. construct the MessageBus (RedisBus/ZeroMqBus once ADR-0001 closes)
      3. start capture (ZedSource) publishing frames + depth
      4. start inference (DeepStreamPipeline + probes for on-demand ReID)
      5. start fusion (DepthFusion, ZoneCounter, HealthMonitor, EventDetector)
      6. start output (SlackAlertSink, EventStore, dashboard)
      7. run until interrupted; tear down cleanly
    """
    raise NotImplementedError("app.main — pipeline wiring pending stage impls")


if __name__ == "__main__":  # pragma: no cover
    main()
